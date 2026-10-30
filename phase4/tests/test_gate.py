from __future__ import annotations

import unittest
from phase4.gate import ConservativeEscalationGate


class TestConservativeEscalationGate(unittest.TestCase):
    def setUp(self):
        self.gate = ConservativeEscalationGate()

    def test_escalates_on_verifier_failure_no_ground_truth(self):
        ver_res = {"runtime_pass": False, "reasons": ["Ungrounded numbers cited in response"]}
        judge_res = {"overall_verdict": "PASS", "scores": {"evidence_grounding": 5, "factual_correctness": 5}}
        comb = {"overall_pass": False}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_1",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={"invoice": {"invoice_total": 50000.0}},
            query="Verify invoice total",
            raw_response="Invoice total is 99999.00",
        )
        self.assertFalse(stop)
        self.assertTrue(any("verifier_failure" in r for r in risks))

    def test_escalates_on_judge_rejection_no_ground_truth(self):
        ver_res = {"runtime_pass": True, "reasons": []}
        judge_res = {"overall_verdict": "NO_PASS", "reasoning": "Missing reconciliation logic"}
        comb = {"overall_pass": False}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_1",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={},
            query="Verify tax",
            raw_response="Some answer",
        )
        self.assertFalse(stop)
        self.assertTrue(any("judge_rejection" in r for r in risks))

    def test_escalates_on_weak_rubric_scores(self):
        ver_res = {"runtime_pass": True, "reasons": []}
        judge_res = {
            "overall_verdict": "PASS",
            "scores": {"evidence_grounding": 3, "factual_correctness": 5, "uncertainty_calibration": 5},
        }
        comb = {"overall_pass": True}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_1",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={},
            query="Query",
            raw_response="Answer",
        )
        self.assertFalse(stop)
        self.assertTrue(any("weak_grounding" in r for r in risks))

    def test_escalates_on_invalid_citations(self):
        ver_res = {"runtime_pass": False, "reasons": ["Invalid / hallucinated citations reported: ['prov_fake']"], "invalid_citations": ["prov_fake"]}
        judge_res = {"overall_verdict": "PASS", "scores": {"evidence_grounding": 5, "factual_correctness": 5}}
        comb = {"overall_pass": False}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_1",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={},
            query="Query",
            raw_response="Answer with prov_fake",
        )
        self.assertFalse(stop)
        self.assertTrue(any("invalid_citations" in r for r in risks))

    def test_stops_when_all_runtime_checks_pass(self):
        ver_res = {"runtime_pass": True, "reasons": []}
        judge_res = {"overall_verdict": "PASS", "scores": {"evidence_grounding": 5, "factual_correctness": 5}}
        comb = {"overall_pass": True}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_1",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={},
            query="Query",
            raw_response="Perfect answer",
        )
        self.assertTrue(stop)
        self.assertEqual(len(risks), 0)
        self.assertIn("Certified pass at layer_1", reason)

    def test_terminal_layer_4_always_stops(self):
        ver_res = {"runtime_pass": False, "reasons": ["Some error"]}
        judge_res = {"overall_verdict": "NO_PASS"}
        comb = {"overall_pass": False}
        stop, reason, risks = self.gate.evaluate_stop_or_escalate(
            tier="layer_4",
            runtime_verifier_result=ver_res,
            runtime_judge_result=judge_res,
            combined_result=comb,
            evidence={},
            query="Query",
            raw_response="Terminal tier answer",
        )
        self.assertTrue(stop)
        self.assertIn("terminal tier", reason)


if __name__ == "__main__":
    unittest.main()
