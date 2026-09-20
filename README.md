# LLM-Cascade

A research harness for studying **Minimum Sufficient Inference**: how much LLM inference is actually necessary to produce a reliable answer, and whether objective evidence can determine when further inference is unnecessary.

The central research question is:

> Can objective, evidence-aware escalation preserve correctness while spending less inference than always using the strongest model?

## Core idea

Instead of sending every problem through a fixed sequence of increasingly strong models, the system spends additional inference only when cheap, objective evidence indicates that the current answer is insufficient.

For executable code problems:

```text
Problem
   ↓
L1: gpt-oss-20b
   ↓ execute against tests
   ├── PASS → STOP
   └── FAIL
        ↓
     L2: gemma4
        ↓ execute against tests
        ├── PASS → STOP
        └── FAIL
             ↓
          L3: nemotron-3-super
             ↓ execute against tests
             ├── PASS → STOP
             └── FAIL
                  ↓
               L4: nemotron-3-ultra
                  ↓ execute against tests
                  STOP
```

Layers after L1 receive the original problem, the previous candidate, and the execution failure, so they repair the failed solution rather than starting blind.

The key principle is:

> Do not use a stronger LLM to verify something that can be established more cheaply and objectively.

The cascade is the experimental mechanism for studying this question. It is not itself the contribution.

## Model ladder

| Layer | Model                    |
| ----- | ------------------------ |
| L1    | `gpt-oss:20b-cloud`      |
| L2    | `gemma4:cloud`           |
| L3    | `nemotron-3-super:cloud` |
| L4    | `nemotron-3-ultra:cloud` |

Models are served through Ollama. Layer names and identifiers live in `config.py`.

## Phase I: baselines (complete)

Phase I evaluates whether execution evidence can determine when escalation is necessary, on 100 code-generation problems with deterministic checks.

Systems:

```text
layer_1_only     Problem → L1
layer_4_only     Problem → L4
fixed_cascade    Problem → L1 → L2 → L3 → L4, always
execution_gated  Problem → L1 → execute → stop on PASS, else escalate
```

`layer_1_only` and `execution_gated` have been run to completion. `layer_4_only` and `fixed_cascade` are implemented but not yet run; they are planned for a sampled run.

### Phase I results

On the corrected 100-problem benchmark:

|                     | Layer 1 only | Execution-gated |
| ------------------- | -----------: | --------------: |
| Accuracy            |        90.0% |          100.0% |
| Mean tokens/query   |       692.59 |          807.64 |
| Median tokens/query |       548.50 |          581.00 |
| Escalation rate     |          n/a |              8% |
| Reached L3 / L4     |          n/a |         0% / 0% |

Under the execution gate, 92% of problems were solved by L1 alone and the remaining 8% were solved by L2. No problem required L3 or L4.

The execution-gated system closed the observed 10-point accuracy gap relative to L1-only, escalating on 8% of problems; every escalation was resolved at L2.

**Read these numbers with the following caveats:**

* **Oracle gate.** The same deterministic tests both trigger escalation and score the result. This is an oracle-verified setting: the gate has access to the grading signal, so it measures how much inference is needed when that signal is available at inference time, and gives an upper-bound reference for what a deployed router could achieve.
* **Single run and model nondeterminism.** Cloud-hosted models are not bit-deterministic even at temperature 0 with a fixed seed. Repeated identical requests produced different code and, on some problems, different pass/fail outcomes. The reported 90% is therefore a point estimate from this 100-problem evaluation.
* **Merged provenance.** Results combine targeted reruns of problems whose checks were corrected with earlier runs for the remainder. Records were merged by problem ID, keeping the newest. Checks for problems that were not rerun were reviewed by hand, not mechanically verified.
* **Latency.** Per-query latency is reported. Total experiment runtime is not, because runs were interrupted and resumed. Latency for problems that needed retries is inflated.
* **Benchmark corrections.** Several checks in the original benchmark were defective and were corrected during Phase I. Results before the corrections are not comparable.

## Evaluation

Each problem records correctness, token usage, latency, and escalation behavior. Records are appended to `stream_<system>.jsonl` as each problem finishes, so an interrupted run can be resumed without losing completed records.

Primary metrics:

* Accuracy and error rate
* Mean and median tokens per query
* Tokens per correct answer
* Mean, median, P50 and P95 latency
* Escalation rate and per-layer invocation rates
* Error recovery rate

The main analysis is a quality–cost–latency tradeoff, not a single accuracy score.

## Phase II: counterfactual analysis (planned)

Run all four layers independently on every problem, without adaptive escalation:

```text
same problem
├── L1 → execute → correct?
├── L2 → execute → correct?
├── L3 → execute → correct?
└── L4 → execute → correct?
```

This yields the **minimum sufficient layer** for each problem:

```text
L1 ✓ L2 ✓ L3 ✓ L4 ✓   → minimum sufficient layer = 1
L1 ✗ L2 ✓ L3 ✓ L4 ✓   → minimum sufficient layer = 2
L1 ✗ L2 ✗ L3 ✓ L4 ✓   → minimum sufficient layer = 3
L1 ✗ L2 ✗ L3 ✗ L4 ✓   → minimum sufficient layer = 4
all ✗                  → no sufficient layer
```

Because this is a counterfactual evaluation, all four layers must be evaluated independently for each problem. This increases cost but is necessary to determine what each model would have produced and therefore identify the minimum sufficient layer.

Goals:

* Determine how much inference each problem actually required.
* Analyze where and why L1 fails and what additional inference contributes.
* Establish the quality–token–latency tradeoff per layer.
* Estimate per-layer pass rates over repeated runs, since single pass/fail outcomes can be unstable on borderline problems.

Each counterfactual record is intended to store:

```text
problem_id
layer_1_correct ... layer_4_correct
layer_1_tokens  ... layer_4_tokens
layer_1_latency ... layer_4_latency
minimum_sufficient_layer
failure_type_per_layer
```

## Phase III: smarter routing (planned)

Move beyond simple execution PASS/FAIL. Investigate whether the need for escalation can be predicted from evidence available after an attempt, without access to the stronger model's output.

Candidate signals:

```text
tests passed / failed, number of failures
syntax validity, runtime failure, error type
execution time
code length, AST characteristics
uncertainty or confidence signals
problem difficulty
historical model reliability
cost and latency of further inference
```

Routers to compare:

```text
never escalate
always escalate
fixed cascade
execution-gated
heuristic router
learned router
```

Goal: match the reliability of the execution-gated ladder with less unnecessary escalation.

## Phase IV: generalization (planned)

Apply the Minimum Sufficient Inference framework beyond code:

| Domain           | Verification                              |
| ---------------- | ----------------------------------------- |
| Mathematics      | exact, symbolic or numerical verification |
| Factual QA       | trusted or deterministic references       |
| Reasoning        | task-specific verification                |
| Open-ended tasks | blinded LLM judge with explicit rubric    |

Code remains the reference domain because it has cheap, objective verification.

## Repository structure

```text
LLM-Cascade/
├── benchmarks/
│   ├── code_subset.json
│   └── code_100.json
├── results/
├── traces/
├── main.py
├── run_baselines.py
├── run_counterfactual_collection.py
├── merge_results.py
├── sandbox.py
├── trace_logger.py
├── metrics.py
├── evaluation.py
├── config.py
├── cascade_spike.py
├── run_open_ended_cascade.py
├── run_open_ended_gemini.py
├── gemini_conditions.py
├── all_questions.json
└── requirements.txt
```

## Running the harness

Run a single system on the 100-problem benchmark:

```bash
python main.py --mode layer_1_only --problems-file benchmarks/code_100.json --temperature 0 --seed 42 --output-dir results/phase1_100
python main.py --mode execution_gated --problems-file benchmarks/code_100.json --temperature 0 --seed 42 --output-dir results/phase1_100_gated
```

Live status is shown by default. Use `--quiet` to suppress it.

Limit or select problems:

```bash
python main.py --mode execution_gated --limit 10
python main.py --mode execution_gated --problem-ids two_sum_indices,three_sum_zero
```

Resume an interrupted run. Completed problems are read from the stream file and skipped:

```bash
python main.py --mode execution_gated --problems-file benchmarks/code_100.json --temperature 0 --seed 42 --output-dir results/phase1_100_gated --resume
```

Use the same output directory, temperature and seed when resuming. Each run should use its own output directory.

Merge stream records into a corrected summary, keeping the newest record per problem:

```bash
python merge_results.py results/phase1_100/stream_layer_1_only.jsonl layer_1_only results/phase1_100 merged_corrected
```

## Reliability notes

* **Timeouts and retries.** Each layer has its own request timeout and attempt limit, configured in `run_baselines.py`. Slower models get longer timeouts.
* **Failed calls hand off.** If a layer exhausts its attempts, the hop is recorded with a `model_error` and the cascade escalates instead of aborting the run. Filter on `model_error` to separate infrastructure failures from wrong answers.
* **Sandboxed execution.** Candidate code runs in an isolated process with time, memory and output limits. Correctness is decided by execution, not by an LLM.
* **Benchmark integrity.** Checks are hand-written and were found to contain errors. Treat any minimum-sufficient-layer label as only as reliable as its check. A mechanical audit, such as running reference solutions against each check, is planned before Phase II.
* **Benchmark version.** `benchmarks/code_100.json` SHA-256: `<paste hash>`. Results are only comparable against this version of the checks.

## Research framing

> Can we identify the minimum amount of LLM inference necessary for reliable task completion, and dynamically stop inference once sufficient evidence exists?

The contribution is not "a four-model cascade." It is a study of whether objective evidence can determine the minimum inference needed, and whether adaptive, evidence-aware escalation can reduce unnecessary inference. The cascade, routing mechanism, objective verification, counterfactual evaluation and cost/quality measurements are the tools for investigating that question.

The project began as a conventional weak-to-strong cascade benchmarked against strong general-purpose models, and was reframed around Minimum Sufficient Inference as the experiments matured.
