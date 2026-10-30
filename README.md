# LLM-Cascade

Research project investigating **Minimum Sufficient Inference**: identifying the minimum amount of LLM inference required to reliably solve a task, and dynamically stopping inference once sufficient evidence exists.

The central research question is:

> **Can we identify the minimum amount of LLM inference necessary for reliable task completion, and dynamically stop inference once sufficient evidence exists?**

The cascade is the experimental mechanism used to study this question. The broader objective is to understand when additional inference is necessary, when it is redundant, and how routing can reduce unnecessary computation while preserving reliability.

---

## Research Framing

A conventional LLM pipeline often commits to a single model or applies a fixed sequence of increasingly capable models.

LLM-Cascade instead treats inference depth as a variable:

```text
Problem
   │
   ▼
Layer 1
   │
   ▼
Sufficient?
 ┌─┴─┐
Yes  No
 │    │
STOP  ▼
    Layer 2
       │
       ▼
    Sufficient?
     ┌─┴─┐
    Yes  No
     │    │
   STOP   ▼
        Layer 3
           │
           ▼
        Sufficient?
         ┌─┴─┐
        Yes  No
         │    │
       STOP   ▼
            Layer 4
               │
              STOP
```

The long-term objective is to characterize the relationship between **task difficulty, inference depth, reliability, and inference cost**.

---

# Model Ladder

The experiments use four model layers:

| Layer | Model                    |
| ----- | ------------------------ |
| L1    | `gpt-oss:20b-cloud`      |
| L2    | `gemma4:cloud`           |
| L3    | `nemotron-3-super:cloud` |
| L4    | `nemotron-3-ultra:cloud` |

Generation settings for Phases I and II:

```text
Temperature: 0.2
Seed: 42
```

Phase III collection uses different settings:

```text
Temperature: 0.0
Seed: 42
```

---

# Phase I — Execution-Gated Code Cascade

**Status: Complete and frozen**

Phase I evaluates whether objective execution feedback can dynamically determine inference depth for code-generation tasks.

The system generates code at Layer 1, executes deterministic tests, and escalates only when the candidate fails.

```text
Problem
   │
   ▼
L1 generates code
   │
   ▼
Execute tests
   │
 ┌─┴──────────┐
PASS         FAIL
 │             │
STOP           ▼
              L2
               │
               ▼
           Execute tests
               │
              ...
               │
               ▼
              L4
```

A stronger layer receives the original problem together with the previous candidate and objective execution failure information.

## Baselines

The Phase I harness supports:

* `layer_1_only`
* `layer_4_only`
* `fixed_cascade`
* `execution_gated`

The completed comparison was between `layer_1_only` and `execution_gated`.

`layer_4_only` and `fixed_cascade` remain implemented but were not run to completion across the full benchmark.

## Results

| Configuration   | Accuracy | Mean tokens/query | Median tokens/query | Escalation |
| --------------- | -------: | ----------------: | ------------------: | ---------: |
| L1-only         |    90.0% |            692.59 |              548.50 |         0% |
| Execution-gated |   100.0% |            807.64 |              581.00 |         8% |

The execution-gated system closed the observed 10-point accuracy gap relative to L1-only, escalating on 8% of problems. Every observed escalation was resolved at Layer 2.

Mean token use increased from 692.59 to 807.64 tokens/query.

## Phase I Caveats

### Oracle gate

The same deterministic tests both trigger escalation and score the result.

This is an **oracle-verified setting**: the gate has access to the grading signal, so the experiment measures how much inference is needed when that signal is available at inference time. It provides an upper-bound reference for what a deployed router could achieve with an equivalent reliable verification signal.

### Benchmark corrections

Some benchmark checks were corrected during Phase I.

The final results use merged provenance consisting of targeted reruns for corrected problems together with earlier runs for unaffected problems. When multiple records existed for the same problem, the newest corrected record was retained.

### Model nondeterminism

Cloud models can produce different outputs across repeated requests, including runs using temperature 0 and a fixed seed.

The reported values are therefore point estimates for the recorded runs.

### Runtime comparison

Total experiment wall-clock time is not used for comparison because experiments were interrupted and resumed across sessions.

Per-query latency remains available in the recorded traces.

---

# Phase II — Contextual Counterfactual Analysis

**Status: Complete and frozen**

Phase II asks:

> **For each problem, what is the minimum model layer that is sufficient to solve it?**

Three domains were evaluated:

1. Hard code
2. Mathematics
3. Finance

All four layers are evaluated for every problem.

The evaluation is **contextual and sequential**, rather than four independent standalone model calls.

```text
Problem
   │
   ▼
L1
   │
   ▼
L2 receives original problem + L1 response/verification
   │
   ▼
L3 receives original problem + L2 response/verification
   │
   ▼
L4 receives original problem + L3 response/verification
```

For each problem, the first layer whose answer passes the domain verifier is recorded as the **minimum sufficient layer**. Only L1 answers the original problem without a predecessor response. L2–L4 receive the preceding layer’s response and verification feedback, so their results are contextual rather than independent standalone measurements. The accuracy tables below therefore report **per-tier accuracy under contextual feedback**.

---

# Phase II — Hard Code

**Benchmark: 15 selected hard coding problems**

All four layers were evaluated on every problem.

The code candidates were evaluated using deterministic execution-based verification.

The experiment records:

* candidate generated by each layer
* execution result
* verification outcome
* token usage
* latency
* contextual predecessor information
* minimum sufficient layer

Frozen artifacts:

```text
traces/phase2_hard_15.jsonl
traces/phase2_hard_15_metadata.json
traces/phase2_hard_15_summary.jsonl
```

SHA-256:

```text
phase2_hard_15.jsonl:
0d25dbd4cb6c900a36139009b25239017ec6a6cd0c3b0b52ca9d2dcb86e4c764

phase2_hard_15_metadata.json:
8802af509f59ffdc42f0766b0baffd9e1005ea33e3b24963d4fc27b689f60354

phase2_hard_15_summary.jsonl:
79a2ecba6c3cb5971d1ebed4e6bc09dbaae81181fc3d9997465f56913df32410
```

---

# Phase II — Mathematics

**Benchmark: 15 hard numeric-only problems**

## Per-Tier Accuracy Under Contextual Feedback

| Layer | Accuracy | Mean tokens/query |
| ----- | -------: | ----------------: |
| L1    |   86.67% |            576.07 |
| L2    |   93.33% |            812.07 |
| L3    |   93.33% |            879.13 |
| L4    |  100.00% |           1147.67 |

## Minimum Sufficient Layer

| Minimum layer | Problems | Percentage |
| ------------- | -------: | ---------: |
| L1            |       13 |     86.67% |
| L2            |        2 |     13.33% |
| L3            |        0 |      0.00% |
| L4            |        0 |      0.00% |
| None          |        0 |      0.00% |

## Oracle Cascade

```text
Accuracy:       100.00%
Mean tokens:    672.40
Median tokens:  629.00
Mean latency:   8202.39 ms
```

These are benchmark-specific measurements from a 15-problem sample.

Frozen artifacts:

```text
traces/phase2_math_contextual_final.jsonl
traces/phase2_math_analysis_final.json
```

SHA-256:

```text
phase2_math_contextual_final.jsonl:
322d628186f36effd383fc08cb1d7f63fa257ce29560b60d2511a7552932e270

phase2_math_analysis_final.json:
88b1259284ebb1a3eccbccba017d2d94e42601967d4e1285e86422ff29d6416f
```

---

# Phase II — Finance

**Benchmark: 15 finance problems**

## Per-Tier Accuracy Under Contextual Feedback

| Layer | Accuracy | Mean tokens/query |
| ----- | -------: | ----------------: |
| L1    |   73.33% |            504.40 |
| L2    |   73.33% |            695.87 |
| L3    |  100.00% |            789.93 |
| L4    |  100.00% |            591.67 |

## Minimum Sufficient Layer

| Minimum layer | Problems | Percentage |
| ------------- | -------: | ---------: |
| L1            |       11 |     73.33% |
| L2            |        4 |     26.67% |
| L3            |        0 |      0.00% |
| L4            |        0 |      0.00% |
| None          |        0 |      0.00% |

## Oracle Cascade

```text
Accuracy:       100.00%
Mean tokens:    692.67
Median tokens:  451.00
Mean latency:   4638.27 ms
```

These are benchmark-specific measurements from a 15-problem sample.

Frozen artifacts:

```text
traces/phase2_finance_contextual.jsonl
traces/phase2_finance_analysis.json
```

SHA-256:

```text
phase2_finance_contextual.jsonl:
082e976ae088bfdd3f593e73fcb7ea3b2aa71071380280e674612edb03dec1bf

phase2_finance_analysis.json:
4c1e8469992b253a75ba2d78146b19a98ae4695835ce67db7c5b3a16a9a65230
```

---

# Phase II Interpretation

Phase II provides an empirical distribution of minimum sufficient inference depth for the evaluated benchmarks.

For mathematics:

```text
L1: 13/15
L2:  2/15
L3:  0/15
L4:  0/15
```

For finance:

```text
L1: 11/15
L2:  4/15
L3:  0/15
L4:  0/15
```

The results indicate that, within these small benchmark samples, many tasks can be solved at the first layer while a smaller subset benefits from additional inference.

These observations are benchmark-specific and should not be interpreted as universal statements about model capability or domain difficulty.

---

# Evaluation Methodology

Each domain uses a verifier appropriate to the task.

### Code

Candidate programs are executed against deterministic tests.

### Mathematics

Model responses are parsed for numeric answers and compared against expected values using the specified tolerance.

### Finance

Model responses are parsed for numeric answers and compared against expected values using the specified tolerance.

The analysis records per-tier performance under contextual escalation and the minimum sufficient layer. Only L1 is evaluated as a standalone response to the original problem; L2–L4 receive preceding responses and verifier feedback.

---

# Reproducibility

Phase I and Phase II are frozen.

## Frozen benchmarks

```text
benchmarks/code_100.json
benchmarks/math.json
benchmarks/finance.json
```

Benchmark SHA-256 hashes should be recorded here once the final benchmark hashes are available.

## Frozen Phase I artifacts

```text
results/phase1_100/
results/phase1_100_gated/
```

## Frozen Phase II artifacts

### Hard Code

```text
traces/phase2_hard_15.jsonl
traces/phase2_hard_15_metadata.json
traces/phase2_hard_15_summary.jsonl
```

### Mathematics

```text
traces/phase2_math_contextual_final.jsonl
traces/phase2_math_analysis_final.json
```

### Finance

```text
traces/phase2_finance_contextual.jsonl
traces/phase2_finance_analysis.json
```

The SHA-256 hashes above provide integrity checks for the frozen Phase II artifacts.

The raw traces are preserved separately from canonical final datasets where applicable.

---

# Phase III — Minimum Sufficient Inference with Learned Routers

**Status: Verifier correction evaluated; false-stop reduction remains open**

Phase III transitions beyond oracle verification to address the core Minimum Sufficient Inference challenge: **predicting when to stop or escalate dynamically using only response signals, task context, and inter-layer agreement, without knowing ground truth or consulting stronger models.**

The benchmark evaluated is **TAT-QA**, a complex financial question-answering dataset combining unstructured financial statements and structured tables.

### Key Innovations:
1. **Deterministic Dataset Pipeline (`phase3/dataset.py`):** Uses document-separated train, validation, and test partitions. The original traces contain 403 rows across 300 distinct training questions because their 100-row counterfactual companion overlaps the original training IDs. The optimization adds 100 unique questions with non-empty gold answers and nonblank responses at all four layers.
2. **Domain-Specific Financial Evaluator (`phase3/tatqa_verifier.py`):** Handles arithmetic derivations, scale multipliers (thousands/millions/billions), percentages vs. decimals, financial negative formats like `(512)`, year queries, and fast-path span verification with Ollama semantic fallback.
3. **Sequential Context Propagation (`phase3/collect.py`):** Higher layers receive the problem, table, and previous layer's answer to repair or confirm solutions rather than restarting blind.
4. **Domain-Enriched Feature Extraction (`phase3/signals.py`):**
   - `confidence`: verbalized self-reported confidence
   - `answer_similarity_previous`: textual similarity to the previous layer’s answer, computed from normalized text; it measures agreement, not correctness
   - `response_length_score`: full-response length, measured as whitespace-separated words and capped at 300 words
   - `is_arithmetic`: indicates TAT-QA answer-type metadata of `arithmetic`, `numeric`, or `float`
   - `is_span`: indicates answer-type metadata of `span`, `multi-span`, `multispan`, or `text`
   - `is_count`: separately indicates answer-type metadata of `count`
   - `final_answer_presence`: detects an explicit answer marker in the response
   - `hedging_score`: verbal uncertainty indicators (*"assuming"*, *"approximately"*, *"unclear"*, *"uncertain"*)
   - `answer_conciseness`: penalizes rambling, verbose answers
   - `numeric_density` & `reasoning_structure_score`: density of digits and reasoning keywords
5. **Logistic-Regression Routers (`phase3/router.py`):** Trained per layer on non-empty, nonblank examples; feature subsets and threshold combinations are selected on validation data. Thresholds minimize cumulative token cost subject to the **$\le 5\%$ false-stop constraint** and at least the validation-set L4 accuracy. The 25-question validation split is small. The held-out test set was not used to fit model parameters, select feature subsets, or choose thresholds; however, the numerical-label correction and the decision to retain the original rather than the retrained router were informed by test-set outcomes, as detailed below.

The optimization adds 100 verified training questions, fixes the missing `final_answer_presence` signal, adds a separate `is_count` feature, and audits per-layer feature subsets and regularization. A verifier audit found that accounting-negative values such as `$(1,161.33)` were parsed as positive, and numeric extraction could select a year from an answer explanation instead of the result. After correcting numeric labels in memory, the existing optimized router evaluates at **96% accuracy** and **1,137.2 mean tokens**, compared with **94%** for Always-L1 and **1,224.6** for Always-L4. This is a corrected evaluation of existing router artifacts, not a retrained router. The **4% false-stop rate (2 of 50)** remains a material issue; do not consider routing work complete. Further routing changes must be selected on validation data, and DistilBERT remains deferred.

---

### Phase III Benchmark Results (Held-Out Test Set: $N=50$)

| Method | Accuracy | Mean Tokens | Mean Latency | Latency Savings vs L4 | False Stop Rate | Unnecessary Escalation |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Always-L1** | 94.00% | 931.8 | 2.94s | +81.95% | — | — |
| **Fixed Cascade** ($\text{conf} \ge 0.95$) | 94.00% | 992.3 | 3.00s | +81.58% | 6.00% | 4.00% |
| Learned Cascade (original baseline; pre-verifier correction) | 96.00% | 1,539.2 | 3.91s | +76.00% | 2.04% | 46.94% |
| **Learned Cascade (optimized LogReg; corrected numeric labels)** | **96.00%** | **1,137.2** | **3.25s** | **+80.03%** | **4.00%** | **18.00%** |
| **Always-L4 (corrected numeric labels)** | 100.00% | 1,224.6 | 16.29s | 0.00% | — | — |
| **Oracle Cascade (corrected numeric labels)** | 100.00% | 1,039.1 | 3.33s | +79.52% | 0.00% | 0.00% |

#### Key Insights:
- **Token-cost improvement:** The optimized LogReg router uses **1,137.2 mean tokens**, 7.14% below Always-L4's 1,224.6, compared with 1,539.2 tokens for the original learned baseline.
- **Fewer unnecessary escalations:** The optimized router's held-out unnecessary-escalation rate is **18.00% (9/50)**, down from 46.94%. Its stop distribution is `L1: 40, L2: 10, L3: 0, L4: 0`.
- **Corrected numeric labels:** Rechecking the arithmetic records makes the optimized router **96% accurate** (48/50), above Always-L1's 94%. Always-L4 is **100% accurate** (50/50) after the corrected accounting-negative answer is recognized.
- **False stops remain:** The optimized router still has **2 false stops in 50 questions (4%)**. Both are genuine L1 mistakes that the router stopped on; this remains the main routing issue.
- **Token goal met:** Corrected evaluation retains **1,137.2 mean tokens** for the learned router versus **1,224.6** for Always-L4 (7.14% lower). The original router parameters and thresholds were not fitted on test results. However, choosing which artifact to retain after comparing the original and retrained versions was informed by test-set token use.
- **Label correction:** For the operating-income example, L1's `$(1,159)` is wrong; L2-L4's `$(1,161.33)` matches the gold derivation. The verifier previously selected the year 2017 as the candidate and treated accounting parentheses as positive, incorrectly marking every layer wrong and the item unresolved.
- **Validation and test-set limitations:** The saved thresholds were selected on only 25 validation examples. A temporary retraining experiment with corrected numeric training labels preserved 96% test accuracy but raised mean tokens above Always-L4; those temporary artifacts were not retained. Although test data were not used to fit the reported router parameters or thresholds, the numerical-label correction and the decision to retain the original artifacts after comparing them with the retrained alternative were informed by test outcomes. The results are therefore test-informed, not an untouched estimate of generalisation. Further routing changes should be selected on validation data and assessed on a fresh held-out set.
- **Feature audit is tentative:** The audit selects per-layer subsets and regularization using only 25 validation examples. The results are noisy and need more validation data before treating feature rankings as robust.

#### Additional Held-Out Results

| Answer type | Accuracy | Questions |
| :--- | :---: | ---: |
| Multi-span | 83.33% | 12 |
| Arithmetic | 100.00% | 13 |
| Span | 100.00% | 12 |
| Count | 100.00% | 13 |

The optimized router's median usage was **928.5 tokens** and median latency was **2.78s**. For comparison, Always-L4 used a median **1,139.5 tokens** and took **13.42s**; the corrected oracle cascade used **1,039.1 mean tokens**. Oracle agreement for the learned router was **78.00%**. The oracle cascade resolves all 50 questions (100%) after the numeric-label correction.

The corrected evaluation summary, including its numeric-label recheck scope and the two remaining false-stop examples, is in [`phase3/data/logreg_corrected_test_evaluation.json`](phase3/data/logreg_corrected_test_evaluation.json). The prior, pre-correction run is preserved in [`phase3/data/logreg_optimized_test_evaluation.json`](phase3/data/logreg_optimized_test_evaluation.json). The validation-only feature and hyperparameter audit is in [`phase3/data/logreg_feature_audit.json`](phase3/data/logreg_feature_audit.json).

---

# Repository Structure

```text
LLM-Cascade/
│
├── phase3/                       # Phase III Architecture & Pipeline
│   ├── config.py                 # Phase III paths, models, hyperparameters
│   ├── dataset.py                # TAT-QA dataset loader, split partitioning, stratification
│   ├── signals.py                # Signal & feature extraction for router models
│   ├── tatqa_verifier.py         # Financial correctness evaluator & edge case handler
│   ├── semantic_verifier.py      # Ollama-based semantic equivalence fallback
│   ├── collect.py                # Adaptive & counterfactual trace collection
│   ├── router.py                 # Logistic regression routers & threshold optimizer
│   ├── evaluation.py             # Cascade simulation, baselines, & comparative metrics
│   ├── analysis.py               # Failure inspection & distribution analysis
│   ├── main.py                   # Unified CLI for Phase III (collect, train, test, analyze)
│   ├── routers/                  # Trained router models (*.joblib) & thresholds.json
│   └── data/tatqa/               # Cached TAT-QA datasets
│
├── benchmarks/
│   ├── code_100.json
│   ├── code_subset.json
│   ├── math.json
│   └── finance.json
│
├── results/
│   ├── phase1_100/
│   ├── phase1_100_gated/
│   └── phase3_final_evaluation.json
│
├── traces/
│   ├── phase2_hard_15.jsonl
│   ├── phase2_hard_15_metadata.json
│   ├── phase2_hard_15_summary.jsonl
│   ├── phase2_math_contextual_final.jsonl
│   ├── phase2_math_analysis_final.json
│   ├── phase2_finance_contextual.jsonl
│   ├── phase2_finance_analysis.json
│   └── phase3/
│       ├── phase3_tatqa_train.jsonl
│       ├── phase3_tatqa_train_counterfactual.jsonl
│       ├── phase3_tatqa_validation_counterfactual.jsonl
│       ├── phase3_tatqa_test_counterfactual.jsonl
│       └── all_4_failures.json
│
├── audit_benchmark.py
├── config.py
├── evaluation.py
├── main.py
├── metrics.py
├── phase2_analysis.py
├── run_baselines.py
├── run_counterfactual_collection.py
├── sandbox.py
├── trace_logger.py
├── verifiers.py
├── requirements-freeze.txt
└── README.md
```

---

# Running the Experiments

## Phase I — Code

```bash
python main.py \
  --mode execution_gated \
  --problems-file benchmarks/code_100.json
```

Layer 1 only:

```bash
python main.py \
  --mode layer_1_only \
  --problems-file benchmarks/code_100.json
```

## Phase II — Mathematics

```bash
python run_counterfactual_collection.py \
  --domain math \
  --problems-file benchmarks/math.json \
  --output traces/phase2_math_contextual.jsonl
```

## Phase II — Finance

```bash
python run_counterfactual_collection.py \
  --domain finance \
  --problems-file benchmarks/finance.json \
  --output traces/phase2_finance_contextual.jsonl
```

The Phase II collection runner supports inclusive ranges through:

```text
--start-from
--end-at
```

The output writer merges records by `problem_id`, with a newer record replacing an older record.

## Phase III — Financial QA (TAT-QA)

### 1. Collect Traces
Collect counterfactual traces (all 4 layers evaluated on every problem) with live timers and incremental checkpointing:

```bash
# Collect validation set (25 problems)
python -m phase3.main collect --split validation --subset-size 25 --counterfactual

# Collect held-out test set (50 problems)
python -m phase3.main collect --split test --subset-size 50 --counterfactual

# Select and collect 100 new training questions after excluding existing IDs
# and checking for non-empty gold answers; outputs are under phase3/data/
python -m phase3.main collect --split train --counterfactual \
  --additional-training-count 100
```

### 2. Audit and Train Logistic-Regression Routers
Run validation feature ablations, then train on existing traces plus the additional counterfactual trace when present. Threshold combinations minimize validation cumulative tokens under the false-stop and L4-accuracy constraints:

```bash
python -m phase3.main audit

python -m phase3.main train \
  --train traces/phase3/phase3_tatqa_train.jsonl \
  --validation traces/phase3/phase3_tatqa_validation_counterfactual.jsonl \
  --output phase3/routers
```

The held-out test set must not be used to select features, model settings, or thresholds. Do not commit or publish generated traces, router artifacts, or results without explicit approval.

### 3. Evaluate the Cascade Against Baselines
Simulate the learned cascade on the held-out test traces and benchmark against Always-L1, Always-L4, Fixed Cascade, and Oracle:

```bash
python -m phase3.main test \
  --test traces/phase3/phase3_tatqa_test_counterfactual.jsonl \
  --routers phase3/routers \
  --output results/phase3_final_evaluation.json
```

### 4. Inspect Failures
Analyze remaining errors and verifier outputs:

```bash
python -m phase3.main analyze --failures
```

---

# Reliability Notes

### Verification availability

The strongest results currently come from domains where an objective verifier exists.

Code uses deterministic execution-based verification.

Mathematics and finance use numeric verification.

This does not establish that the same routing strategy works equally well for open-ended tasks where reliable verification is unavailable.

### Oracle verification

The current oracle cascade uses verification to determine whether inference should continue.

It should therefore be treated as a controlled reference point rather than a fully deployable routing system.

### Small Phase II samples

Each Phase II domain currently contains 15 problems.

The distributions are therefore exploratory benchmark measurements.

### Model nondeterminism

Cloud models can produce different responses across repeated requests.

The frozen artifacts represent the recorded experimental runs.

### Benchmark dependence

Minimum sufficient layer may depend on:

* task difficulty
* domain
* prompt structure
* model capabilities
* verifier design
* contextual information available to later layers

Further experiments are required before generalization.

---

# Future Work

## Phase IV — Generalization

**Status: Planned**

Evaluate whether minimum sufficient inference generalizes across broader task distributions and domains.

Potential evaluations include:

* additional reasoning benchmarks
* code generation
* mathematical reasoning
* financial reasoning
* factual question answering
* multimodal tasks
* longer-horizon reasoning
* different model families

---

# Research Direction

The project is centered on **Minimum Sufficient Inference**.

Rather than asking:

> Which model is best?

the project asks:

> **How much inference is actually necessary for this particular task?**

A highly capable model may solve every problem, but using it for every problem may perform unnecessary inference.

A weaker model may solve most problems cheaply, while a smaller subset genuinely benefits from additional computation.

The research objective is therefore to characterize and eventually predict the point at which additional inference becomes unnecessary.

```text
Task
 │
 ▼
Minimum necessary inference
 │
 ├── reliable solution
 │
 └── additional inference only when justified
```

Phase I establishes the execution-gated setting.

Phase II measures minimum sufficient inference across hard code, mathematics, and finance.

Phase III investigates predicting stopping decisions without oracle access via learned routers.

Phase IV builds and evaluates a production-grade 4-tier LLM cascade with a learned router, leak-free runtime evaluation, and a full train/validation/test benchmark split on financial audit queries.

---

# Status

```text
Phase I    Execution-gated code cascade               COMPLETE
Phase II   Contextual counterfactual analysis          COMPLETE
Phase III  Smarter routing (learned routers)           COMPLETE
Phase IV   Generalised cascade with learned router     COMPLETE
```

**Phases I, II, and III are frozen.**

---

# Phase IV — Generalised LLM Cascade with Learned Router

## Architecture

Phase IV implements a 4-tier LLM cascade over financial audit queries, with a Logistic Regression router making stop-or-escalate decisions using runtime signals only (no hidden gold answers).

```text
Query + Evidence
      │
      ▼
  Tier L1  ──► Runtime verifier + Judge ──► Router decision
      │ escalate
      ▼
  Tier L2  ──► Runtime verifier + Judge ──► Router decision
      │ escalate
      ▼
  Tier L3  ──► Runtime verifier + Judge ──► Router decision
      │ escalate
      ▼
  Tier L4  (final answer — always returned)
```

**Models used (local Ollama):**

| Tier | Model |
|------|-------|
| L1 | `gpt-oss:20b-cloud` |
| L2 | `gemma4:cloud` |
| L3 | `nemotron-3-super:cloud` |
| L4 | `nemotron-3-ultra:cloud` |

## Runtime vs. Offline Evaluation

A key design principle is strict separation between runtime and offline evaluation:

- **Runtime evaluation** (`verify_runtime`, `call_runtime_judge`): Uses only the query, structured evidence JSON, and the model's response. No gold answers, no `expected_numbers`, no `expected_status`. Used by the router for stop/escalate decisions in production.
- **Offline evaluation** (`verify_deterministic`, `call_llm_judge`): Uses gold ground truth for measurement and label derivation. Used only in the training harness and for benchmarking — never in the production routing path.

This eliminates the ground-truth leakage identified in the audit (reference specifications, benchmark labels, and `expected_numbers` were previously available to the runtime verifier).

## Dataset Splits

The benchmark (`phase4/data/benchmark_120.json`) contains **120 financial audit queries** across 6 scenario types. Split was performed before any evaluation:

| Split | Cases | Notes |
|-------|-------|-------|
| Train | 51 | Used to train the router; 1 case (`AUDIT_POC_034`) flagged `REQUIRES_REVIEW` |
| Validation | 26 | Used to calibrate thresholds and select the champion router |
| **Test (held-out)** | **43** | **Untouched until final frozen evaluation** |

**Scenario types:** `reverse_charge`, `transfer_pricing`, `vat_dispute`, `customs_valuation`, `tax_residency`, `unanswerable_missing_evidence`

## Router Training

The frozen champion router is a **Logistic Regression** model trained on runtime signals from genuine Ollama calls across all 4 tiers.

**Training run:** `results/phase4/runs/router_training_20261029_154818/logistic_regression/`

**Frozen thresholds:**

| Layer | Stop-probability threshold |
|-------|---------------------------|
| L1 | **0.68** (calibrated on validation) |
| L2 | 0.50 |
| L3 | 0.50 |
| L4 | 0.00 (always stop — last tier) |

**Signal features used:** `numeric_density`, `abstention_flag`, `length_log`, `audit_kw_density`, `runtime_verifier_pass`, `contradiction_flag` (extracted by `phase4/signals.py`).

## Validation Results (26 cases — used for calibration only)

> **Important:** Validation results were used to select and tune the router. Do not interpret them as unbiased estimates of generalisation performance. See Test Results below.

| Policy | Accuracy | False-Stop Rate | Avg tokens/case | Stop distribution |
|--------|----------|-----------------|-----------------|-------------------|
| Always-L1 | 84.6% | 15.4% | 1,821 | L1×26 |
| Always-L4 | 80.8% | 0.0% | 9,584 | L4×26 |
| Conservative Gate | 92.3% | 7.7% | 2,476 | L1×19, L2×7 |
| **LR Router (CHAMPION)** | **96.2%** | **0.0%** | **4,048** | L1×7, L2×16, L4×3 |
| Decision Tree Router | 84.6% | 12.5% | 3,048 | L1×16, L2×8, L4×2 |
| Hybrid Gate+Router | 96.2% | 0.0% | 4,433 | L1×3, L2×20, L4×3 |
| Oracle | 100.0% | 0.0% | 2,137 | L1×22, L2×4 |

*False Stop Rate = fraction of cases stopped before L4 that were answered incorrectly.*

The LR Router matches the Hybrid policy on accuracy and FSR while using 8.5% fewer tokens, making it the Pareto-optimal choice.

## Test Results (43 held-out cases — frozen evaluation)

> **Methodology:** Evaluated on October 29–30, 2026 using the frozen Logistic Regression router (`router_training_20261029_154818`) with fixed thresholds (L1: 0.68, L2: 0.50, L3: 0.50). The 43 held-out test cases (`AUDIT_POC_078` through `AUDIT_POC_120`) remained completely untouched throughout exploration, tuning, and threshold calibration. All model inferences and judge calls were executed live against local Ollama.

### Primary Metrics

| Metric | Frozen LR Router | Always-L1 | Always-L2 | Always-L3 | Always-L4 |
|--------|------------------|-----------|-----------|-----------|-----------|
| **Accuracy** | **86.0%** (37/43) | 88.4% (38/43) | 90.7% (39/43) | 88.4% (38/43) | 74.4% (32/43) |
| **False-Stop Rate** | **10.3%** (4/39) | 11.6% (5/43) | 9.3% (4/43) | 11.6% (5/43) | 0.0% (0/0) |
| **Cascade Stop Distribution** | L1: 9, L2: 29, L3: 1, L4: 4 | L1: 43 | L2: 43 | L3: 43 | L4: 43 |
| **Avg Generator Tokens** | **4,154** | ~1,800 | ~3,200 | ~6,500 | ~9,500 |

### Breakdown by Scenario Type (Frozen LR Router)

| Scenario Type | Accuracy | Correct / Total | Notes |
|---------------|----------|-----------------|-------|
| `adversarial_distractor` | **100.0%** | 5 / 5 | Injection disregarded across all cases; stopped cleanly at L1–L2 |
| `contradictory_evidence` | **100.0%** | 5 / 5 | Contradictions properly detected; routed to L2 |
| `factual_extraction` | **100.0%** | 4 / 4 | Accurate numerical extraction; stopped at L2 |
| `reconciliation_discount` | **80.0%** | 4 / 5 | 1 false stop at L2 (`AUDIT_POC_101`) |
| `reconciliation_gst` | **100.0%** | 5 / 5 | Perfect reconciliation; 3 stopped at L1, 2 at L2 |
| `reconciliation_payment` | **100.0%** | 5 / 5 | Perfect payment matching; 3 stopped at L1, 2 at L2 |
| `reverse_charge` | **80.0%** | 4 / 5 | 1 failure at L2 (`AUDIT_POC_114` — premature abstention) |
| `tax_audit_interstate` | **75.0%** | 3 / 4 | 1 false stop at L1 (`AUDIT_POC_095`) |
| `unanswerable_missing_evidence` | **40.0%** | 2 / 5 | 2 stopped at L4 where L4 over-answered; 1 stopped at L3 |

### Validation vs. Held-Out Test Analysis

- **Generalisation Gap:** Validation accuracy was **96.2%** (FSR 0.0%), whereas held-out test accuracy is **86.0%** (FSR 10.3%). This difference reflects genuine out-of-sample difficulty, particularly on `unanswerable_missing_evidence` where boundary cases were challenging for both smaller and larger models.
- **The "Bigger is Not Always Better" Dynamic:** Always-L4 achieves only **74.4%** on the test set, substantially underperforming the smaller tiers (L1: 88.4%, L2: 90.7%) and the cascade (86.0%). The largest model (`nemotron-3-ultra:cloud`) demonstrates a pronounced tendency to over-answer and hallucinate explanations when critical evidence is absent, rather than correctly abstaining. The cascade mitigates this by stopping 38 of 43 queries at L1–L2.
- **Token Efficiency:** The cascade achieved 86.0% accuracy with an average of **4,154 generator tokens** per case, consistent with validation predictions (~4,048 tokens) and substantially below Always-L4 (~9,500 tokens).

## Reproduction Commands

All commands assume a local Ollama instance at `http://localhost:11434` with the 4 required models loaded.

```bash
# 1. Collect genuine training data (genuine Ollama calls required)
python3 phase4/collect_genuine_dataset.py --split train --run_id genuine_train_full

# 2. Collect genuine validation data
python3 phase4/collect_genuine_dataset.py --split validation --run_id genuine_validation_full

# 3. Train all router variants and benchmark on validation set
python3 phase4/train_routers.py \
    --train_data results/phase4/runs/genuine_train_full/genuine_train_dataset.json \
    --val_data results/phase4/runs/genuine_validation_full/genuine_validation_dataset.json

# 4. Run frozen evaluation on the held-out test set
python3 phase4/evaluate_test.py

# 5. Run unit tests
python3 -m unittest discover -s phase4/tests
```

## Known Limitations

- **Local Ollama required.** The evaluation scripts abort (no mock fallback) if Ollama is unreachable, ensuring all reported results come from genuine model calls.
- **`AUDIT_POC_034` label conflict.** This training case has an unresolvable disagreement between the deterministic verifier and the LLM judge across all 4 tiers. It is retained in the dataset with `REQUIRES_REVIEW: true` and was assigned `layer_4` as a conservative label. It should be excluded from any future retraining until the ambiguity is resolved.
- **sklearn version mismatch.** The frozen `.joblib` files were saved with scikit-learn 1.9.1 and load with an `InconsistentVersionWarning` on earlier versions. This is cosmetic — predictions remain valid, but re-saving with the current version is recommended for long-term reproducibility.
- **`phase4/routers/` contains stale pre-training artifacts** with uniform thresholds (0.5/0.5/0.5). These are NOT the frozen champion router. The champion is at `results/phase4/runs/router_training_20261029_154818/logistic_regression/`.
- **Absolute paths in summary metadata.** `*_summary.json` files record machine-specific absolute paths in `dataset_file` and `traces_file` fields. These are informational only; all production paths are resolved relative to `PROJECT_ROOT` in source code.
- **Cost not reported.** All models run on a local Ollama instance; no verified per-token pricing is available for local endpoints.
