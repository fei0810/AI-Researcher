from openai import OpenAI
from utils import call_api
import argparse
import json
import os
from lit_review_tools import parse_and_execute, format_papers_for_printing, print_top_papers_from_paper_bank, dedup_paper_bank
from utils import cache_output
from tqdm import tqdm
import random 
import retry
random.seed(2024)

def paper_query(idea, topic_description, openai_client, model, seed):
    prompt = "You are a professor in Natural Language Processing. You need to evaluate the novelty of a proposed research idea.\n"

    prompt += "The idea is:\n" + idea + "\n\n"
    prompt += "You want to do a round of paper search in order to find out whether the proposed project has already been done. "
    prompt += "You should propose some keywords for using the Semantic Scholar API to find the most relevant papers to this proposed idea. Formulate your query as: KeywordQuery(\"keyword\"). Give me 1 - 3 queries, the keyword can be a concatenation of multiple keywords (just put a space between every word) but please be concise and try to cover all the main aspects.\n"
    prompt += "The query keywords should be specific to the proposed research idea, in order to find whether there are similar ideas in the literature. try to include language model to find relevant papers within NLP. "
    prompt += "Your query (just return the queries with no additional text, put each one in a new line without any other explanation):"
    prompt_messages = [{"role": "user", "content": prompt}]
    response, cost = call_api(openai_client, model, prompt_messages, temperature=0., max_tokens=100, seed=seed, json_output=False)
    
    return prompt, response, cost

def paper_scoring(paper_lst, idea, topic_description, openai_client, model, seed):
    ## use gpt4 to score each paper 
    prompt = "You are a research assistant whose job is to read the below set of papers and score each paper based on how similar the paper is to the proposed idea.\n"
    prompt += "The proposed idea is: " + idea.strip() + ".\n"
    prompt += "The topic is " + topic_description.strip() + " and it should be related to large language models and NLP broadly.\n"
    prompt += "The papers are:\n" + format_papers_for_printing(paper_lst) + "\n"
    prompt += "Please score each paper from 1 to 10 based on the similarity and relevance to the proposed idea. 10 means the paper is essentially the same as the proposed idea; 1 means the paper is not even relevant to the topic; 5 means the paper shares some similarity but some key details are different.\n"
    prompt += "Write the response in JSON format with \"paperID: score\" as the key and value for each paper.\n"
    
    prompt_messages = [{"role": "user", "content": prompt}]
    response, cost = call_api(openai_client, model, prompt_messages, temperature=0., max_tokens=4000, seed=seed, json_output=True)
    return prompt, response, cost

def self_improve(experiment_plan, paper_bank, openai_client, model, seed):
    ## use gpt4 to improve the original experiment plan with the new set of retrieved papers 
    prompt = "You are a professor specialized in Natural Language Processing. You have a research project proposal but you have received some criticisms that it is not clearly contextualized in related works.\n"
    prompt += "The project proposal is:\n" + experiment_plan.strip() + ".\n"
    prompt += "The set of potentially related papers is:\n" + format_papers_for_printing(paper_bank[:5]) + "\n"
    prompt += "Now edit the problem statement section. First explain clearly what is the proposed method or the proposed research problem to analyze. Then clearly explain how the proposed project connects to prior works, for example, is any prior work a motivation for this project? Explain the similaries and differences and explain why this proposed project is important (e.g., are there any limitations, gaps, or open problems from prior works). Specify the type of contribution that the project is trying to make (e.g., proposing a new method to address limitations of prior work, exploring a new question or phenomenon that prior works did not analyze, applying prior methods on a new problem, etc.).\n"
    prompt += "You should preserve the original experiment plan as much as possible. But if you added additional ideas in the problem statement, then you should incoporate them in the experiment plan accordingly. You should delete any details from the original experiment plan, and are only allowed to add additional details to match the updated problem statement when necessary.\n"
    prompt += "You do not have to mention all the provided papers in the problem statement, just the most important one or two. Return the results in json format, where the keys should be: Title, Problem Statement, Step-by-Step Experiment Plan (the entire experiment plan should be the value, store as a string, not dictionary), and Fallback Plan (only needed for method papers, just copy if the given proposal already has one). Strip all other unnecessary sections.\n"
    prompt += "Directly give me the final improved project proposal.\n"
    
    prompt_messages = [{"role": "user", "content": prompt}]
    response, cost = call_api(openai_client, model, prompt_messages, temperature=0., max_tokens=4000, seed=seed, json_output=True)
    return prompt, response, cost


@retry.retry(tries=3, delay=2)
def get_related_works(idea_name, idea, topic_description, openai_client, model, seed):
    paper_bank = {}
    total_cost = 0
    all_queries = []

    ## get KeywordSearch queries
    _, queries, cost = paper_query(idea, topic_description, openai_client, model, seed)
    total_cost += cost
    # print ("queries: \n", queries)
    all_queries = queries.strip().split("\n")
    ## also add the idea name as an additional query
    all_queries.append("KeywordQuery(\"{}\")".format(idea_name + " NLP"))

    for query in all_queries:
        print ("current query: ", query.strip())
        paper_lst = parse_and_execute(query.strip())
        if paper_lst is None:
            continue
        paper_bank.update({paper["paperId"]: paper for paper in paper_lst})

        ## score each paper
        prompt, response, cost = paper_scoring(paper_lst, idea, topic_description, openai_client, model, seed)
        total_cost += cost
        response = json.loads(response.strip())

        ## initialize all scores to 0 then fill in gpt4 scores
        for k,v in response.items():
            if k in paper_bank:
                paper_bank[k]["score"] = v
        
        ## the missing papers will have a score of 0 
        for k,v in paper_bank.items():
            if "score" not in v:
                v["score"] = 0
            
        # print (paper_bank)
        # print_top_papers_from_paper_bank(paper_bank, top_k=10)
        # print ("-----------------------------------\n")
    
    ## the missing papers will have a score of 0 
    for k,v in paper_bank.items():
        if "score" not in v:
            v["score"] = 0
    
    ## rank all papers by score
    data_list = [{'id': id, **info} for id, info in paper_bank.items()]
    sorted_papers = sorted(data_list, key=lambda x: x['score'], reverse=True)
    sorted_papers = dedup_paper_bank(sorted_papers)

    return sorted_papers, total_cost, all_queries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=str, default='gpt-4-1106-preview', help='api engine; https://openai.com/api/')
    parser.add_argument('--cache_name', type=str, default=None, required=True, help='cache file name for the retrieved papers')
    parser.add_argument('--idea_name', type=str, default=None, required=True, help='the specific idea to be formulated into an experiment plan')
    parser.add_argument('--load_papers_from_cache', type=bool, default=False, help='whether to load the retrieved papers from cache')
    parser.add_argument('--seed', type=int, default=2024, help="seed for GPT-4 generation")
    args = parser.parse_args()

    with open("keys.json", "r") as f:
        keys = json.load(f)

    OAI_KEY = keys["api_key"]
    ORG_ID = keys["organization_id"]
    S2_KEY = keys["s2_key"]
    openai_client = OpenAI(
        organization=ORG_ID,
        api_key=OAI_KEY
    )

    if args.idea_name == "all":
        filenames = os.listdir("cache_results/experiment_plans/"+args.cache_name)
    else:
        filenames = ["_".join(args.idea_name.lower().split())+".json"]

    for filename in tqdm(filenames):
        print ("working on: ", filename)
        ## load the idea
        cache_file = os.path.join("cache_results/experiment_plans/"+args.cache_name, filename)
        with open(cache_file, "r") as f:
            ideas = json.load(f)

        topic_description = ideas["topic_description"]
        idea = ideas["improved_plan"]
        idea_name = ideas["idea_name"]

        if args.load_papers_from_cache:
            with open("cache_results/novelty_check/"+args.cache_name+"_"+"_".join(args.idea_name.lower().split())+".json", "r") as f:
                output_dict = json.load(f)
            paper_bank = output_dict["paper_bank"]
        else:
            print ("Retrieving related works...")
            paper_bank, total_cost, all_queries = get_related_works(idea_name, idea, topic_description, openai_client, args.engine, args.seed)
            output = format_papers_for_printing(paper_bank[ : 10])
            print ("Top 10 papers: ")
            print (output)
            print ("Total cost: ", total_cost)

            ## save the paper bank
            if not os.path.exists("cache_results/novelty_check"):
                os.makedirs("cache_results/novelty_check")
            ideas["novelty_check_queries"] = all_queries
            ideas["novelty_check_papers"] = paper_bank

        ## use gpt4 to improve the original experiment plan with the new set of retrieved papers
        print ("Improving the original experiment plan with the new set of retrieved papers...")
        prompt, response, cost = self_improve(idea, paper_bank, openai_client, args.engine, args.seed)
        # print (prompt + "\n")
        # print (response + "\n")
        # print (cost)

        ## cache the improved experiment plan
        final_plan_json = json.loads(response.strip())
        ideas["final_plan_json"] = final_plan_json
        cache_output(ideas, cache_file)

    

    
    