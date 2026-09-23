from __future__ import annotations

from statistics import median
from typing import Any


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0

    return sum(values) / len(values)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0

    return float(median(values))


def _numeric_values(
    records: list[dict[str, Any]],
    key: str,
) -> list[float]:
    values = []

    for record in records:
        value = record.get(key)

        if isinstance(value, (int, float)):
            values.append(float(value))

    return values


def _bool_rate(
    records: list[dict[str, Any]],
    key: str,
) -> float:
    if not records:
        return 0.0

    values = [
        bool(record.get(key, False))
        for record in records
    ]

    return sum(values) / len(values)


def summarize_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not records:
        return {
            "n": 0,
            "accuracy": 0.0,
            "correct": 0,
            "incorrect": 0,
            "mean_tokens": 0.0,
            "median_tokens": 0.0,
            "total_tokens": 0,
            "mean_latency_ms": 0.0,
            "median_latency_ms": 0.0,
            "escalation_rate": 0.0,
            "strong_invocation_rate": 0.0,
        }

    correct = sum(
        int(
            record.get(
                "final_correct",
                record.get(
                    "correct",
                    False,
                ),
            )
        )
        for record in records
    )

    total_token_values = _numeric_values(
        records,
        "total_tokens",
    )

    latency_values = _numeric_values(
        records,
        "total_latency_ms",
    )

    return {
        "n": len(records),
        "accuracy": correct / len(records),
        "correct": correct,
        "incorrect": len(records) - correct,
        "mean_tokens": _mean(
            total_token_values
        ),
        "median_tokens": _median(
            total_token_values
        ),
        "total_tokens": int(
            sum(total_token_values)
        ),
        "mean_latency_ms": _mean(
            latency_values
        ),
        "median_latency_ms": _median(
            latency_values
        ),
        "escalation_rate": _bool_rate(
            records,
            "escalated",
        ),
        "strong_invocation_rate": _bool_rate(
            records,
            "strong_invoked",
        ),
    }


def summarize_run_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not records:
        return {
            "n": 0,
            "weak_accuracy": 0.0,
            "final_accuracy": 0.0,
            "error_recovery_rate": 0.0,
            "escalation_rate": 0.0,
            "total_tokens": 0,
            "mean_tokens": 0.0,
            "median_tokens": 0.0,
            "mean_latency_ms": 0.0,
            "median_latency_ms": 0.0,
        }

    weak_correct = sum(
        int(record.get("weak_correct", False))
        for record in records
    )

    final_correct = sum(
        int(record.get("final_correct", False))
        for record in records
    )

    escalated = sum(
        int(record.get("escalated", False))
        for record in records
    )

    recovered = sum(
        1
        for record in records
        if (
            record.get("weak_correct", False)
            is False
            and record.get("final_correct", False)
            is True
        )
    )

    recoverable_errors = sum(
        1
        for record in records
        if record.get(
            "weak_correct",
            False,
        )
        is False
    )

    token_values = _numeric_values(
        records,
        "total_tokens",
    )

    latency_values = _numeric_values(
        records,
        "total_latency_ms",
    )

    return {
        "n": len(records),
        "weak_accuracy": (
            weak_correct / len(records)
        ),
        "final_accuracy": (
            final_correct / len(records)
        ),
        "error_recovery_rate": (
            recovered / recoverable_errors
            if recoverable_errors
            else 0.0
        ),
        "escalation_rate": (
            escalated / len(records)
        ),
        "total_tokens": int(
            sum(token_values)
        ),
        "mean_tokens": _mean(
            token_values
        ),
        "median_tokens": _median(
            token_values
        ),
        "mean_latency_ms": _mean(
            latency_values
        ),
        "median_latency_ms": _median(
            latency_values
        ),
    }


def summarize_counterfactual_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not records:
        return {
            "n": 0,
            "layers": {},
            "minimum_sufficient_layer_counts": {},
            "minimum_sufficient_layer_rates": {},
            "all_layers_failed": 0,
            "beneficial_escalations": 0,
            "beneficial_escalation_rate": 0.0,
            "oracle_cascade_accuracy": 0.0,
            "oracle_cascade_mean_tokens": 0.0,
            "oracle_cascade_median_tokens": 0.0,
            "oracle_cascade_total_tokens": 0,
            "oracle_cascade_mean_latency_ms": 0.0,
            "oracle_cascade_median_latency_ms": 0.0,
            "oracle_cascade_total_latency_ms": 0.0,
        }

    layer_metrics: dict[str, Any] = {}

    for layer_number in range(1, 5):
        layer_key = f"layer_{layer_number}"

        layer_records = [
            record
            for record in records
            if isinstance(
                record.get("layers"),
                dict,
            )
            and isinstance(
                record["layers"].get(layer_key),
                dict,
            )
        ]

        correct = sum(
            int(
                record["layers"][layer_key].get(
                    "correct",
                    False,
                )
            )
            for record in layer_records
        )

        token_values = [
            float(
                record["layers"][layer_key].get(
                    "total_tokens",
                    0,
                )
            )
            for record in layer_records
            if isinstance(
                record["layers"][layer_key].get(
                    "total_tokens",
                    0,
                ),
                (int, float),
            )
        ]

        latency_values = [
            float(
                record["layers"][layer_key].get(
                    "latency_ms",
                    0.0,
                )
            )
            for record in layer_records
            if isinstance(
                record["layers"][layer_key].get(
                    "latency_ms",
                    0.0,
                ),
                (int, float),
            )
        ]

        layer_metrics[layer_key] = {
            "n": len(layer_records),
            "accuracy": (
                correct / len(layer_records)
                if layer_records
                else 0.0
            ),
            "correct": correct,
            "incorrect": (
                len(layer_records) - correct
            ),
            "mean_tokens": _mean(
                token_values
            ),
            "median_tokens": _median(
                token_values
            ),
            "total_tokens": int(
                sum(token_values)
            ),
            "mean_latency_ms": _mean(
                latency_values
            ),
            "median_latency_ms": _median(
                latency_values
            ),
            "total_latency_ms": sum(
                latency_values
            ),
        }

    minimum_counts = {
        str(layer): 0
        for layer in range(1, 5)
    }

    minimum_counts["none"] = 0

    beneficial_escalations = 0

    oracle_token_values = []
    oracle_latency_values = []

    for record in records:
        minimum_layer = record.get(
            "minimum_sufficient_layer"
        )

        if minimum_layer is None:
            minimum_counts["none"] += 1
            continue

        minimum_key = str(
            minimum_layer
        )

        if minimum_key not in minimum_counts:
            minimum_counts[minimum_key] = 0

        minimum_counts[minimum_key] += 1

        if record.get(
            "beneficial_escalation",
            False,
        ):
            beneficial_escalations += 1

        layers = record.get(
            "layers",
            {},
        )

        cumulative_tokens = 0.0
        cumulative_latency = 0.0

        for layer_number in range(
            1,
            minimum_layer + 1,
        ):
            layer_key = f"layer_{layer_number}"
            layer = layers.get(
                layer_key,
                {},
            )

            cumulative_tokens += float(
                layer.get(
                    "total_tokens",
                    0,
                )
            )

            cumulative_latency += float(
                layer.get(
                    "latency_ms",
                    0.0,
                )
            )

        oracle_token_values.append(
            cumulative_tokens
        )

        oracle_latency_values.append(
            cumulative_latency
        )

    solved = sum(
        value
        for key, value in minimum_counts.items()
        if key != "none"
    )

    minimum_rates = {
        key: value / len(records)
        for key, value in minimum_counts.items()
    }

    return {
        "n": len(records),
        "layers": layer_metrics,
        "minimum_sufficient_layer_counts": (
            minimum_counts
        ),
        "minimum_sufficient_layer_rates": (
            minimum_rates
        ),
        "all_layers_failed": minimum_counts[
            "none"
        ],
        "all_layers_failed_rate": (
            minimum_counts["none"] / len(records)
        ),
        "beneficial_escalations": (
            beneficial_escalations
        ),
        "beneficial_escalation_rate": (
            beneficial_escalations / len(records)
        ),
        "oracle_cascade_accuracy": (
            solved / len(records)
        ),
        "oracle_cascade_mean_tokens": _mean(
            oracle_token_values
        ),
        "oracle_cascade_median_tokens": _median(
            oracle_token_values
        ),
        "oracle_cascade_total_tokens": int(
            sum(oracle_token_values)
        ),
        "oracle_cascade_mean_latency_ms": _mean(
            oracle_latency_values
        ),
        "oracle_cascade_median_latency_ms": _median(
            oracle_latency_values
        ),
        "oracle_cascade_total_latency_ms": (
            sum(oracle_latency_values)
        ),
    }


def compare_record_sets(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_metrics = summarize_records(
        baseline
    )

    candidate_metrics = summarize_records(
        candidate
    )

    baseline_accuracy = baseline_metrics[
        "accuracy"
    ]

    candidate_accuracy = candidate_metrics[
        "accuracy"
    ]

    baseline_tokens = baseline_metrics[
        "mean_tokens"
    ]

    candidate_tokens = candidate_metrics[
        "mean_tokens"
    ]

    return {
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "accuracy_delta": (
            candidate_accuracy
            - baseline_accuracy
        ),
        "mean_token_delta": (
            candidate_tokens
            - baseline_tokens
        ),
        "mean_token_ratio": (
            candidate_tokens / baseline_tokens
            if baseline_tokens
            else None
        ),
        "token_overhead_rate": (
            (
                candidate_tokens
                - baseline_tokens
            )
            / baseline_tokens
            if baseline_tokens
            else None
        ),
    }