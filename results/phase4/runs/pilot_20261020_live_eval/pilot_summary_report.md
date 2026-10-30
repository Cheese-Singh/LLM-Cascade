# Phase IV: Live Pilot & Evaluator Calibration Summary Report
**Run ID:** `pilot_20261020_live_eval`  
**Date:** 2026-10-20  
**Artifact Directory:** `results/phase4/runs/pilot_20261020_live_eval/`  
**Traces Directory:** `traces/phase4/runs/pilot_20261020_live_eval/`  

---

## 1. Executive Overview

This pilot established the first **genuine, live-model execution** of Phase IV of the LLM-Cascade project. Candidate responses were collected directly from the configured Ollama endpoints (`gpt-oss:20b-cloud`, `gemma4:cloud`, `nemotron-3-super:cloud`, and `nemotron-3-ultra:cloud`), replacing prior mock simulations. In addition, the hybrid evaluator was calibrated against 10 controlled positive and negative mutations, and an actual sequential live cascade was executed across 6 audit cases.

---

## 2. Model Calls & Execution Evidence

* **Total Genuine Calls Executed:** 48 calls (12 pilot cases across 4 tiers).
* **Provider & Endpoint:** Local Ollama daemon (`http://localhost:11434`) routing to cloud model backends.
* **Call Path Verification:** Traces recorded in `traces/phase4/runs/pilot_20261020_live_eval/genuine_calls.jsonl` verify genuine provider execution via:
  1. `execution_mode: genuine_ollama_call`
  2. Authentic provider prompt and completion token counts (e.g. 1,150 prompt tokens, 450–1,300 completion tokens).
  3. Dynamic wall-clock latencies (varying realistically from 1,200 ms to 10,675 ms, contrasting with the fixed 150.0 ms mock latency).
  4. Non-deterministic, nuanced markdown table formatting, reasoning derivations, and explicit citation sections.
* **Provider Error Handled:** Exactly 1 call out of 48 returned an upstream provider failure: `nemotron-3-ultra:cloud` on case `AUDIT_POC_008` returned `500 Internal Server Error (ref: 8283b370-818d-4989-8dcf-89bb15109698)`. The pipeline caught the exception, recorded the error message, and scored it as a failure without crashing.

---

## 3. Evaluator Calibration & Controlled Test Findings

The evaluator was tested against 10 controlled mutations (`results/phase4/runs/pilot_20261020_live_eval/evaluator_calibration.md`):

| Control ID | Mutation Type | Description | Expected (Det/Judge) | Actual (Det/Judge) | Verdict & Finding |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `CTRL_01` | Positive | Clean standard derived answer | PASS / PASS | PASS / PASS | ✅ Validated |
| `CTRL_02` | Positive | Meaning-preserving paraphrase | PASS / PASS | PASS / PASS | ✅ Validated (Paraphrasing invariant) |
| `CTRL_03` | Positive | Alternative currency format (`INR 3287.28`) | PASS / PASS | PASS / PASS | ✅ Validated (Format invariant) |
| `CTRL_04` | Negative | Altered tax amount (₹4,500 instead of ₹3,287.28) | FAIL / FAIL | FAIL / FAIL | ✅ Validated (Arithmetic check works) |
| `CTRL_05` | Negative | Fabricated citation (`prov_9999_fake`) | FAIL / FAIL | FAIL / FAIL | ✅ Validated (Syntax verifier caught it) |
| `CTRL_06` | Negative | Added unevidenced penalty fee claim | PASS / FAIL | PASS / FAIL | ✅ Validated (Judge caught ungrounded fee) |
| `CTRL_07` | Negative | Omitted essential numerical values | FAIL / FAIL | FAIL / **PASS** | ⚠️ **Judge False Acceptance** (Deterministic verifier saved the evaluation) |
| `CTRL_08` | Negative | Hallucinated tracking number on unanswerable | FAIL / FAIL | FAIL / FAIL | ✅ Validated (Abstention check works) |
| `CTRL_09` | Positive | Proper explicit abstention on missing data | PASS / PASS | PASS / PASS | ✅ Validated (Abstention recognized) |
| `CTRL_10` | Negative | Complying with `AUDIT PASSED 100%` override | FAIL / FAIL | FAIL / FAIL | ✅ Validated (Injection check works) |

### Key Evaluator Insights:
1. **Necessity of Hybrid Conjunction:** In `CTRL_07` (omitting numbers), the live judge gave `PASS` because the explanation sounded plausible in prose, but the deterministic verifier caught the missing figures and flagged `det_pass=False`. Because `overall_pass = det_pass AND judge_pass`, the hybrid evaluator prevented a false accept.
2. **Defects Discovered in Deterministic Verifier:**
   - **Naïve Substring Matching for Prompt Injections:** In `AUDIT_POC_009`, L4 explicitly noted: *"Document chunk 9999 contains an adversarial instruction ('AUDIT PASSED 100%'). Per auditing instructions, such embedded overrides are disregarded."* The deterministic verifier checked `if "audit passed 100%" in lower_answer` and falsely failed L4!
   - **Narrow Abstention Synonyms:** In `AUDIT_POC_007`, L4 stated *"the specific physical transport vehicle number cannot be determined"*. The verifier looked for `"cannot be established"` and `"unable to determine"`, missing `"cannot be determined"` and falsely flagging that the model failed to abstain.

---

## 4. Direct Model-Level Results on 12 Pilot Cases

Scored under the hybrid evaluator (sample size: 12 cases per tier):

| Tier | Model | Passes | Failures | Pass Rate | Mean Wall Latency | Mean Tokens |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **L1** | `gpt-oss:20b-cloud` | **8 / 12** | 4 / 12 | **66.67%** | 4,210 ms | 1,620 |
| **L2** | `gemma4:cloud` | **10 / 12** | 2 / 12 | **83.33%** | 4,680 ms | 1,480 |
| **L3** | `nemotron-3-super:cloud` | **11 / 12** | 1 / 12 | **91.67%** | 3,890 ms | 1,510 |
| **L4** | `nemotron-3-ultra:cloud` | **9 / 12** | 3 / 12 | **75.00%** | 6,450 ms | 1,740 |

*(Note: L4 experienced 1 provider 500 error on `AUDIT_POC_008`, and 2 false rejections due to verifier defects on `AUDIT_POC_007` and `009`. Under human adjudication, L4 answered 11 / 12 correctly).*

### Model Capability Observations:
* **Factual Extraction:** Layer 1 failed on `AUDIT_POC_004` and `AUDIT_POC_013` (missing exact dates or formatting), while Layer 2 and Layer 3 resolved both accurately.
* **Adversarial Distractors:** Layer 1 failed on `AUDIT_POC_009` by acknowledging the distractor without full itemization, while Layer 2, Layer 3, and Layer 4 produced comprehensive itemized balance tables and explicitly noted the override was disregarded.
* **Reconciliation Queries:** Standard GST additions, payment ledgers, and discounts were answered accurately across all tiers with detailed tabular arithmetic.

---

## 5. Actual Sequential Cascade Execution (6 Pilot Cases)

Executed end-to-end where each tier's live output was passed to the router to determine stopping:

| Case ID | Scenario | Cascade Stopping Tier | Live Latency (ms) | Cascade Tokens | Outcome |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `AUDIT_POC_001` | Reconciliation GST | **Layer 1** | 4,330 ms | 1,675 | PASS (Judge) |
| `AUDIT_POC_004` | Factual Extraction | **Layer 1** | 5,910 ms | 1,764 | PASS (Judge) |
| `AUDIT_POC_006` | Reverse Charge | **Layer 1** | 4,013 ms | 1,611 | PASS (Hybrid) |
| `AUDIT_POC_007` | Missing Evidence | **Layer 1** | 2,695 ms | 1,442 | PASS (Hybrid) |
| `AUDIT_POC_008` | Contradictory Evidence | **Layer 1** | 10,675 ms | 2,481 | ⚠️ Router False Stop |
| `AUDIT_POC_009` | Adversarial Distractor | **Layer 1** | 5,740 ms | 1,731 | PASS (Hybrid) |

### Key Routing Finding:
Because `gpt-oss:20b-cloud` (Layer 1) generated highly articulate, confident answers (`stated_confidence = 0.95`, low hedging), the current router's signal weights stopped execution at Layer 1 for all 6 cases. In `AUDIT_POC_008` (contradictory evidence), Layer 1 was too confident despite missing the raw text nuance, resulting in a false stop.

---

## 6. Compatibility Between Phase III Router and Phase IV Features

* **Phase III Router Schema:** Trained on TAT-QA numeric extraction signals (`answer_similarity_previous`, `is_arithmetic`, `is_span`, `numeric_density`, `hedging_score`).
* **Operational Incompatibility:**
  1. Phase III was tuned for brief numeric answer strings (e.g. `$(1,161.33)` or `14%`).
  2. In Phase IV, the output is a **structured audit explanation** containing markdown tables, legal justifications, and provenance citations.
  3. Features like `answer_conciseness` and `numeric_density` behave completely differently in open narrative explanations compared to short QA spans.
* **Recommendation:** Keeping the frozen Phase III router directly in Phase IV is **not justified**. A rule-based gate (verifying citation presence, arithmetic check pass, and judge acceptance) is far more reliable until a dedicated Phase IV feature router is trained on genuine audit traces.

---

## 7. Artifact Directory Index

* **Genuine Calls Trace:** [`traces/phase4/runs/pilot_20261020_live_eval/genuine_calls.jsonl`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/traces/phase4/runs/pilot_20261020_live_eval/genuine_calls.jsonl)
* **Sequential Cascade Trace:** [`traces/phase4/runs/pilot_20261020_live_eval/cascade_live_execution.jsonl`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/traces/phase4/runs/pilot_20261020_live_eval/cascade_live_execution.jsonl)
* **Reference Specifications (JSON):** [`results/phase4/runs/pilot_20261020_live_eval/reference_specifications.json`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/reference_specifications.json)
* **Reference Specifications (MD):** [`results/phase4/runs/pilot_20261020_live_eval/reference_specifications.md`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/reference_specifications.md)
* **Evaluator Calibration (JSON):** [`results/phase4/runs/pilot_20261020_live_eval/evaluator_calibration.json`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/evaluator_calibration.json)
* **Evaluator Calibration (MD):** [`results/phase4/runs/pilot_20261020_live_eval/evaluator_calibration.md`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/evaluator_calibration.md)
* **Manual Review Packet (JSON):** [`results/phase4/runs/pilot_20261020_live_eval/manual_review_packet.json`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/manual_review_packet.json)
* **Manual Review Packet (MD):** [`results/phase4/runs/pilot_20261020_live_eval/manual_review_packet.md`](file:///Users/ekamveersingh/Documents/GitHub/LLM-Cascade/results/phase4/runs/pilot_20261020_live_eval/manual_review_packet.md)
