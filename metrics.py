from __future__ import annotations

from statistics import mean, median
from typing import Any


def safe_mean(values: list[float | int]) -> float:
    if not values:
        return 0.0
    return float(mean(values))


def safe_median(values: list[float | int]) -> float:
    if not values:
        return 0.0
    return float(median(values))


def accuracy(values: list[bool | None]) -> float:
    valid = [value for value in values if value is not None]
    if not valid:
        return 0.0
    return sum(valid) / len(valid)


def error_rate(values: list[bool | None]) -> float:
    return 1.0 - accuracy(values)


def escalation_rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def error_recovery_rate(
    weak_correct: list[bool | None],
    final_correct: list[bool | None],
) -> float:
    eligible = [
        final
        for weak, final in zip(weak_correct, final_correct)
        if weak is False and final is not None
    ]

    if not eligible:
        return 0.0

    return sum(eligible) / len(eligible)


def false_acceptance_rate(
    weak_correct: list[bool | None],
    verification_passed: list[bool | None],
) -> float:
    eligible = [
        verification
        for weak, verification in zip(
            weak_correct,
            verification_passed,
        )
        if weak is False and verification is not None
    ]

    if not eligible:
        return 0.0

    return sum(eligible) / len(eligible)


def total_tokens(records: list[dict[str, Any]]) -> int:
    return int(
        sum(
            int(record.get("total_tokens", 0) or 0)
            for record in records
        )
    )


def mean_tokens(records: list[dict[str, Any]]) -> float:
    values = [
        int(record.get("total_tokens", 0) or 0)
        for record in records
    ]
    return safe_mean(values)


def median_tokens(records: list[dict[str, Any]]) -> float:
    values = [
        int(record.get("total_tokens", 0) or 0)
        for record in records
    ]
    return safe_median(values)


def tokens_per_correct_answer(
    records: list[dict[str, Any]],
) -> float:
    total = total_tokens(records)
    correct = sum(
        1
        for record in records
        if record.get("correct") is True
    )

    if correct == 0:
        return 0.0

    return total / correct


def mean_latency(records: list[dict[str, Any]]) -> float:
    values = [
        float(record.get("latency_ms", 0.0) or 0.0)
        for record in records
    ]
    return safe_mean(values)


def median_latency(records: list[dict[str, Any]]) -> float:
    values = [
        float(record.get("latency_ms", 0.0) or 0.0)
        for record in records
    ]
    return safe_median(values)


def percentile(
    values: list[float | int],
    percentile_value: float,
) -> float:
    if not values:
        return 0.0

    if not 0.0 <= percentile_value <= 100.0:
        raise ValueError(
            "percentile_value must be between 0 and 100."
        )

    sorted_values = sorted(float(value) for value in values)

    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (
        percentile_value / 100.0
    ) * (len(sorted_values) - 1)

    lower = int(position)
    upper = min(
        lower + 1,
        len(sorted_values) - 1,
    )

    weight = position - lower

    return (
        sorted_values[lower]
        + weight
        * (
            sorted_values[upper]
            - sorted_values[lower]
        )
    )


def latency_percentile(
    records: list[dict[str, Any]],
    percentile_value: float,
) -> float:
    values = [
        float(record.get("latency_ms", 0.0) or 0.0)
        for record in records
    ]
    return percentile(
        values,
        percentile_value,
    )


def strong_invocation_rate(
    records: list[dict[str, Any]],
    strong_model: str | None = None,
) -> float:
    if not records:
        return 0.0

    if strong_model is None:
        strong_count = sum(
            1
            for record in records
            if record.get("escalated") is True
        )
    else:
        strong_count = sum(
            1
            for record in records
            if record.get("model") == strong_model
        )

    return strong_count / len(records)


def summarize_records(
    records: list[dict[str, Any]],
) -> dict[str, float | int]:
    correct_values = [
        record.get("correct")
        for record in records
    ]

    return {
        "count": len(records),
        "accuracy": accuracy(correct_values),
        "error_rate": error_rate(correct_values),
        "total_tokens": total_tokens(records),
        "mean_tokens": mean_tokens(records),
        "median_tokens": median_tokens(records),
        "tokens_per_correct_answer": tokens_per_correct_answer(
            records
        ),
        "mean_latency_ms": mean_latency(records),
        "median_latency_ms": median_latency(records),
        "p95_latency_ms": latency_percentile(
            records,
            95.0,
        ),
        "p99_latency_ms": latency_percentile(
            records,
            99.0,
        ),
        "strong_invocation_rate": strong_invocation_rate(
            records
        ),
    }


def summarize_run_records(
    records: list[dict[str, Any]],
) -> dict[str, float | int]:
    weak_values = [
        record.get("weak_correct")
        for record in records
    ]

    final_values = [
        record.get("final_correct")
        for record in records
    ]

    escalations = [
        bool(record.get("escalated", False))
        for record in records
    ]

    weak_correct_values = [
        record.get("weak_correct")
        for record in records
    ]

    final_correct_values = [
        record.get("final_correct")
        for record in records
    ]

    total_token_values = [
        int(record.get("total_tokens", 0) or 0)
        for record in records
    ]

    total_latency_values = [
        float(record.get("total_latency_ms", 0.0) or 0.0)
        for record in records
    ]

    return {
        "count": len(records),
        "weak_accuracy": accuracy(weak_values),
        "final_accuracy": accuracy(final_values),
        "error_recovery_rate": error_recovery_rate(
            weak_correct_values,
            final_correct_values,
        ),
        "escalation_rate": escalation_rate(escalations),
        "total_tokens": int(sum(total_token_values)),
        "mean_tokens": safe_mean(total_token_values),
        "median_tokens": safe_median(total_token_values),
        "mean_latency_ms": safe_mean(total_latency_values),
        "median_latency_ms": safe_median(
            total_latency_values
        ),
        "p95_latency_ms": percentile(
            total_latency_values,
            95.0,
        ),
        "p99_latency_ms": percentile(
            total_latency_values,
            99.0,
        ),
    }