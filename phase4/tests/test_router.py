from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from phase4.router import (
    Phase4CascadeRouter,
    Phase4DecisionTreeRouter,
    Phase4HybridRouter,
    extract_layer_data,
    calibrate_layer_threshold,
)
from phase4.signals import extract_signals, signal_vector


class TestPhase4Router(unittest.TestCase):
    def test_signals_extraction(self):
        resp = "The ₹14,160.00 total is due to 18% GST (CGST and SGST). Discrepancy explained with prov_1."
        prob = {"query": "Explain discrepancy", "evidence": {}}
        signals = extract_signals(resp, prob)
        self.assertIn("stated_confidence", signals)
        self.assertIn("audit_kw_density", signals)
        self.assertGreater(signals["audit_kw_density"], 0.0)
        vec = signal_vector(signals)
        self.assertEqual(len(vec), 8)

    def test_router_stopping(self):
        router = Phase4CascadeRouter()
        # Layer 4 always stops
        stop, prob = router.should_stop("layer_4", {})
        self.assertTrue(stop)
        self.assertEqual(prob, 1.0)

    def test_extract_layer_data_genuine_format(self):
        record = {
            "query_id": "TEST_001",
            "tier_evaluations": {
                "layer_1": {
                    "runtime_feature_vector": [0.95, 0.0, 0.1, 0.2, 0.5, 0.6, 0.0, 0.0],
                    "offline_overall_pass": True,
                }
            }
        }
        vec, passed = extract_layer_data(record, "layer_1")
        self.assertIsNotNone(vec)
        self.assertEqual(len(vec), 8)
        self.assertEqual(passed, 1)

    def test_extract_layer_data_legacy_format(self):
        record = {
            "query_id": "TEST_002",
            "layers": {
                "layer_1": {
                    "signals": {"stated_confidence": 0.8},
                    "passed": False,
                }
            }
        }
        vec, passed = extract_layer_data(record, "layer_1")
        self.assertIsNotNone(vec)
        self.assertEqual(len(vec), 8)
        self.assertEqual(passed, 0)

    def test_hybrid_router_escalation_on_verifier_failure(self):
        mock_ml_router = Phase4CascadeRouter()
        # Set threshold to 0.0 so ML router would always stop
        mock_ml_router.thresholds["layer_1"] = 0.0
        hybrid = Phase4HybridRouter(mock_ml_router)

        # When runtime verifier fails, hybrid router must force escalation
        fail_verifier = {"runtime_pass": False, "reasons": ["Ungrounded number"]}
        stop, prob = hybrid.should_stop("layer_1", {"stated_confidence": 1.0}, runtime_verifier_result=fail_verifier)
        self.assertFalse(stop)
        self.assertEqual(prob, 0.0)

        # When runtime verifier passes, hybrid router respects ML router
        pass_verifier = {"runtime_pass": True, "reasons": []}
        stop, prob = hybrid.should_stop("layer_1", {"stated_confidence": 1.0}, runtime_verifier_result=pass_verifier)
        self.assertTrue(stop)

    def test_decision_tree_router_train_and_rules(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dt_router = Phase4DecisionTreeRouter(routers_dir=Path(tmp_dir), max_depth=2)
            train_recs = [
                {
                    "tier_evaluations": {
                        "layer_1": {
                            "runtime_feature_vector": [0.95, 0.0, 0.1, 0.2, 1.0, 0.6, 0.0, 0.0],
                            "offline_overall_pass": True,
                        }
                    }
                },
                {
                    "tier_evaluations": {
                        "layer_1": {
                            "runtime_feature_vector": [0.40, 0.8, 0.0, 0.1, 0.0, 0.2, 0.9, 0.0],
                            "offline_overall_pass": False,
                        }
                    }
                },
            ]
            val_recs = train_recs  # for smoke test
            summary = dt_router.train_on_data(train_recs, val_recs)
            self.assertIn("layer_1", summary)
            self.assertIn("rules", summary["layer_1"])
            self.assertIn("stated_confidence", summary["layer_1"]["rules"])

            # Test stopping logic
            stop, prob = dt_router.should_stop("layer_1", {"stated_confidence": 0.95, "citation_score": 1.0})
            self.assertIsInstance(stop, (bool, np.bool_))
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)


if __name__ == "__main__":
    unittest.main()

