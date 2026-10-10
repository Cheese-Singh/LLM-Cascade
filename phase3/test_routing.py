from __future__ import annotations

import unittest
from typing import Any

import numpy as np

from phase3.dataset import select_additional_training_records
from phase3.router import (
    build_training_matrix,
    choose_thresholds_for_cascade,
)
from phase3.signals import (
    extract_signals,
    final_answer_presence,
    signal_names,
)


class _ConfidenceRouter:
    def predict_proba(
        self,
        values: np.ndarray,
    ) -> np.ndarray:
        probabilities = values[:, 0]
        return np.column_stack(
            (1.0 - probabilities, probabilities)
        )


class RoutingTests(unittest.TestCase):
    def test_final_answer_presence_is_a_numeric_signal(self) -> None:
        self.assertEqual(
            final_answer_presence("ANSWER: 42"),
            1.0,
        )
        self.assertEqual(
            final_answer_presence("Reasoning without an answer label"),
            0.0,
        )
        self.assertEqual(
            final_answer_presence(" \n"),
            0.0,
        )

    def test_count_answer_type_has_its_own_feature(self) -> None:
        signals = extract_signals(
            "ANSWER: 3\nCONFIDENCE: 0.8",
            {"answer_type": "count"},
        )

        self.assertEqual(signals["is_arithmetic"], 0.0)
        self.assertEqual(signals["is_span"], 0.0)
        self.assertEqual(signals["is_count"], 1.0)
        self.assertIn("is_count", signal_names())

    def test_training_matrix_skips_empty_gold_and_blank_responses(self) -> None:
        records = [
            {
                "answer": 3,
                "answer_type": "count",
                "layers": {
                    "layer_1": {
                        "response": "ANSWER: 3\nCONFIDENCE: 0.9",
                        "correct": True,
                    }
                },
            },
            {
                "answer": "",
                "answer_type": "count",
                "layers": {
                    "layer_1": {
                        "response": "ANSWER: 3\nCONFIDENCE: 0.9",
                        "correct": True,
                    }
                },
            },
            {
                "answer": 3,
                "answer_type": "count",
                "layers": {
                    "layer_1": {
                        "response": " \n",
                        "correct": False,
                    }
                },
            },
        ]

        x, y = build_training_matrix(records, "layer_1")
        count_feature = signal_names().index("is_count")
        answer_feature = signal_names().index(
            "final_answer_presence"
        )

        self.assertEqual(x.shape[0], 1)
        self.assertEqual(y.tolist(), [1])
        self.assertEqual(x[0, count_feature], 1.0)
        self.assertEqual(x[0, answer_feature], 1.0)

    def test_training_matrix_prefers_complete_counterfactual_duplicate(self) -> None:
        records = [
            {
                "problem_id": "same-question",
                "answer": 3,
                "answer_type": "count",
                "layers": {
                    "layer_1": {
                        "response": "ANSWER: wrong\nCONFIDENCE: 0.2",
                        "correct": False,
                    }
                },
            },
            {
                "problem_id": "same-question",
                "answer": 3,
                "answer_type": "count",
                "collection_mode": "counterfactual",
                "layers": {
                    "layer_1": {
                        "response": "ANSWER: 3\nCONFIDENCE: 0.9",
                        "correct": True,
                    },
                    "layer_2": {"response": "ANSWER: 3"},
                    "layer_3": {"response": "ANSWER: 3"},
                    "layer_4": {"response": "ANSWER: 3"},
                },
            },
        ]

        x, y = build_training_matrix(records, "layer_1")

        self.assertEqual(x.shape[0], 1)
        self.assertEqual(y.tolist(), [1])

    def test_additional_training_selection_filters_and_excludes_ids(self) -> None:
        records = [
            {
                "id": "seen",
                "answer": "valid",
                "answer_type": "span",
            },
            {
                "id": "blank",
                "answer": "  ",
                "answer_type": "span",
            },
            {
                "id": "new-a",
                "answer": "valid",
                "answer_type": "count",
            },
            {
                "id": "new-b",
                "answer": "valid",
                "answer_type": "span",
            },
        ]

        first = select_additional_training_records(
            records,
            2,
            {"seen"},
            seed=42,
        )
        second = select_additional_training_records(
            records,
            2,
            {"seen"},
            seed=42,
        )

        self.assertEqual(
            [record["id"] for record in first],
            [record["id"] for record in second],
        )
        self.assertEqual(
            {record["id"] for record in first},
            {"new-a", "new-b"},
        )

    def test_cascade_thresholds_preserve_l4_accuracy_and_false_stop_limit(self) -> None:
        records: list[dict[str, Any]] = []
        for index in range(20):
            correct_layer = 4
            layers: dict[str, Any] = {}
            for layer_number in range(1, 5):
                layers[f"layer_{layer_number}"] = {
                    "response": f"ANSWER: {index}\nCONFIDENCE: 0.99",
                    "correct": layer_number >= correct_layer,
                    "total_tokens": 100 * layer_number,
                }
            records.append(
                {
                    "answer": index,
                    "answer_type": "count",
                    "minimum_sufficient_layer": correct_layer,
                    "layers": layers,
                }
            )

        thresholds = choose_thresholds_for_cascade(
            {
                f"layer_{layer_number}": _ConfidenceRouter()
                for layer_number in range(1, 5)
            },
            records,
            max_false_stop_rate=0.05,
        )

        self.assertTrue(
            all(
                layer["validation_accuracy"] >= 1.0
                for layer in thresholds.values()
            )
        )
        self.assertTrue(
            all(
                layer["false_stop_rate"] <= 0.05
                for layer in thresholds.values()
            )
        )

    def test_threshold_optimizer_prefers_safe_early_stops(self) -> None:
        records: list[dict[str, Any]] = []
        for index in range(20):
            layers: dict[str, Any] = {}
            for layer_number in range(1, 5):
                layers[f"layer_{layer_number}"] = {
                    "response": f"ANSWER: {index}\nCONFIDENCE: 0.99",
                    "correct": index < 19,
                    "total_tokens": 100 * layer_number,
                }
            records.append(
                {
                    "answer": index,
                    "answer_type": "count",
                    "minimum_sufficient_layer": 1 if index < 19 else None,
                    "layers": layers,
                }
            )

        thresholds = choose_thresholds_for_cascade(
            {
                f"layer_{layer_number}": _ConfidenceRouter()
                for layer_number in range(1, 5)
            },
            records,
            max_false_stop_rate=0.05,
        )

        self.assertEqual(
            thresholds["layer_1"]["threshold"],
            0.0,
        )
        self.assertEqual(
            thresholds["layer_1"]["mean_tokens"],
            100.0,
        )
        self.assertEqual(
            thresholds["layer_1"]["unnecessary_escalation_rate"],
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
