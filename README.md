# LLM Cascade Evaluation

A Python evaluation project for comparing a weak-to-strong Ollama model cascade
against Gemini. It supports both executable code-generation problems and a
separate open-ended question workflow.

## What it does

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

The report is written to `report/comparison_report.md` and the machine-readable
results to `report/comparison_results.json`.

## Run the open-ended benchmark

### 1. Generate Cascade answers

```bash
python3 run_open_ended_cascade.py \
  --questions-file open_ended/questions.json \
  --output open_ended/cascade_answers.json
```

For a quick connectivity test using only the first model:

```bash
python3 run_open_ended_cascade.py --single-model
```

The normal run prints the active question, each model hop, each `AGREE`/
`EDIT`/`REWRITE` verdict, tokens for every call, tokens by model, and the final
answer.

### 2. Generate Gemini answers

```bash
python3 run_open_ended_gemini.py \
  --questions-file open_ended/questions.json \
  --output open_ended/gemini_answers.json
```

Each Gemini answer includes prompt, completion, and total token usage.

### 3. Judge Cascade against Gemini

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

The open-ended report includes per-question Cascade and Gemini rubric scores,
winner, fuzzy preference percentage, Cascade token totals, and Gemini token
totals.

## Open-ended question format

`open_ended/questions.json` is a JSON list. Each question needs an `id` and a
`prompt`; `type` is optional but recommended:

```json
[
  {
    "id": "binary_search_explained",
    "type": "open_ended",
    "prompt": "Explain in plain language why binary search works."
  }
]
```

## Judging methodology

The open-ended judge sees two responses labeled `A` and `B`; it does not see
which one came from Cascade or Gemini, and the label assignment is randomized
per question. It scores each response on:

- correctness: 0-2
- completeness: 0-2
- clarity: 0-1

It also returns a fuzzy preference split such as `A: 55%` and `B: 45%`.
The report maps those percentages back to Cascade and Gemini after the blinded
judgment. A difference of two percentage points or less is reported as a tie.

The judge model must be independent of the systems being compared. The default
is `gpt-oss:120b-cloud`; it is not used in the Cascade chain.

## Validation

Check Python syntax before running a long cloud evaluation:

```bash
python3 -m py_compile main.py cascade_spike.py \
  run_open_ended_cascade.py run_open_ended_gemini.py
```

Cloud model response time depends on Ollama availability, account access, and
model load. The open-ended cascade may use fewer than four model calls when a
review returns `AGREE`.

## Limitations

- Cloud model availability and pricing/access rules can change.
- A single run is not a statistically robust benchmark.
- Open-ended judge scores are an automated signal and should be checked against
  the generated `audit_sample.json`.
- Token counts are reported as returned by the Ollama and Gemini APIs; they are
  not necessarily directly comparable across providers.
