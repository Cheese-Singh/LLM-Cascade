from __future__ import annotations

import unittest
from typing import Any

from phase3.tatqa_verifier import verify_tatqa


class TatqaVerifierTests(unittest.TestCase):
    def test_numeric_explanation_skips_years_before_the_answer(self) -> None:
        problem: dict[str, Any] = {
            "answer": -1161.33,
            "answer_type": "arithmetic",
            "scale": "thousand",
        }

        result = verify_tatqa(
            problem,
            "ANSWER: The average operating income (loss) for "
            "2017-2019 is approximately $ (1,159) thousand.\n"
            "CONFIDENCE: 1.00",
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["candidate"], -1159.0)
        self.assertEqual(result["reason"], "numeric_mismatch")

    def test_percentage_answer_is_not_confused_by_years_in_explanation(self) -> None:
        problem: dict[str, Any] = {
            "answer": 3.85,
            "answer_type": "arithmetic",
            "scale": "percent",
        }

        result = verify_tatqa(
            problem,
            "ANSWER: 3.85% increase in costs from 2018 to 2019.\n"
            "CONFIDENCE: 1.00",
        )

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["candidate"], 3.85)

    def test_change_answer_is_not_confused_by_years_and_source_values(self) -> None:
        problem: dict[str, Any] = {
            "answer": 52,
            "answer_type": "arithmetic",
            "scale": "thousand",
        }

        result = verify_tatqa(
            problem,
            "ANSWER: $52,000 increase (from $670,000 in 2018 "
            "to $722,000 in 2019).\nCONFIDENCE: 1.00",
        )

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["candidate"], 52000.0)

    def test_accounting_parentheses_verify_negative_answer(self) -> None:
        problem: dict[str, Any] = {
            "answer": -1161.33,
            "answer_type": "arithmetic",
            "scale": "thousand",
            "derivation": "(-2,235+(-6,986)+5,737)/3",
        }
        response = (
            "The operating income values are $5,737, $(6,986), "
            "and $(2,235). Their average is -1,161.33.\n\n"
            "ANSWER: $(1,161.33)\n"
            "CONFIDENCE: 1.00"
        )

        result = verify_tatqa(problem, response)

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["candidate"], -1161.33)
        self.assertEqual(
            result["candidate_sign_reason"],
            "accounting_parentheses",
        )

    def test_both_accounting_negative_currency_forms_are_negative(self) -> None:
        problem: dict[str, Any] = {
            "answer": -1161.33,
            "answer_type": "arithmetic",
            "scale": "thousand",
        }

        for answer in ("$(1,161.33)", "($1,161.33)", "(1,161.33)"):
            with self.subTest(answer=answer):
                result = verify_tatqa(
                    problem,
                    f"ANSWER: {answer}\nCONFIDENCE: 1.00",
                )
                self.assertTrue(result["passed"], result)
                self.assertEqual(result["candidate"], -1161.33)


if __name__ == "__main__":
    unittest.main()
