from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional
import ollama

from phase4.config import DEFAULT_JUDGE_MODEL, CHEAP_JUDGE_MODEL, OLLAMA_HOST

logger = logging.getLogger(__name__)

OFFLINE_JUDGE_PROMPT_TEMPLATE = """You are an expert impartial financial audit judge evaluating an LLM's response against source evidence and benchmark gold reference notes.

[SOURCE EVIDENCE JSON]:
{evidence_json}

[USER AUDIT QUERY]:
{query}

[CANDIDATE ANSWER TO EVALUATE]:
{answer}

[REFERENCE AUDIT NOTES (BENCHMARK GOLD STANDARDS)]:
Expected Status: {expected_status}
Expected Numbers / Calculations: {expected_numbers}
Required Citation IDs: {required_citations}
Expected Explanation / Reconciliation: {expected_explanation}
Allow Abstention: {allow_abstain}

Grade the candidate answer on a 0 to 5 scale across these 4 rubric dimensions:
1. evidence_grounding (0-5): Are all stated facts strictly grounded in the source evidence? (0 if hallucinated, 5 if perfectly cited).
2. factual_correctness (0-5): Are the arithmetic, invoice totals, and GST tax classifications accurate?
3. completeness (0-5): Did the model answer the core inquiry, include required numbers, and explain the discrepancy? (Deduct points if critical calculations or numbers are omitted).
4. uncertainty_calibration (0-5): Did the model properly abstain or state uncertainties if evidence was insufficient or contradictory?

Return ONLY a JSON object formatted exactly as follows:
{{
  "evidence_grounding": 5,
  "factual_correctness": 5,
  "completeness": 5,
  "uncertainty_calibration": 5,
  "overall_verdict": "PASS",
  "reasoning": "Concise justification for scores"
}}
Where overall_verdict is "PASS" if each score is >= 4, else "NO_PASS".
"""

# Alias for backwards compatibility with offline tools
JUDGE_PROMPT_TEMPLATE = OFFLINE_JUDGE_PROMPT_TEMPLATE

RUNTIME_JUDGE_PROMPT_TEMPLATE = """You are an expert impartial financial audit judge evaluating an LLM's audit response against the provided source evidence.
You do NOT have access to any external or future information.

[SOURCE EVIDENCE JSON]:
{evidence_json}

[USER AUDIT QUERY]:
{query}

[CANDIDATE ANSWER TO EVALUATE]:
{answer}

Evaluate the candidate answer strictly using the provided evidence and user inquiry.
Grade the candidate answer on a 0 to 5 scale across these 4 rubric dimensions:
1. evidence_grounding (0-5): Are all stated facts, invoice numbers, and figures strictly grounded in the source evidence? (0 if hallucinated, 5 if perfectly cited and grounded).
2. factual_correctness (0-5): Are the arithmetic derivations, tax calculations, and reconciliation statements mathematically sound and consistent with the evidence?
3. completeness (0-5): Did the model directly and thoroughly answer the user's inquiry, explaining discrepancies where present, without omitting critical facts?
4. uncertainty_calibration (0-5): Did the model properly state limitations or abstain if evidence was missing or contradictory, without hallucinating unsupported claims?

Return ONLY a JSON object formatted exactly as follows:
{{
  "evidence_grounding": 5,
  "factual_correctness": 5,
  "completeness": 5,
  "uncertainty_calibration": 5,
  "overall_verdict": "PASS",
  "reasoning": "Concise justification for scores"
}}
Where overall_verdict is "PASS" if each score is >= 4, else "NO_PASS".
"""


def evaluate_with_mock_judge(
    query: str,
    answer: str,
    evidence: Dict[str, Any],
    ground_truth: Optional[Dict[str, Any]],
    deterministic_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Deterministic mock judge for offline execution and CI."""
    det_pass = deterministic_result.get("deterministic_pass", deterministic_result.get("runtime_pass", False))
    reasons = deterministic_result.get("reasons", [])

    if det_pass:
        return {
            "evidence_grounding": 5,
            "factual_correctness": 5,
            "completeness": 5,
            "uncertainty_calibration": 5,
            "overall_verdict": "PASS",
            "reasoning": "Candidate answer satisfies all evidence grounding and factual reconciliation criteria.",
            "is_mock": True,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "latency_ms": 0.0,
        }
    else:
        return {
            "evidence_grounding": 3,
            "factual_correctness": 2,
            "completeness": 3,
            "uncertainty_calibration": 2,
            "overall_verdict": "NO_PASS",
            "reasoning": f"Failed criteria: {'; '.join(reasons)}",
            "is_mock": True,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "latency_ms": 0.0,
        }


def call_runtime_judge(
    query: str,
    answer: str,
    evidence: Dict[str, Any],
    judge_model: str = DEFAULT_JUDGE_MODEL,
    client: Optional[ollama.Client] = None,
    allow_mock_fallback: bool = False,
    runtime_verifier_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Production runtime judge: operates strictly without ground-truth benchmark references.
    Captures verified provider tokens and latency.
    """
    if client is None:
        try:
            client = ollama.Client(host=OLLAMA_HOST)
        except Exception:
            client = None

    prompt = RUNTIME_JUDGE_PROMPT_TEMPLATE.format(
        evidence_json=json.dumps(evidence, indent=2),
        query=query,
        answer=answer,
    )

    retries = 0
    if client is not None:
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                response = client.chat(
                    model=judge_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.0, "seed": 42, "num_predict": 2048},
                )
                lat_ms = round((time.perf_counter() - t0) * 1000, 2)
                content = response.message.content.strip()

                if "```json" in content:
                    content = content.split("```json")[-1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[-1].split("```")[0].strip()

                parsed = json.loads(content)
                parsed["is_mock"] = False
                parsed["judge_model"] = judge_model
                parsed["prompt_tokens"] = getattr(response, "prompt_eval_count", None)
                parsed["completion_tokens"] = getattr(response, "eval_count", None)
                p_tok = parsed["prompt_tokens"] or 0
                c_tok = parsed["completion_tokens"] or 0
                parsed["total_tokens"] = (p_tok + c_tok) if (parsed["prompt_tokens"] is not None) else None
                parsed["latency_ms"] = lat_ms
                parsed["retries"] = retries
                return parsed
            except Exception as e:
                retries += 1
                logger.warning("Runtime live judge call to %s attempt %d failed (%s).", judge_model, attempt + 1, e)
                time.sleep(attempt * 2 + 1)

        if not allow_mock_fallback:
            raise RuntimeError(f"Runtime judge model {judge_model} unavailable after {retries} retries.")

    if allow_mock_fallback:
        res = evaluate_with_mock_judge(query, answer, evidence, None, runtime_verifier_result or {"runtime_pass": True, "reasons": []})
        res["judge_model"] = f"{judge_model}_mock_fallback"
        return res

    raise RuntimeError(f"Runtime judge model {judge_model} unavailable and mock fallback disabled.")


def call_llm_judge(
    query: str,
    answer: str,
    evidence: Dict[str, Any],
    ground_truth: Dict[str, Any],
    judge_model: str = DEFAULT_JUDGE_MODEL,
    client: Optional[ollama.Client] = None,
    allow_mock_fallback: bool = True,
    deterministic_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Offline benchmark reference-based judge: uses ground-truth reference notes
    to grade model quality for dataset labeling and evaluation.
    Captures verified provider tokens and latency.
    """
    if client is None:
        try:
            client = ollama.Client(host=OLLAMA_HOST)
        except Exception:
            client = None

    prompt = OFFLINE_JUDGE_PROMPT_TEMPLATE.format(
        evidence_json=json.dumps(evidence, indent=2),
        query=query,
        answer=answer,
        expected_status=ground_truth.get("expected_status"),
        expected_numbers=ground_truth.get("expected_numbers", []),
        required_citations=ground_truth.get("required_citations", []),
        expected_explanation=ground_truth.get("expected_explanation"),
        allow_abstain=ground_truth.get("allow_abstain"),
    )

    retries = 0
    if client is not None:
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                response = client.chat(
                    model=judge_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.0, "seed": 42, "num_predict": 2048},
                )
                lat_ms = round((time.perf_counter() - t0) * 1000, 2)
                content = response.message.content.strip()

                if "```json" in content:
                    content = content.split("```json")[-1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[-1].split("```")[0].strip()

                parsed = json.loads(content)
                parsed["is_mock"] = False
                parsed["judge_model"] = judge_model
                parsed["prompt_tokens"] = getattr(response, "prompt_eval_count", None)
                parsed["completion_tokens"] = getattr(response, "eval_count", None)
                p_tok = parsed["prompt_tokens"] or 0
                c_tok = parsed["completion_tokens"] or 0
                parsed["total_tokens"] = (p_tok + c_tok) if (parsed["prompt_tokens"] is not None) else None
                parsed["latency_ms"] = lat_ms
                parsed["retries"] = retries
                return parsed
            except Exception as e:
                retries += 1
                logger.warning("Offline reference judge call to %s attempt %d failed (%s).", judge_model, attempt + 1, e)
                time.sleep(attempt * 2 + 1)

    if allow_mock_fallback:
        det = deterministic_result or {"deterministic_pass": True, "reasons": []}
        res = evaluate_with_mock_judge(query, answer, evidence, ground_truth, det)
        res["judge_model"] = f"{judge_model}_mock_fallback"
        return res

    raise RuntimeError(f"Judge model {judge_model} unavailable and mock fallback disabled.")


def combine_verifications(
    deterministic_result: Dict[str, Any],
    judge_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Combine code verification (runtime or offline) with LLM judge.
    Strict rule: BOTH must agree for a full PASS.
    """
    det_pass = deterministic_result.get("runtime_pass", deterministic_result.get("deterministic_pass", False))
    judge_pass = (judge_result.get("overall_verdict") == "PASS")

    overall_pass = det_pass and judge_pass

    disagreement = det_pass != judge_pass
    disagreement_type = None
    if disagreement:
        if det_pass and not judge_pass:
            disagreement_type = "FALSE_REJECTION_BY_JUDGE"
        else:
            disagreement_type = "FALSE_ACCEPTANCE_BY_JUDGE"

    return {
        "overall_pass": overall_pass,
        "deterministic_pass": det_pass,
        "judge_pass": judge_pass,
        "disagreement": disagreement,
        "disagreement_type": disagreement_type,
        "deterministic_reasons": deterministic_result.get("reasons", []),
        "judge_reasoning": judge_result.get("reasoning", ""),
        "scores": {
            "evidence_grounding": judge_result.get("evidence_grounding", 0),
            "factual_correctness": judge_result.get("factual_correctness", 0),
            "completeness": judge_result.get("completeness", 0),
            "uncertainty_calibration": judge_result.get("uncertainty_calibration", 0),
        }
    }
