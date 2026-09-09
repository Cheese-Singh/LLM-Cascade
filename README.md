# LLM Cascade Evaluation

A weak-to-strong model cascade for code generation and evaluation. This repository contains a Python evaluation project that compares an adaptive Ollama model cascade against Gemini; it supports executable code-generation problems and a separate open-ended question workflow.

The cascade uses **different escalation signals depending on the task**:

* **Code / objectively verifiable tasks:** generate → execute the verifier → stop on **PASS**, escalate on **FAIL**.
* **Open-ended tasks:** generate → have the next model review → stop on **AGREE**, escalate on **EDIT/REWRITE**.

In both cases, the caller makes a single cascade request; the internal system decides whether escalation is necessary.

## What it does

Instead of always calling the strongest or most expensive model, the cascade starts with a weaker, cheaper model and escalates only when the current model fails the task-specific evaluation signal.

The configured maximum chain is:

```text
gpt-oss:20b-cloud
        ↓
gemma4:cloud
        ↓
nemotron-3-super:cloud
        ↓
nemotron-3-ultra:cloud
```

The four-model chain is the **maximum escalation path**, not a requirement that every problem invokes all four models.

### Code benchmark

For objectively verifiable programming problems, the cascade is **verifier-gated** rather than review-gated.

1. The first model generates an implementation.
2. The implementation is executed against the problem's deterministic verifier.
3. If it **passes**, the cascade stops immediately.
4. If it **fails**, the next model receives the original specification, the previous implementation, and the exact verifier error.
5. The next model attempts to fix the implementation.
6. This continues until a solution passes or the model chain is exhausted.

There is no unnecessary LLM `AGREE`/`EDIT`/`REWRITE` review call when a deterministic verifier can directly establish correctness.

### Open-ended benchmark

For open-ended questions where there is no deterministic test suite, the cascade uses model-based review.

1. The first model generates an answer.
2. The next model reviews the answer.
3. `AGREE` stops the cascade.
4. `EDIT` or `REWRITE` causes the answer to continue through the cascade.

This gives the project two distinct escalation mechanisms:

```text
Code:
Generate → Execute → PASS / FAIL → Escalate on FAIL

Open-ended:
Generate → Review → AGREE / EDIT / REWRITE → Escalate when needed
```

## Current model chain

```text
gpt-oss:20b-cloud
    ↓
gemma4:cloud
    ↓
nemotron-3-super:cloud
    ↓
nemotron-3-ultra:cloud
```

The ordering is intentionally weak-to-strong. A problem that succeeds at an earlier stage does not invoke later models.

## How to read the cascade

From the outside, the system can be treated as a single opaque call:

```text
Input problem
     ↓
┌──────────────────────────┐
│      Cascade system      │
│                          │
│  generate → evaluate     │
│       ↓                  │
│  escalate if necessary  │
│       ↓                  │
│  return final answer     │
└──────────────────────────┘
     ↓
Final answer
```

The caller does not need to implement its own retry loop or decide which model should be called next. That policy is internal to the cascade.

This leads to the central research question:

> **If you're only allowed one opaque call, can that call be backed by an adaptive weak-to-strong escalation policy to achieve the benefits of iterative inference without requiring the caller to implement the retry policy itself?**

## Open-ended evaluation

The repository also contains an open-ended evaluation workflow for questions that cannot be checked with deterministic execution.

Unlike the code benchmark, these problems rely on model-based evaluation. A model generates an answer, and subsequent models assess or modify it using the `AGREE` / `EDIT` / `REWRITE` protocol.

This provides a complementary evaluation setting for testing whether the weak-to-strong cascade can improve answers when objective verification is unavailable.

## Repository layout

| Path                        | Purpose                                               |
| --------------------------- | ----------------------------------------------------- |
| `main.py`                   | Verifier-gated code-generation cascade                |
| `cascade_spike.py`          | Scores code results and evaluates open-ended results  |
| `run_open_ended_cascade.py` | Runs the open-ended cascade                           |
| `run_open_ended_gemini.py`  | Generates Gemini answers for the open-ended benchmark |
| `gemini_conditions.py`      | Gemini evaluation conditions                          |
| `all_questions.json`        | Code benchmark problems                               |
| `open_ended/`               | Open-ended benchmark data and outputs                 |
| `report/`                   | Evaluation reports and analysis                       |
| `requirements.txt`          | Python dependencies                                   |

## Setup

Install the required Python dependencies:

```bash
pip install -r requirements.txt
```

The code benchmark uses Ollama models. Make sure the required models are available through your configured Ollama environment.

## Run the code benchmark

Run the main cascade:

```bash
python3 main.py
```

The cascade will execute each generated solution against its verifier and escalate only when verification fails.

Token usage is recorded per model and per problem so that the cost of adaptive escalation can be compared with alternative inference strategies.

## Results

The current code benchmark uses the following cascade:

```text
gpt-oss:20b-cloud
        ↓
gemma4:cloud
        ↓
nemotron-3-super:cloud
        ↓
nemotron-3-ultra:cloud
```

The reported pilot contains **3 execution-graded programming problems**.

### Verifier-gating vs. the previous review-gated cascade

The verifier-gating change produced a substantial reduction in cascade token usage on the same three problems.

| Cascade version                |       Total tokens |
| ------------------------------ | -----------------: |
| Previous review-gated cascade  |             23,645 |
| Current verifier-gated cascade |              9,101 |
| **Reduction**                  | **14,544 (61.5%)** |

The previous cascade used review calls between model stages, even for objectively verifiable code-generation problems. The current implementation instead executes each candidate directly and escalates only when the verifier reports failure.

This reduced the token cost of the cascade by approximately **61%** on the same pilot while retaining **3/3 execution-graded correctness**.

### Execution-graded accuracy

| Condition                |       Pass@1 |
| ------------------------ | -----------: |
| Cascade                  | 3/3 (100.0%) |
| Gemini one-shot          |  2/3 (66.7%) |
| Gemini blind iteration   | 3/3 (100.0%) |
| Gemini context iteration | 3/3 (100.0%) |

### Token usage by problem

| Problem                      |   Cascade |  One-shot | Blind iteration | Context iteration |
| ---------------------------- | --------: | --------: | --------------: | ----------------: |
| `weighted_interval_plan`     |     5,437 |     1,009 |           4,367 |             3,448 |
| `decode_nested_escapes`      |     1,221 |       815 |           2,060 |             2,285 |
| `circular_minimax_partition` |     2,443 |       757 |           2,251 |             2,443 |
| **Total**                    | **9,101** | **2,581** |       **8,678** |         **8,176** |

### Cascade execution trace

The interesting part of the cascade is that not every problem reaches every model.

| Problem                      | Execution path      | Result                |
| ---------------------------- | ------------------- | --------------------- |
| `weighted_interval_plan`     | `gpt-oss` → `gemma` | PASS after escalation |
| `decode_nested_escapes`      | `gpt-oss`           | PASS immediately      |
| `circular_minimax_partition` | `gpt-oss`           | PASS immediately      |

Thus, in this run:

* `gpt-oss:20b-cloud` handled all 3 initial attempts.
* `gemma4:cloud` was invoked only once.
* `nemotron-3-super:cloud` was never required.
* `nemotron-3-ultra:cloud` was never required.

### Cascade token breakdown

| Model                    | Calls | Prompt tokens | Completion tokens |     Total |
| ------------------------ | ----: | ------------: | ----------------: | --------: |
| `gpt-oss:20b-cloud`      |     3 |         1,185 |             5,139 |     6,324 |
| `gemma4:cloud`           |     1 |         1,320 |             1,457 |     2,777 |
| `nemotron-3-super:cloud` |     0 |             0 |                 0 |         0 |
| `nemotron-3-ultra:cloud` |     0 |             0 |                 0 |         0 |
| **Total**                | **4** |     **2,505** |         **6,596** | **9,101** |

### How to interpret the pilot

The current result is most interesting as a comparison between **single-shot inference**, **externally managed iterative inference**, and **internally managed adaptive escalation**.

* The cascade achieved **3/3 correctness**, while Gemini one-shot achieved **2/3**.
* The cascade matched both Gemini iterative conditions at **3/3**.
* The cascade recovered the `weighted_interval_plan` problem through escalation after the initial model failed verification.
* Replacing the previous review-gated code cascade with verifier-gating reduced cascade token usage from **23,645 to 9,101**, a **61.5% reduction**, on the same three problems.
* The current cascade still used approximately **3.5×** the tokens of Gemini one-shot.
* Its token usage was only about **4.9% higher than blind iteration** and **11.3% higher than context iteration**.
* Two of the three problems stopped at the first model, demonstrating that later models are not automatically invoked.
* The four-model configuration therefore represents available escalation capacity rather than mandatory computation.

The pilot does **not** establish that the cascade is more accurate than externally managed iterative inference. Its more precise result is:

> **Automatic verifier-gated escalation achieved the same 3/3 correctness as the tested Gemini retry strategies, while outperforming the ungoverned one-shot condition on the only problem where the conditions differed. The verifier-gating redesign also reduced the cascade's token usage by 61.5% relative to the previous review-gated implementation.**

## Gemini answers

The repository also contains scripts for generating Gemini answers under different inference conditions.

For example:

```bash
python3 run_open_ended_gemini.py
```

The Gemini conditions are intended to provide comparison baselines for the cascade rather than serve as components of the cascade itself.

## Judge Cascade against Gemini

For open-ended evaluation, the repository can use a separate judge model to compare generated answers.

For example:

```bash
python3 cascade_spike.py \
    --judge-model gpt-oss:120b-cloud
```

The judge is kept separate from the cascade being evaluated so that the evaluation model does not simply become another stage of the generation cascade.

## Limitations

The current results are a small pilot and should not be interpreted as a general benchmark.

### Evaluation scale

Only three code-generation problems are included in the reported execution-graded comparison. A larger and more diverse benchmark is required to make stronger claims.

### Token cost

The verifier-gating redesign demonstrates that **task-appropriate evaluation can substantially reduce cascade token usage**: the same three-problem pilot fell from 23,645 tokens under the previous review-gated implementation to 9,101 tokens under verifier-gating, a **61.5% reduction**.

However, the current cascade is **still not cheaper than Gemini one-shot** in this pilot, using 9,101 versus 2,581 tokens. The next optimization target is therefore not whether verifier-gating can reduce cost—it already has—but whether the cascade can approach one-shot efficiency while retaining the correctness benefits of adaptive escalation.

### Open-ended results

The reported numerical comparison above concerns the execution-graded code benchmark. Open-ended evaluation uses a different, model-based review protocol and should not be conflated with deterministic code verification.

### Token accounting

Token counts across Gemini and Ollama are not necessarily perfectly apples-to-apples because different providers and inference pipelines may tokenize or expose usage differently.

### Fixed model ordering

The current cascade uses a fixed weak-to-strong ordering. Future work could investigate adaptive routing or model selection based on problem characteristics.

### Verifier dependence

The strongest form of early stopping is available when correctness can be established deterministically. Problems without reliable verifiers require a different evaluation signal.

### Cloud model availability

The current model chain depends on cloud-hosted Ollama models and therefore depends on model availability, provider behavior, and network conditions.

### Single pilot run

The current experiment uses a small fixed evaluation set and a single reported run. Larger-scale experiments and repeated trials are needed to determine whether the observed accuracy and token-efficiency patterns generalize.

## Limitations & next steps

The next stage of the project is to evaluate the cascade over a larger benchmark and investigate whether the escalation policy can achieve better efficiency without sacrificing correctness.

Potential directions include:

* Larger execution-graded code benchmarks.
* More difficult problems that require deeper escalation.
* Further token-efficiency optimization toward one-shot cost.
* Adaptive model ordering.
* More robust verifier design.
* Larger open-ended evaluations.
* Comparison against additional iterative inference strategies.
* Analysis of when escalation is most useful.
* Studying whether an opaque cascade can reproduce the benefits of caller-managed retry policies.

## Core idea

The project is not fundamentally about using four models.

The central idea is:

> **Use the weakest model that can successfully solve the task, and escalate only when it cannot.**

For objectively verifiable tasks:

```text
Generate → Verify → PASS / FAIL → Escalate on FAIL
```

For open-ended tasks:

```text
Generate → Review → AGREE / EDIT / REWRITE → Escalate when needed
```

The four-model chain simply provides the available escalation capacity.

The intended abstraction is therefore an **adaptive weak-to-strong inference system**: one opaque call from the caller, with the cascade internally deciding how much model capacity the problem actually requires.
