from __future__ import annotations

from collections import Counter
from typing import Any


def analyze(
    trace_records: list[dict[str, Any]],
    evaluation_records: list[dict[str, Any]],
) -> dict[str, Any]:
    layer_usage = Counter()
    oracle_usage = Counter()
    false_stop_layers = Counter()
    unnecessary_escalation_layers = Counter()

    confidence_by_layer = {}
    router_probability_by_layer = {}

    for record in trace_records:
        oracle_layer = record.get(
            "minimum_sufficient_layer"
        )

        if oracle_layer is not None:
            oracle_usage[
                f"L{oracle_layer}"
            ] += 1

        for layer_index, (
            layer_key,
            layer,
        ) in enumerate(
            record.get(
                "layers",
                {},
            ).items(),
            start=1,
        ):
            confidence_by_layer.setdefault(
                f"L{layer_index}",
                [],
            ).append(
                float(
                    layer.get(
                        "signals",
                        {},
                    ).get(
                        "confidence",
                        0.0,
                    )
                )
            )

    for result in evaluation_records:
        layer = result.get(
            "router_layer"
        )

        if layer is not None:
            layer_usage[
                f"L{layer}"
            ] += 1

        if result.get(
            "false_stop",
            False,
        ):
            false_stop_layers[
                f"L{layer}"
            ] += 1

        if result.get(
            "unnecessary_escalation",
            False,
        ):
            unnecessary_escalation_layers[
                f"L{layer}"
            ] += 1

        probability = result.get(
            "router_probability"
        )

        if layer is not None and probability is not None:
            router_probability_by_layer.setdefault(
                f"L{layer}",
                [],
            ).append(
                float(probability)
            )

    confidence_summary = {}

    for layer, values in (
        confidence_by_layer.items()
    ):
        confidence_summary[layer] = {
            "mean": (
                sum(values)
                / len(values)
            ),
            "minimum": min(values),
            "maximum": max(values),
            "samples": len(values),
        }

    router_probability_summary = {}

    for layer, values in (
        router_probability_by_layer.items()
    ):
        router_probability_summary[
            layer
        ] = {
            "mean": (
                sum(values)
                / len(values)
            ),
            "minimum": min(values),
            "maximum": max(values),
            "samples": len(values),
        }

    return {
        "oracle_layer_usage": dict(
            oracle_usage
        ),
        "router_layer_usage": dict(
            layer_usage
        ),
        "false_stop_by_layer": dict(
            false_stop_layers
        ),
        "unnecessary_escalation_by_layer": dict(
            unnecessary_escalation_layers
        ),
        "confidence_by_layer": (
            confidence_summary
        ),
        "router_probability_by_layer": (
            router_probability_summary
        ),
    }