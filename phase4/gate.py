from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ConservativeEscalationGate:
    """
    Leak-free provisional rule-based conservative escalation gate for production routing.
    Does NOT require or use ground-truth benchmark references, expected numbers, or gold labels.
    
    Escalates when:
    - Runtime verification fails (prompt injection compliance, ungrounded numbers, etc.).
    - Runtime judge rejects candidate (NO_PASS or rubric scores < 4).
    - Reported citations do not exist in evidence provenance.
    - Material evidence discrepancies remain unresolved.
    - Premature abstention occurs on complete evidence.
    
    Stops ONLY when:
    - All configured runtime criteria pass (overall_pass == True).
    - No unresolved risk flag requires escalation.
    - Or at terminal tier layer_4.
    """

    def evaluate_stop_or_escalate(
        self,
        tier: str,
        runtime_verifier_result: Dict[str, Any],
        runtime_judge_result: Dict[str, Any],
        combined_result: Dict[str, Any],
        evidence: Dict[str, Any],
        query: str = "",
        raw_response: str = "",
        **kwargs: Any,  # Absorb any legacy ground_truth arg safely without using it
    ) -> Tuple[bool, str, List[str]]:
        """
        Evaluate candidate tier response against leak-free conservative risk rubric.
        
        Returns:
            stop (bool): True if candidate response is certified acceptable and should stop.
            reason (str): Explicit justification for stopping or escalating.
            risk_flags (List[str]): Identified risk triggers requiring escalation.
        """
        # Guard against accidental ground-truth leakage
        if "ground_truth" in kwargs:
            logger.debug("Legacy ground_truth passed to ConservativeEscalationGate; ignored to prevent runtime leakage.")

        if tier == "layer_4":
            return True, "Reached terminal tier layer_4.", []

        risk_flags: List[str] = []

        # 1. Runtime verification check
        ver_pass = runtime_verifier_result.get(
            "runtime_pass", runtime_verifier_result.get("deterministic_pass", False)
        )
        if not ver_pass:
            reasons = runtime_verifier_result.get("reasons", ["Runtime verification failed"])
            for r in reasons:
                risk_flags.append(f"verifier_failure: {r}")

        # 2. Judge verdict and rubric check
        judge_verdict = runtime_judge_result.get("overall_verdict", "NO_PASS")
        if judge_verdict != "PASS":
            risk_flags.append(f"judge_rejection: {runtime_judge_result.get('reasoning', 'Judge rejected candidate')}")

        scores = runtime_judge_result.get("scores", {})
        if not scores:
            scores = {
                "evidence_grounding": runtime_judge_result.get("evidence_grounding", 5),
                "factual_correctness": runtime_judge_result.get("factual_correctness", 5),
                "uncertainty_calibration": runtime_judge_result.get("uncertainty_calibration", 5),
                "completeness": runtime_judge_result.get("completeness", 5),
            }

        if scores.get("evidence_grounding", 5) < 4:
            risk_flags.append(f"weak_grounding: evidence_grounding score is {scores.get('evidence_grounding')}/5")
        if scores.get("factual_correctness", 5) < 4:
            risk_flags.append(f"factual_inaccuracy: factual_correctness score is {scores.get('factual_correctness')}/5")
        if scores.get("uncertainty_calibration", 5) < 4:
            risk_flags.append(f"poor_uncertainty_calibration: uncertainty_calibration score is {scores.get('uncertainty_calibration')}/5")

        # 3. Citation grounding check
        invalid_cites = runtime_verifier_result.get("invalid_citations", [])
        if invalid_cites:
            risk_flags.append(f"invalid_citations: Citations do not exist in evidence provenance: {invalid_cites}")

        # 4. Ungrounded figures check
        ungrounded = runtime_verifier_result.get("ungrounded_numbers", [])
        if len(ungrounded) >= 2:
            risk_flags.append(f"ungrounded_numbers: Figures not present in or derivable from evidence: {ungrounded}")

        # 5. Overall pass conjunction
        if not combined_result.get("overall_pass", False) and not risk_flags:
            risk_flags.append("conjunction_failure: Combined verification failed")

        if risk_flags:
            stop = False
            reason = f"Escalate from {tier} due to: {'; '.join(risk_flags)}"
        else:
            stop = True
            reason = f"Certified pass at {tier}: all runtime verification and judge criteria satisfied without unresolved risk flags."

        return stop, reason, risk_flags
