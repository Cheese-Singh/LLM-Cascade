# LLM Cascade Evaluation

A weak-to-strong model cascade for code generation and evaluation. This repository contains a Python evaluation project that compares a weak-to-strong Ollama model cascade against Gemini; it supports executable code-generation problems and a separate open-ended question workflow.

## What it does

Instead of always calling the most expensive model, the cascade starts with a cheap/fast model and escalates through progressively stronger reviewers **only when needed**:

### Code benchmark

`main.py` runs the original code-generation cascade. The first model writes a
solution, and later models review it using the `AGREE`, `EDIT`, or `REWRITE`
protocol. Each draft is executed against the problem's `check(candidate)` test
suite before the cascade can accept it.

The current cloud-only cascade is:

```text
gpt-oss:20b-cloud -> gemma4:cloud -> nemotron-3-super:cloud -> nemotron-3-ultra:cloud
```

The cascade stops when a review returns `AGREE`, or when it reaches the end of
the chain. Token usage is recorded per problem, per model, and for the whole
run.

**How to read the cascade.** From the caller's side, the cascade is a single system: one prompt in, one final answer out. The AGREE/EDIT/REWRITE hops are an internal escalation policy, not something the caller invokes or manages — in that sense it's no different from treating a single large model's internal routing or depth as opaque. The hop-by-hop trace is disclosed in this repo for inspection and evaluation, not because a user of the system would ever see or drive it directly. The design question this project is asking is: if you're only allowed one opaque call, does letting that call be backed by an internal escalation policy beat a single fixed-size model answering once?

### Open-ended benchmark

Open-ended questions use the same model chain but do not require a Python test
suite. The separate `run_open_ended_cascade.py` script:

1. Reads questions from `open_ended/questions.json`.
2. Has the first model produce an answer.
3. Has the next model review the answer.
4. Stops on `AGREE`, or applies `EDIT`/`REWRITE` and passes the revised answer
   to the next model.
5. Records every hop, verdict, answer, and token count in
   `open_ended/cascade_answers.json`.

Gemini answers are generated separately by `run_open_ended_gemini.py`. The
judge then compares the final Cascade and Gemini answers blindly using
`gpt-oss:120b-cloud` by default. It reports both rubric scores and a fuzzy
preference percentage between the two responses.

## Repository layout

| Path | Purpose |
|---|---|
| `main.py` | Runs the original executable code cascade |
| `cascade_spike.py` | Scores code results and judges open-ended results |
| `all_questions.json` | Code benchmark questions and test suites |
| `spike_results.json` | Generated code cascade results |
| `gemini_conditions.py` | Generates the original code-benchmark Gemini conditions |
| `run_open_ended_cascade.py` | Generates open-ended Cascade answers |
| `run_open_ended_gemini.py` | Generates open-ended Gemini answers |
| `open_ended/questions.json` | Open-ended questions |
| `open_ended/cascade_answers.json` | Generated open-ended Cascade answers |
| `open_ended/gemini_answers.json` | Generated open-ended Gemini answers |
| `report/` | Code benchmark reports |
| `open_ended/report/` | Open-ended comparison reports |

Generated result files can be kept locally or committed as experiment
artifacts. They do not replace the original code benchmark inputs.

## Setup

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For the Ollama cloud models, sign in with Ollama and make sure the models are
available to the account:

```bash
ollama signin
```

For Gemini, create an API key and set it in the shell. Do not commit the key or
place it in a tracked file:

```bash
export GEMINI_API_KEY="your_api_key_here"
```

## Run the code benchmark

From the repository root:

```bash
python3 main.py
```

This reads `all_questions.json` and writes `spike_results.json`.

Generate the code benchmark Gemini conditions:

```bash
python3 gemini_conditions.py \
  --problems-file all_questions.json \
  --condition one_shot \
  --out gemini_one_shot_results.json

python3 gemini_conditions.py \
  --problems-file all_questions.json \
  --cascade-results spike_results.json \
  --condition blind \
  --out gemini_blind_results.json

python3 gemini_conditions.py \
  --problems-file all_questions.json \
  --cascade-results spike_results.json \
  --condition context \
  --out gemini_context_results.json
```

Create the code comparison report:

```bash
python3 cascade_spike.py
```
The report is written to `report/comparison_report.md` and the machine-readable results to `report/comparison_results.json`.

## Results (pilot, n=3)

This is a **small pilot**, not a benchmark — 3 code problems, run once, constrained by API quota rather than by design (see [Limitations](#limitations--next-steps)). The numbers below are a proof of concept for the evaluation methodology and an early signal, not a statistically robust claim about cascade performance in general.

**Important:** The code-question report shown below was generated with the
initial Cascade configuration, before the current cloud-only model names were
introduced. That report used this chain:

```text
gemma -> gpt-oss:120b -> nemotron-3-super -> minimax-m3
```

The results below should therefore be treated as results from that initial
configuration. They are not results from the current chain documented above.

Four conditions are compared, execution-graded (pass/fail against each problem's test suite, no LLM judge involved):

- **Cascade** — the system described above, one opaque call.
- **one_shot** — Gemini 3.6 Flash, single call, no retries.
- **blind_iter** — Gemini 3.6 Flash, allowed to retry on the same problem without seeing the cascade's trace.
- **context_iter** — Gemini 3.6 Flash, allowed to retry with the cascade's hop trace as additional context.

**Pass@1:** Cascade 3/3 (100%) · one_shot 2/3 (66.7%) · blind_iter 3/3 (100%) · context_iter 3/3 (100%)

| Problem | Cascade | one_shot | blind_iter | context_iter | Cascade tokens | one_shot tokens | blind_iter tokens | context_iter tokens |
|---|---|---|---|---|---|---|---|---|
| weighted_interval_plan | ✅ | ❌ | ✅ | ✅ | 17049 | 1009 | 4367 | 3448 |
| decode_nested_escapes | ✅ | ✅ | ✅ | ✅ | 3535 | 815 | 2060 | 2285 |
| circular_minimax_partition | ✅ | ✅ | ✅ | ✅ | 3061 | 757 | 2251 | 2443 |

**Token totals (whole run):** Cascade 23,645 · one_shot 2,581 · blind_iter 8,678 · context_iter 8,176

**Cascade by model:**

| Model | Calls | Total tokens |
|---|---:|---:|
| gemma | 3 | 3,271 |
| gptoss | 3 | 7,953 |
| nemotron | 1 | 12,421 |

**Reading the results honestly:**

- **The cascade beat ungoverned single-shot use (2/3) and matched both iterated-Gemini conditions (3/3 each).** The one problem that separates the conditions, `weighted_interval_plan`, is the clean example: one_shot Gemini got it wrong on its only attempt, while the cascade's internal escalation — and Gemini's *external* retry mechanisms in blind_iter/context_iter — all recovered the correct answer.
- **This means the cascade's actual advantage is over ungoverned single-shot use, not over Gemini with retry logic already built around it.** Once Gemini is given a retry budget (blind or context-aware), it matches the cascade on correctness in this pilot. The interesting claim here is narrower and more honest than "cascade beats Gemini": *a single opaque call to the cascade gets you what an external harness would otherwise need to build (multiple calls, a retry policy, a stopping condition) to get out of a single fixed-size model.*
- **The cascade is not cheaper.** On the hard problem, cascade spent 17,049 tokens — nearly all of it (12,421) in escalating to `nemotron`, the strongest model in the chain — against 3,448–4,367 tokens for Gemini's iterated conditions to reach the same correct answer. Total run cost favors every Gemini condition by 2.7–9x. Any claim about the cascade's value has to rest on something other than raw token cost: not requiring an external caller to design and tune a retry policy, and not depending on any single model's failure mode to trigger that policy.
- **The cascade's cost is plausibly explained by capacity, not compared against it 1:1.** The chain draws on a strictly larger pool of total model capacity than a single Flash-class model call — this is a reasonable explanation for why escalation is expensive when it happens, not a justification that the expense doesn't matter. Model parameter counts across separate sequential calls don't compose into one larger model's capability; they represent independent attempts with a stopping rule.

Net: in this pilot, the cascade's escalation is triggered automatically and without an external harness having to decide when to retry, and it recovers the one case where a single ungoverned call fails — but it does this at meaningfully higher token cost than achieving the same correctness through governed Gemini retries. Both the correctness parity and the cost gap are the finding; neither should be reported without the other.

### 2. Generate Gemini answers

**Code problems** (have a `check` function + `entry_point` in `all_questions.json`): scored by executing each condition's final code against the identical test suite. No LLM judgment is involved in scoring these — it's pass/fail by execution, same as CI.

```bash
python3 run_open_ended_gemini.py \
  --questions-file open_ended/questions.json \
  --output open_ended/gemini_answers.json
```

Each Gemini answer includes prompt, completion, and total token usage.

### 3. Judge Cascade against Gemini

Default judge model: `qwen3.5:397b-cloud` — chosen because it's a different model family/lab than everything else being compared or used in the chain. Example command (you can override `--judge-model`):

```bash
python3 cascade_spike.py \
  --open-ended-problems open_ended/questions.json \
  --open-ended-cascade-results open_ended/cascade_answers.json \
  --open-ended-gemini-results open_ended/gemini_answers.json \
  --open-ended-out-dir open_ended/report \
  --judge-model gpt-oss:120b-cloud
```

This writes:

- `open_ended/report/comparison_report.md`
- `open_ended/report/comparison_results.json`
- `open_ended/report/audit_sample.json`

## Limitations

- **Sample size.** n=3 is a pilot to validate the evaluation pipeline, not a result to generalize from. It's constrained by API quota limits during development, not a deliberate scope choice — a meaningful benchmark needs on the order of 15–30+ problems, ideally spanning difficulty levels, to make the pass-rate and token-delta numbers statistically meaningful rather than anecdotal.
- **Reducing cascade token cost while preserving correctness is an explicit future goal, not yet attempted.** This pilot establishes the correctness case (parity with governed Gemini retries, a win over ungoverned single-shot use); it does not yet establish cost-competitiveness. Next steps include tighter per-hop prompts, early-exit heuristics so weaker models can decline to escalate with more precision, and cheaper review passes for hops that historically AGREE quickly. The goal is to keep the 3/3 correctness result while closing the token gap toward Gemini's iterated conditions, not just documenting the gap as-is.
- **No open-ended problems yet.** The current problem set is 100% code generation. The judge pipeline is built and tested but has no real data to report on — adding open-ended reasoning/explanation questions is the natural next step, and would need the manual audit-sample step actually run and its agreement rate reported.
- **Cascade token cost isn't fully apples-to-apples.** Cascade tokens reflect every hop across the chain (up to 4 model calls); Gemini conditions reflect 1 or more calls depending on condition. This is disclosed rather than normalized away, since collapsing it into one ratio would hide exactly the overhead-vs-parity tradeoff that's the actual finding here.
- **Single run, fixed temperature settings.** No repeated-sampling variance analysis yet — a single pass@1 number per problem could shift on a re-run given non-zero temperature.
- **Free-tier model availability.** Both the cascade's cloud models and the suggested judge model depend on Ollama's free-tier catalog, which changes over time. `--judge-model` is a CLI flag specifically so this doesn't require a code change if a model's access changes.
