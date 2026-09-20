# Cascade vs. Gemini Conditions — Comparison Report

## Code problems (execution-graded, no judge involved)

- Cascade pass@1: 3/3 (100.0%)
- one_shot pass@1: 2/3 (66.7%)
- blind_iter pass@1: 3/3 (100.0%)
- context_iter pass@1: 3/3 (100.0%)

| Problem | Cascade | one_shot | blind_iter | context_iter | Cascade model tokens | Cascade tokens | one_shot tokens | blind_iter tokens | context_iter tokens |
|---|---|---|---|---|---|---|---|---|---|
| weighted_interval_plan | ✅ | ❌ | ✅ | ✅ | gptoss: 2660, gemma: 2777 | 5437 | 1009 | 4367 | 3448 |
| decode_nested_escapes | ✅ | ✅ | ✅ | ✅ | gptoss: 1221 | 1221 | 815 | 2060 | 2285 |
| circular_minimax_partition | ✅ | ✅ | ✅ | ✅ | gptoss: 2443 | 2443 | 757 | 2251 | 2443 |

> Token columns are per-problem totals. Compare them alongside the pass/fail columns; a condition may improve correctness at additional cost.

## Token totals (whole run)

- Cascade total tokens: 9101
- one_shot total tokens: 2581
- blind_iter total tokens: 8678
- context_iter total tokens: 8176


### Cascade by model

| Model | Calls | Prompt tokens | Completion tokens | Total tokens |
|---|---:|---:|---:|---:|
| gptoss | 3 | 1185 | 5139 | 6324 |
| gemma | 1 | 1320 | 1457 | 2777 |

> Cascade tokens include every model hop. Gemini condition totals include all calls used by that condition.