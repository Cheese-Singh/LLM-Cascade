from __future__ import annotations

import unittest
from phase4.accounting import ModelCallRecord, CascadeResourceTracker


class TestAccounting(unittest.TestCase):
    def test_call_record_serialization(self):
        rec = ModelCallRecord(
            model="gpt-oss:20b-cloud",
            role="tier_generator",
            tier="layer_1",
            prompt_tokens=150,
            completion_tokens=65,
            total_tokens=215,
            wall_latency_ms=1250.5,
            execution_mode="genuine_ollama_call",
        )
        d = rec.to_dict()
        self.assertEqual(d["model"], "gpt-oss:20b-cloud")
        self.assertEqual(d["prompt_tokens"], 150)
        self.assertEqual(d["completion_tokens"], 65)
        self.assertEqual(d["total_tokens"], 215)
        self.assertTrue(d["tokens_provider_reported"])

    def test_missing_tokens_marked_unavailable_not_fabricated(self):
        rec = ModelCallRecord(
            model="gpt-oss:20b-cloud",
            role="tier_generator",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            wall_latency_ms=800.0,
            execution_mode="offline_deterministic",
        )
        d = rec.to_dict()
        self.assertIsNone(d["prompt_tokens"])
        self.assertIsNone(d["completion_tokens"])
        self.assertFalse(d["tokens_provider_reported"])

    def test_aggregation_preserves_judge_and_generator_calls(self):
        tracker = CascadeResourceTracker()

        # Call 1: Layer 1 generator
        tracker.record_call(ModelCallRecord(
            model="gpt-oss:20b-cloud",
            role="tier_generator",
            tier="layer_1",
            prompt_tokens=200,
            completion_tokens=80,
            total_tokens=280,
            wall_latency_ms=1000.0,
        ))

        # Call 2: Runtime Judge evaluating Layer 1
        tracker.record_call(ModelCallRecord(
            model="gpt-oss:120b-cloud",
            role="runtime_judge",
            prompt_tokens=350,
            completion_tokens=90,
            total_tokens=440,
            wall_latency_ms=1500.0,
        ))

        # Call 3: Escalated Layer 2 generator
        tracker.record_call(ModelCallRecord(
            model="gemma4:cloud",
            role="tier_generator",
            tier="layer_2",
            prompt_tokens=300,
            completion_tokens=110,
            total_tokens=410,
            wall_latency_ms=1800.0,
        ))

        # Call 4: Runtime Judge evaluating Layer 2
        tracker.record_call(ModelCallRecord(
            model="gpt-oss:120b-cloud",
            role="runtime_judge",
            prompt_tokens=400,
            completion_tokens=95,
            total_tokens=495,
            wall_latency_ms=1600.0,
        ))

        summary = tracker.get_summary()

        # Verify all calls are accounted for without silent omission
        self.assertEqual(summary["total_calls"], 4)
        self.assertEqual(summary["generator_calls"], 2)
        self.assertEqual(summary["runtime_judge_calls"], 2)

        # Generator tokens: 280 + 410 = 690
        # Judge tokens: 440 + 495 = 935
        # Total tokens: 690 + 935 = 1625
        self.assertEqual(summary["prompt_tokens"], 200 + 350 + 300 + 400)
        self.assertEqual(summary["completion_tokens"], 80 + 90 + 110 + 95)
        self.assertEqual(summary["total_tokens"], 1625)

        # Verify latency separation
        self.assertEqual(summary["tier_model_latency_ms"], 2800.0)
        self.assertEqual(summary["runtime_judge_latency_ms"], 3100.0)

        # Verify no fabricated cost
        self.assertFalse(summary["cost_reported"])

    def test_retries_and_errors_tracked(self):
        tracker = CascadeResourceTracker()
        tracker.record_call(ModelCallRecord(
            model="gpt-oss:20b-cloud",
            role="tier_generator",
            retries=2,
            error="Connection timeout on attempt 1",
            wall_latency_ms=2500.0,
        ))
        summary = tracker.get_summary()
        self.assertEqual(summary["total_retries"], 2)
        self.assertEqual(summary["total_failures"], 1)


if __name__ == "__main__":
    unittest.main()
