## example usage
python3 src/lit_review.py \
    --engine "claude-3-5-sonnet-20240620" \
    --mode "topic" \
    --topic_description "multi-cancer early detection using cfDNA fragmention and methylation" \
    --cache_name "../cache_results_test/lit_review/factuality_prompting_method.json" \
    --max_paper_bank_size 30 \
    --print_all
