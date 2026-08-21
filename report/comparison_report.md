# Cascade vs. Gemini 3.6 Flash — Comparison Report

## Code problems (execution-graded, no judge involved)

- Cascade pass@1: 4/4 (100.0%)
- Gemini 3.6 Flash pass@1: 3/4 (75.0%)

| Problem | Cascade | Gemini | Cascade tokens | Gemini tokens | Token delta |
|---|---|---|---|---|---|
| minimal_bracket_rebalance | ✅ | ✅ | 6120 | 1609 | +4511 |
| collapse_runs | ✅ | ❌ | 1793 | 1123 | +670 |
| merge_touching_intervals | ✅ | ✅ | 1794 | 2044 | +-250 |
| first_mismatch_index | ✅ | ✅ | 5489 | 2256 | +3233 |

> Token delta = cascade tokens minus Gemini tokens for that problem (positive = cascade spent more). Read this next to the pass/fail columns — a positive delta on a problem cascade got right and Gemini got wrong is the actual 'spent more, got it right' evidence; a positive delta where both passed is pure overhead worth noting as a limitation.

## Token totals (whole run)

- Cascade total tokens: 15196
- Gemini total tokens: 7032

> Cascade tokens reflect every hop across the chain (gemma → nemotron → gptoss → minimax as needed); Gemini tokens reflect a single call per question. These are not directly divided into a single 'tokens per point of accuracy' ratio here because the two systems' unit of work differs — report both totals and let the pass-rate / win-rate tables above carry the quality comparison.