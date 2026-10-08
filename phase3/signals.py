from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


CONFIDENCE_PATTERN = re.compile(
    r"CONFIDENCE\s*:\s*([01](?:\.\d+)?)",
    re.IGNORECASE,
)

NUMBER_PATTERN = re.compile(
    r"[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:%|[a-zA-Z]+)?"
)


def parse_confidence(
    response: str,
) -> float:
    matches = CONFIDENCE_PATTERN.findall(
        response
    )

    if not matches:
        return 0.0

    try:
        value = float(matches[-1])
    except ValueError:
        return 0.0

    return max(
        0.0,
        min(1.0, value),
    )


def extract_final_answer_text(
    response: str,
) -> str:
    lines = [
        line.strip()
        for line in response.splitlines()
        if line.strip()
    ]

    filtered = [
        line
        for line in lines
        if not line.upper().startswith(
            "CONFIDENCE:"
        )
    ]

    if not filtered:
        return ""

    for line in reversed(filtered):
        if any(
            marker in line.lower()
            for marker in (
                "final answer",
                "answer:",
                "answer =",
            )
        ):
            return line

    return filtered[-1]


def normalize_text(
    text: str,
) -> str:
    text = text.lower()
    text = re.sub(
        r"[^a-z0-9.%-]+",
        " ",
        text,
    )
    return " ".join(text.split())


def answer_similarity(
    current_response: str,
    previous_response: str | None,
) -> float:
    if not previous_response:
        return 0.0

    current = normalize_text(
        extract_final_answer_text(
            current_response
        )
    )

    previous = normalize_text(
        extract_final_answer_text(
            previous_response
        )
    )

    if not current or not previous:
        return 0.0

    return float(
        SequenceMatcher(
            None,
            current,
            previous,
        ).ratio()
    )


def numeric_density(
    response: str,
) -> float:
    if not response:
        return 0.0

    numbers = NUMBER_PATTERN.findall(
        response
    )

    return min(
        1.0,
        len(numbers) / max(
            1,
            len(response.split()),
        ),
    )


def response_length_score(
    response: str,
) -> float:
    length = len(
        response.split()
    )

    return min(
        1.0,
        length / 300.0,
    )


def reasoning_structure_score(
    response: str,
) -> float:
    if not response:
        return 0.0

    lower = response.lower()

    indicators = (
        "therefore",
        "thus",
        "because",
        "calculate",
        "calculation",
        "step",
        "equals",
        "=",
        "%",
    )

    count = sum(
        indicator in lower
        for indicator in indicators
    )

    return min(
        1.0,
        count / 4.0,
    )


def final_answer_presence(
    response: str,
) -> float:
    if not response.strip():
        return 0.0

    lower = response.lower()

    markers = (
        "final answer",
        "answer:",
        "answer =",
    )

HEDGING_INDICATORS = (
    "approximately",
    "approx",
    "estimated",
    "estimate",
    "unclear",
    "assuming",
    "assumption",
    "cannot determine",
    "cannot be determined",
    "uncertain",
    "roughly",
    "likely",
    "probably",
    "might be",
)


def hedging_score(
    response: str,
) -> float:
    if not response:
        return 0.0

    lower = response.lower()
    count = sum(
        1
        for indicator in HEDGING_INDICATORS
        if indicator in lower
    )

    return min(1.0, count / 2.0)


def answer_conciseness(
    response: str,
) -> float:
    ans_text = extract_final_answer_text(response)
    if not ans_text:
        return 0.0
    words = len(ans_text.split())
    if words <= 10:
        return 1.0
    return max(0.0, 1.0 - (words - 10) / 25.0)


def answer_type_signals(
    problem: dict[str, Any],
) -> tuple[float, float]:
    atype = str(problem.get("answer_type", "")).lower()
    is_arithmetic = 1.0 if atype in ("arithmetic", "numeric", "float") else 0.0
    is_span = 1.0 if atype in ("span", "multi-span", "multispan", "text") else 0.0
    return is_arithmetic, is_span


def extract_signals(
    response: str,
    problem: dict[str, Any],
    previous_response: str | None = None,
) -> dict[str, float]:
    confidence = parse_confidence(
        response
    )

    similarity = answer_similarity(
        response,
        previous_response,
    )

    is_arithmetic, is_span = answer_type_signals(
        problem
    )

    return {
        "confidence": confidence,
        "answer_similarity_previous": similarity,
        "response_length_score": response_length_score(
            response
        ),
        "numeric_density": numeric_density(
            response
        ),
        "reasoning_structure_score": reasoning_structure_score(
            response
        ),
        "final_answer_presence": final_answer_presence(
            response
        ),
        "is_arithmetic": is_arithmetic,
        "is_span": is_span,
        "hedging_score": hedging_score(
            response
        ),
        "answer_conciseness": answer_conciseness(
            response
        ),
    }


def signal_names() -> list[str]:
    return [
        "confidence",
        "answer_similarity_previous",
        "response_length_score",
        "numeric_density",
        "reasoning_structure_score",
        "final_answer_presence",
        "is_arithmetic",
        "is_span",
        "hedging_score",
        "answer_conciseness",
    ]


def signal_vector(
    signals: dict[str, Any],
) -> list[float]:
    values = []

    for name in signal_names():
        value = signals.get(
            name,
            0.0,
        )

        try:
            value = float(value)
        except (
            TypeError,
            ValueError,
        ):
            value = 0.0

        values.append(value)

    return values