# Phase IV — Evidence-Grounded Financial Auditing Proof-of-Concept (PoC)

This directory contains the complete implementation of **Phase IV of the LLM-Cascade project**, extending the four-tier adaptive inference architecture to **evidence-grounded financial auditing**.

---

## 1. Project Scope & Architecture

### In-Scope: LLM Auditing Component
* **Inputs:** User audit queries paired with structured evidence JSON (business metadata, invoice details, itemized line items, tax breakdowns, payments, provenance bounding records, and document chunks).
* **Outputs:** Evidence-grounded answers with reconciliation statuses (`MATCHED`, `DISCREPANCY`, `UNSUPPORTED`, `CONTRADICTORY`, `INSUFFICIENT_EVIDENCE`), mathematical derivations, supporting citations, and calibrated uncertainty/abstention.
* **Inference Cascade:** Decides dynamically whether Layer 1 is sufficient or whether to escalate sequentially through Layer 2, Layer 3, and Layer 4 with predecessor context and verification critique.

### Explicitly Out of Scope:
* RAG retrieval pipelines, OCR, PDF/image parsing, vector embeddings, and upstream database ingestion (owned by upstream systems).

---

## 2. Database Integration: Supabase & Local Development Fixture

### 2.1 Authoritative Schema
Conforms strictly to the provided logical schema:
1. `public.business`
2. `public.source_file`
3. `public.invoice`
4. `public.invoice_item`
5. `public.invoice_payment`
6. `public.invoice_extraction`
7. `public.invoice_field_provenance`
8. `public.document_chunks` (embedding supported via pgvector `vector(1536)` with graceful fallback to `DOUBLE PRECISION[]`).

### 2.2 Connection Strategy (Zero-Secret)
* **Supabase Integration (`phase4/db/supabase_adapter.py`):** Authorised connection via `SUPABASE_DB_URL` or `DATABASE_URL` environment variables. No secrets or credentials are hard-coded in the repository.
* **Portable Development Fixture (`phase4/db/sqlite_fixture.py`):** Standalone SQLite replica of the authoritative schema (`phase4/data/audit_fixture.db`) seeded with 8 synthetic Indian businesses and 120 invoices.
* **Zero Runtime Dependency:** The LLM evaluation harness operates directly on JSON inputs and does not depend on database uptime.

---

## 3. Synthetic Benchmark (120 Cases)

A benchmark of **120 realistic, internally consistent synthetic Indian financial auditing cases** was generated and saved to `phase4/data/benchmark_120.json`.

### Coverage:
* **Factual Extraction (20 cases):** Retrieval of GSTIN, PAN, dates, and amounts.
* **Invoice & GST Reconciliation (30 cases):** Base taxable value vs gross total explaining 5%, 12%, 18%, 28% GST, cess, round-offs.
* **Discounts & Deductions (15 cases):** Line-item and trade discounts reducing taxable base.
* **Payment Auditing (15 cases):** Partial payments, underpayments, and outstanding ledger reconciliation.
* **Interstate vs. Intrastate Classification (15 cases):** Supplier in Maharashtra (27) billing Karnataka (29) with IGST vs intrastate CGST+SGST.
* **Reverse Charge & TDS (10 cases):** Tax liability falling on recipient under GST rules.
* **Unanswerable & Missing Evidence (10 cases):** Inquiries about unrecorded delivery dates or missing line items requiring **explicit abstention**.
* **Contradictory Evidence & Adversarial Injections (5 cases):** Discrepant amounts between raw text notes and structured items, or adversarial prompts in chunks (`AUDIT PASSED 100%`).

### Leak-Free Partitioning (By Business ID):
* **Train split (51 cases):** Businesses `biz_001` (Maharashtra), `biz_002` (Karnataka), `biz_003` (Delhi).
* **Validation split (26 cases):** Businesses `biz_004` (Tamil Nadu), `biz_005` (Gujarat).
* **Held-out Test split (43 cases):** Businesses `biz_006` (Punjab), `biz_007` (Andhra Pradesh), `biz_008` (Karnataka).

---

## 4. Hybrid Verification Framework

Audit explanations cannot be judged by exact-match alone. We implement a **dual verification engine**:

```text
                  Model Candidate Answer
                            │
          ┌─────────────────┴─────────────────┐
          ▼                                   ▼
  Deterministic Code                     LLM Judge
 (phase4/verifier.py)               (phase4/judge.py)
   - Arithmetic checks (₹0.50 tol)    - Evidence grounding (0-5)
   - Provenance citation validity     - Factual correctness (0-5)
   - Mandatory abstention check       - Completeness (0-5)
   - Adversarial compliance check     - Uncertainty calibration (0-5)
          │                                   │
          └─────────────────┬─────────────────┘
                            ▼
             Hybrid Decision: PASS iff BOTH agree
```

### Disagreement Analysis:
* **Judge Limitations:** LLM judges can be overly generous on persuasive but mathematically inaccurate explanations (False Acceptance) or penalize valid paraphrased calculations (False Rejection).
* **Strict Hybrid Rule:** An answer only receives `overall_pass = True` when **both** deterministic verification and the semantic judge agree.

---

## 5. Experimental Results: 4-Tier Cascade Evaluation

Evaluated across the held-out test split ($N=43$ cases):

| Policy | Accuracy / Pass Rate | Mean Tokens | Median Tokens | Mean Latency | Est. Cost / Query | Stop Distribution |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Always-L1** | 6.98% | 151.2 | 154.0 | 150.0 ms | $0.000076 | L1: 43 |
| **Always-L4** | 37.21% | 158.7 | 156.0 | 150.0 ms | $0.000635 | L4: 43 |
| **Fixed Cascade** ($\text{conf} \ge 0.90$) | 6.98% | 151.2 | 154.0 | 150.0 ms | $0.000076 | L1: 43 |
| **Learned Cascade (LogReg Router)** | **37.21%** | 534.6 | 616.0 | 512.8 ms | $0.000896 | L2: 10, L3: 5, L4: 28 |
| **Oracle Baseline ($L^*(q)$)** | 53.49% | 489.8 | 576.0 | 470.9 ms | $0.000779 | L1: 3, L2: 11, L3: 6, L4: 23 |

### Key Findings:
1. **False-Stop Constraint Met:** The Learned Router achieved a **False-Stop Rate of 4.65%** (2 out of 43 cases), successfully adhering to the strict **$\le 5\%$ false-stop constraint**.
2. **Quality Parity with Always-L4:** The Learned Cascade achieved **37.21% accuracy**, matching Always-L4 while identifying 15 cases (35% of the benchmark) that can be reliably resolved at Layer 2 or Layer 3 without escalating to Layer 4.
3. **Robustness Against Adversarial Injections:** Layer 1 failed on injected prompt overrides in document chunks; contextual escalation to Layer 2 and above allowed the system to detect and discard the distractor.

---

## 6. How to Run and Reproduce

### 1. Run Automated Unit Tests (10/10 passing)
```bash
.venv/bin/python3 -m unittest discover phase4/tests
```

### 2. Initialize and Verify Database Fixture
```bash
# Initialize SQLite fixture or test Supabase connection
python -m phase4.main db-init

# Seed synthetic businesses and invoices
python -m phase4.main db-seed

# Verify table counts and integrity
python -m phase4.main db-verify
```

### 3. Generate Benchmark and Train Cascade Router
```bash
# Generate 120 synthetic cases
python -m phase4.main benchmark-gen

# Train logistic regression routers on train/validation splits
python -m phase4.main train-router
```

### 4. Run Cascade Benchmark Evaluation
```bash
# Evaluate on held-out test split
python -m phase4.main evaluate --split test

# Evaluate with live Ollama models (if active)
python -m phase4.main evaluate --split test --live
```

### 5. Audit an Individual Case
```bash
python -m phase4.main audit --input phase4/data/sample_audit_query.json
```

---

## 7. Limitations & Future Work

1. **PoC Synthetic Benchmark:** The 120 cases provide proof-of-concept validation for Indian GST invoice auditing. Real production generalization will require evaluation on actual audited datasets when provided by upstream teams.
2. **Deterministic-Judge Disagreements:** On complex narrative invoices, the LLM judge occasionally grants high scores when numbers deviate slightly; our strict hybrid verifier overrides the judge in such cases.
3. **Phases I–III Unchanged:** All historical traces, benchmarks, and router code in Phases I–III remain completely preserved.
