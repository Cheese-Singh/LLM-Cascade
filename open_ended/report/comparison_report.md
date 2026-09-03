# Cascade vs. Gemini Conditions — Comparison Report

## Open-ended problems (blinded LLM-judge scored)

- Judge model: gpt-oss:120b-cloud (not part of either system under test)
- Cascade wins: 2/3  |  Gemini wins: 1/3  |  Ties: 0/3

| Problem | Cascade score | Gemini score | Preference | Winner |
|---|---:|---:|---:|---|
| binary_search_explained | 5/5 | 5/5 | Gemini +55% | Gemini |
| inflation_tradeoff | 5/5 | 4/5 | Cascade +65% | Cascade |
| learn_new_tech | 5/5 | 5/5 | Cascade +55% | Cascade |

> Judging is blinded (responses labeled A/B, order randomized per question) and rubric-based (correctness/completeness/clarity), with a fuzzy percentage preference between the two responses. See raw_judge_response in the JSON output for the full judge text.

## Token totals (whole run)

- Cascade total tokens: 0
- judge_only total tokens: 2799


### Cascade by model

| Model | Calls | Prompt tokens | Completion tokens | Total tokens |
|---|---:|---:|---:|---:|
| gptoss | 3 | 476 | 1959 | 2435 |
| gemma | 3 | 2405 | 1790 | 4195 |

> Cascade tokens include every model hop. Gemini condition totals include all calls used by that condition.