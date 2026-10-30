from __future__ import annotations

import unittest
from phase4.judge import call_llm_judge, combine_verifications, evaluate_with_mock_judge


class TestPhase4Judge(unittest.TestCase):
    def test_mock_judge_pass(self):
        det_res = {"deterministic_pass": True, "reasons": []}
        res = evaluate_with_mock_judge(
            query="Why did expense increase?",
            answer="GST was applied.",
            evidence={},
            ground_truth={},
            deterministic_result=det_res,
        )
        self.assertEqual(res["overall_verdict"], "PASS")
        self.assertEqual(res["evidence_grounding"], 5)

    def test_combine_verifications(self):
        det_pass = {"deterministic_pass": True, "reasons": []}
        judge_pass = {"overall_verdict": "PASS", "evidence_grounding": 5, "factual_correctness": 5, "completeness": 5, "uncertainty_calibration": 5}
        combined = combine_verifications(det_pass, judge_pass)
        self.assertTrue(combined["overall_pass"])
        self.assertFalse(combined["disagreement"])

        # Test Disagreement
        judge_fail = {"overall_verdict": "NO_PASS", "reasoning": "Missing detail"}
        combined_disag = combine_verifications(det_pass, judge_fail)
        self.assertFalse(combined_disag["overall_pass"])
        self.assertTrue(combined_disag["disagreement"])
        self.assertEqual(combined_disag["disagreement_type"], "FALSE_REJECTION_BY_JUDGE")


if __name__ == "__main__":
    unittest.main()
