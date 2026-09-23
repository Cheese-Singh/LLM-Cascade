from __future__ import annotations

import math
import re
from typing import Any, Callable

from sandbox import run_check


def verify_code(
    problem: dict[str, Any],
    candidate: str,
) -> dict[str, Any]:
    result = run_check(
        problem,
        candidate,
    )

    return {
        "passed": bool(result.get("passed", False)),
        "error": result.get("error", ""),
        "raw": result,
    }


def extract_numeric_answer(answer: str) -> float:
    text = answer.strip().replace(",", "")

    boxed_matches = re.findall(
        r"\\boxed\{([^{}]+)\}",
        text,
    )

    if boxed_matches:
        text = boxed_matches[-1]

    labeled_matches = re.findall(
        r"(?:final\s+answer|answer|therefore|thus|so)\s*"
        r"(?:is|=|:)?\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
        text,
        re.IGNORECASE,
    )

    if labeled_matches:
        return float(labeled_matches[-1])

    numeric_unit_matches = re.findall(
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?|"
        r"days?|years?|rupees?|dollars?|percent|%)",
        text,
        re.IGNORECASE,
    )

    if numeric_unit_matches:
        return float(numeric_unit_matches[-1])

    matches = re.findall(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",
        text,
    )

    if not matches:
        raise ValueError(
            f"Could not extract a numeric answer from: {answer!r}"
        )

    return float(matches[-1])


def verify_numeric(
    problem: dict[str, Any],
    answer: str,
) -> dict[str, Any]:
    if "answer" not in problem:
        raise ValueError(
            f"Problem '{problem.get('id')}' has no expected answer."
        )

    expected = float(problem["answer"])
    tolerance = float(problem.get("tolerance", 1e-4))

    try:
        actual = extract_numeric_answer(answer)
    except ValueError as exc:
        return {
            "passed": False,
            "error": str(exc),
            "raw": {
                "expected": expected,
                "tolerance": tolerance,
                "answer": answer,
            },
        }

    passed = math.isclose(
        actual,
        expected,
        rel_tol=tolerance,
        abs_tol=tolerance,
    )

    return {
        "passed": passed,
        "error": (
            ""
            if passed
            else (
                f"Expected {expected}, got {actual} "
                f"with tolerance {tolerance}."
            )
        ),
        "raw": {
            "expected": expected,
            "actual": actual,
            "tolerance": tolerance,
            "answer": answer,
        },
    }


def verify_math(
    problem: dict[str, Any],
    answer: str,
) -> dict[str, Any]:
    verifier = problem.get("verifier", "numeric")

    if verifier == "numeric":
        return verify_numeric(
            problem,
            answer,
        )

    raise ValueError(
        f"Unsupported math verifier '{verifier}' "
        f"for problem '{problem.get('id')}'."
    )


def verify_finance(
    problem: dict[str, Any],
    answer: str,
) -> dict[str, Any]:
    verifier = problem.get("verifier", "numeric")

    if verifier == "numeric":
        return verify_numeric(
            problem,
            answer,
        )

    raise ValueError(
        f"Unsupported finance verifier '{verifier}' "
        f"for problem '{problem.get('id')}'."
    )


VERIFIERS: dict[str, Callable[..., dict[str, Any]]] = {
    "code": verify_code,
    "math": verify_math,
    "finance": verify_finance,
}


def verify(
    domain: str,
    problem: dict[str, Any],
    candidate: str,
) -> dict[str, Any]:
    if domain not in VERIFIERS:
        raise ValueError(
            f"Unsupported verification domain: {domain}"
        )

    return VERIFIERS[domain](
        problem,
        candidate,
    )