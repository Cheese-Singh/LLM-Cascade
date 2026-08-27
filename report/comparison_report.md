# Cascade vs. Gemini Conditions — Comparison Report

## Code problems (execution-graded, no judge involved)

- Cascade pass@1: 3/3 (100.0%)
- one_shot pass@1: 2/3 (66.7%)
- blind_iter pass@1: 3/3 (100.0%)
- context_iter pass@1: 3/3 (100.0%)

| Problem | Cascade | one_shot | blind_iter | context_iter | Cascade model tokens | Cascade tokens | one_shot tokens | blind_iter tokens | context_iter tokens |
|---|---|---|---|---|---|---|---|---|---|
| weighted_interval_plan | ✅ | ❌ | ✅ | ✅ | gemma: 1493, gptoss: 3135, nemotron: 12421 | 17049 | 1009 | 4367 | 3448 |
| decode_nested_escapes | ✅ | ✅ | ✅ | ✅ | gemma: 854, gptoss: 2681 | 3535 | 815 | 2060 | 2285 |
| circular_minimax_partition | ✅ | ✅ | ✅ | ✅ | gemma: 924, gptoss: 2137 | 3061 | 757 | 2251 | 2443 |

> Token columns are per-problem totals. Compare them alongside the pass/fail columns; a condition may improve correctness at additional cost.

## Token totals (whole run)

- Cascade total tokens: 23645
- one_shot total tokens: 2581
- blind_iter total tokens: 8678
- context_iter total tokens: 8176


### Cascade by model

| Model | Calls | Prompt tokens | Completion tokens | Total tokens |
|---|---:|---:|---:|---:|
| gemma | 3 | 1076 | 2195 | 3271 |
| gptoss | 3 | 2990 | 4963 | 7953 |
| nemotron | 1 | 1093 | 11328 | 12421 |

> Cascade tokens include every model hop. Gemini condition totals include all calls used by that condition.