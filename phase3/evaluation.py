from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from phase3.config import LAYER_KEYS
from phase3.router import (
    load_routers,
    load_thresholds,
    predict_correctness,
)


def load_records(
    path: Path,
) -> list[dict[str, Any]]:
    records = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line in handle:
            line = line.strip()

            if line:
                records.append(
                    json.loads(line)
                )

    return records


def _mean(
    values: list[float],
) -> float:
    if not values:
        return 0.0

    return float(
        statistics.mean(values)
    )


def _median(
    values: list[float],
) -> float:
    if not values:
        return 0.0

    return float(
        statistics.median(values)
    )


def _layer_number(
    layer_key: str,
) -> int:
    return int(
        layer_key.split("_")[-1]
    )


def _cumulative_tokens(
    record: dict[str, Any],
    layer_number: int,
) -> int:
    total = 0

    for number in range(
        1,
        layer_number + 1,
    ):
        layer = record.get(
            "layers",
            {},
        ).get(
            f"layer_{number}"
        )

        if layer:
            total += int(
                layer.get(
                    "total_tokens",
                    0,
                )
                or 0
            )

    return total


def _cumulative_latency(
    record: dict[str, Any],
    layer_number: int,
) -> float:
    total = 0.0

    for number in range(
        1,
        layer_number + 1,
    ):
        layer = record.get(
            "layers",
            {},
        ).get(
            f"layer_{number}"
        )

        if layer:
            total += float(
                layer.get(
                    "latency_ms",
                    0.0,
                )
                or 0.0
            )

    return total


def _simulate_record(
    record: dict[str, Any],
    routers: dict[str, Any],
    thresholds: dict[str, dict[str, float]],
) -> dict[str, Any]:
    layers = record.get(
        "layers",
        {},
    )

    router_stop_layer = len(
        LAYER_KEYS
    )

    stop_probability = None

    for layer_key in LAYER_KEYS:
        layer = layers.get(
            layer_key
        )

        if not layer:
            continue

        router = routers.get(
            layer_key
        )

        if router is None:
            continue

        threshold_config = thresholds.get(
            layer_key,
            {},
        )

        if isinstance(
            threshold_config,
            dict,
        ):
            threshold = float(
                threshold_config.get(
                    "threshold",
                    1.0,
                )
            )
        else:
            threshold = float(
                threshold_config
            )

        signals = layer.get(
            "signals",
            {},
        )

        if "is_arithmetic" not in signals:
            from phase3.signals import extract_signals
            layer_idx = int(layer_key.split("_")[-1]) - 1
            prev_resp = None
            if layer_idx > 0:
                prev_layer = layers.get(f"layer_{layer_idx}")
                if prev_layer:
                    prev_resp = prev_layer.get("response")
            signals = extract_signals(
                response=layer.get("response", ""),
                problem=record,
                previous_response=prev_resp,
            )
            layer["signals"] = signals

        probability = predict_correctness(
            router,
            signals,
        )

        if probability >= threshold:
            router_stop_layer = _layer_number(
                layer_key
            )
            stop_probability = probability
            break

    final_layer = layers.get(
        f"layer_{router_stop_layer}"
    )

    final_correct = bool(
        final_layer.get(
            "correct",
            False,
        )
        if final_layer
        else False
    )

    oracle_layer = record.get(
        "minimum_sufficient_layer"
    )

    return {
        "router_stop_layer": (
            router_stop_layer
        ),
        "final_correct": final_correct,
        "oracle_layer": oracle_layer,
        "stop_probability": (
            stop_probability
        ),
    }


def evaluate_router(
    records: list[dict[str, Any]],
    routers: dict[str, Any],
    thresholds: dict[str, dict[str, float]],
) -> dict[str, Any]:
    simulations = [
        _simulate_record(
            record,
            routers,
            thresholds,
        )
        for record in records
    ]

    router_tokens = []
    oracle_tokens = []
    router_latencies = []
    oracle_latencies = []

    final_correct = []
    oracle_resolved = []
    oracle_agreement = []

    false_stops = []
    unnecessary_escalations = []

    by_answer_type = {}

    for record, simulation in zip(
        records,
        simulations,
    ):
        stop_layer = simulation[
            "router_stop_layer"
        ]

        correct = bool(
            simulation[
                "final_correct"
            ]
        )

        final_correct.append(
            correct
        )

        router_tokens.append(
            _cumulative_tokens(
                record,
                stop_layer,
            )
        )

        router_latencies.append(
            _cumulative_latency(
                record,
                stop_layer,
            )
        )

        oracle_layer = simulation[
            "oracle_layer"
        ]

        if oracle_layer is not None:
            oracle_resolved.append(
                True
            )

            oracle_tokens.append(
                _cumulative_tokens(
                    record,
                    int(oracle_layer),
                )
            )

            oracle_latencies.append(
                _cumulative_latency(
                    record,
                    int(oracle_layer),
                )
            )

            agreement = (
                stop_layer
                == int(oracle_layer)
            )

            oracle_agreement.append(
                agreement
            )

            false_stops.append(
                stop_layer
                < int(oracle_layer)
            )

            unnecessary_escalations.append(
                stop_layer
                > int(oracle_layer)
            )

        answer_type = str(
            record.get(
                "answer_type",
                "unknown",
            )
        )

        by_answer_type.setdefault(
            answer_type,
            [],
        ).append(
            correct
        )

    stop_distribution = Counter(
        simulation[
            "router_stop_layer"
        ]
        for simulation in simulations
    )

    accuracy = (
        sum(final_correct)
        / len(final_correct)
        if final_correct
        else 0.0
    )

    resolved_rate = (
        len(oracle_resolved)
        / len(records)
        if records
        else 0.0
    )

    false_stop_rate = (
        sum(false_stops)
        / len(false_stops)
        if false_stops
        else 0.0
    )

    unnecessary_rate = (
        sum(
            unnecessary_escalations
        )
        / len(
            unnecessary_escalations
        )
        if unnecessary_escalations
        else 0.0
    )

    oracle_agreement_rate = (
        sum(oracle_agreement)
        / len(oracle_agreement)
        if oracle_agreement
        else 0.0
    )

    type_metrics = {
        answer_type: {
            "count": len(values),
            "accuracy": (
                sum(values)
                / len(values)
            ),
        }
        for answer_type, values
        in by_answer_type.items()
    }

    return {
        "dataset": "TAT-QA",
        "records": len(records),
        "final_accuracy": accuracy,
        "mean_router_tokens": _mean(
            [
                float(value)
                for value in router_tokens
            ]
        ),
        "median_router_tokens": _median(
            [
                float(value)
                for value in router_tokens
            ]
        ),
        "mean_router_latency_ms": _mean(
            router_latencies
        ),
        "median_router_latency_ms": _median(
            router_latencies
        ),
        "mean_oracle_tokens": _mean(
            [
                float(value)
                for value in oracle_tokens
            ]
        ),
        "median_oracle_tokens": _median(
            [
                float(value)
                for value in oracle_tokens
            ]
        ),
        "mean_oracle_latency_ms": _mean(
            oracle_latencies
        ),
        "median_oracle_latency_ms": _median(
            oracle_latencies
        ),
        "oracle_resolved_rate": (
            resolved_rate
        ),
        "oracle_agreement_rate": (
            oracle_agreement_rate
        ),
        "false_stop_rate": (
            false_stop_rate
        ),
        "unnecessary_escalation_rate": (
            unnecessary_rate
        ),
        "stop_distribution": {
            f"L{layer}": stop_distribution.get(
                layer,
                0,
            )
            for layer in range(
                1,
                len(LAYER_KEYS) + 1,
            )
        },
        "answer_type_metrics": type_metrics,
        "router_thresholds": thresholds,
    }


def evaluate_always_layer(
    records: list[dict[str, Any]],
    layer_number: int,
) -> dict[str, Any]:
    layer_key = f"layer_{layer_number}"
    corrects = []
    tokens = []
    latencies = []

    for record in records:
        layer = record.get("layers", {}).get(layer_key)
        if not layer:
            continue
        corrects.append(bool(layer.get("correct", False)))
        tokens.append(int(layer.get("total_tokens", 0) or 0))
        latencies.append(float(layer.get("latency_ms", 0.0) or 0.0))

    return {
        "layer": layer_number,
        "records": len(corrects),
        "accuracy": sum(corrects) / len(corrects) if corrects else 0.0,
        "mean_tokens": _mean([float(t) for t in tokens]),
        "median_tokens": _median([float(t) for t in tokens]),
        "mean_latency_ms": _mean(latencies),
        "median_latency_ms": _median(latencies),
    }


def evaluate_fixed_cascade(
    records: list[dict[str, Any]],
    confidence_threshold: float = 0.95,
) -> dict[str, Any]:
    corrects = []
    tokens = []
    latencies = []
    stop_layers = []
    false_stops = []
    unnecessary_escalations = []

    for record in records:
        layers = record.get("layers", {})
        stop_layer = len(LAYER_KEYS)

        for layer_idx, layer_key in enumerate(LAYER_KEYS, start=1):
            layer = layers.get(layer_key)
            if not layer:
                continue
            conf = float(
                layer.get("signals", {}).get("confidence", 0.0) or 0.0
            )
            if conf >= confidence_threshold or layer_idx == len(LAYER_KEYS):
                stop_layer = layer_idx
                break

        final_layer = layers.get(f"layer_{stop_layer}")
        correct = bool(
            final_layer.get("correct", False) if final_layer else False
        )
        corrects.append(correct)
        stop_layers.append(stop_layer)
        tokens.append(_cumulative_tokens(record, stop_layer))
        latencies.append(_cumulative_latency(record, stop_layer))

        oracle_layer = record.get("minimum_sufficient_layer")
        if oracle_layer is not None:
            false_stops.append(stop_layer < int(oracle_layer))
            unnecessary_escalations.append(stop_layer > int(oracle_layer))

    return {
        "confidence_threshold": confidence_threshold,
        "records": len(records),
        "accuracy": sum(corrects) / len(corrects) if corrects else 0.0,
        "mean_tokens": _mean([float(t) for t in tokens]),
        "median_tokens": _median([float(t) for t in tokens]),
        "mean_latency_ms": _mean(latencies),
        "median_latency_ms": _median(latencies),
        "false_stop_rate": (
            sum(false_stops) / len(false_stops) if false_stops else 0.0
        ),
        "unnecessary_escalation_rate": (
            sum(unnecessary_escalations) / len(unnecessary_escalations)
            if unnecessary_escalations
            else 0.0
        ),
        "stop_distribution": dict(Counter(f"L{l}" for l in stop_layers)),
    }


def evaluate_oracle(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    oracle_layers = [
        record.get(
            "minimum_sufficient_layer"
        )
        for record in records
    ]

    valid = [
        layer
        for layer in oracle_layers
        if layer is not None
    ]

    distribution = Counter(
        valid
    )

    oracle_tokens = []
    oracle_latencies = []
    for record in records:
        ol = record.get("minimum_sufficient_layer")
        if ol is not None:
            oracle_tokens.append(_cumulative_tokens(record, int(ol)))
            oracle_latencies.append(_cumulative_latency(record, int(ol)))

    return {
        "records": len(records),
        "resolved": len(valid),
        "unresolved": (
            len(records)
            - len(valid)
        ),
        "resolved_rate": (
            len(valid)
            / len(records)
            if records
            else 0.0
        ),
        "accuracy": (
            len(valid) / len(records) if records else 0.0
        ),
        "mean_tokens": _mean([float(t) for t in oracle_tokens]),
        "mean_latency_ms": _mean(oracle_latencies),
        "distribution": {
            f"L{layer}": distribution.get(
                layer,
                0,
            )
            for layer in range(
                1,
                len(LAYER_KEYS) + 1,
            )
        },
    }


def evaluate_test(
    test_path: Path,
    router_directory: Path,
) -> dict[str, Any]:
    records = load_records(
        test_path
    )

    routers = load_routers(
        router_directory
    )

    thresholds = load_thresholds(
        router_directory
    )

    results = evaluate_router(
        records,
        routers,
        thresholds,
    )

    oracle_results = evaluate_oracle(records)
    always_l1 = evaluate_always_layer(records, 1)
    always_l4 = evaluate_always_layer(records, 4)
    fixed_cascade = evaluate_fixed_cascade(records, 0.95)

    l4_tokens = always_l4.get("mean_tokens", 0.0)
    l4_lat = always_l4.get("mean_latency_ms", 0.0)
    router_tokens = results.get("mean_router_tokens", 0.0)
    router_lat = results.get("mean_router_latency_ms", 0.0)

    token_savings_pct = (
        ((l4_tokens - router_tokens) / l4_tokens * 100.0)
        if l4_tokens > 0
        else 0.0
    )
    latency_savings_pct = (
        ((l4_lat - router_lat) / l4_lat * 100.0)
        if l4_lat > 0
        else 0.0
    )

    results["oracle"] = oracle_results
    results["always_l1"] = always_l1
    results["always_l4"] = always_l4
    results["fixed_cascade"] = fixed_cascade
    results["token_savings_pct_vs_l4"] = token_savings_pct
    results["latency_savings_pct_vs_l4"] = latency_savings_pct

    results[
        "test_trace"
    ] = str(
        test_path
    )

    results[
        "router_directory"
    ] = str(
        router_directory
    )

    return results


def print_evaluation(
    results: dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print(
        "PHASE III TAT-QA EVALUATION REPORT"
    )
    print("=" * 72)

    print(
        f"Dataset                  : {results['dataset']}"
    )
    print(
        f"Records                  : {results['records']}"
    )

    print()
    print("-" * 72)
    print("METHOD COMPARISON SUMMARY")
    print("-" * 72)
    header = f"{'Method':<24} | {'Accuracy':<9} | {'Mean Tokens':<11} | {'Mean Latency':<12}"
    print(header)
    print("-" * 72)

    l1 = results.get("always_l1", {})
    if l1.get("records"):
        print(
            f"{'Always-L1':<24} | "
            f"{l1.get('accuracy', 0.0) * 100:>8.2f}% | "
            f"{l1.get('mean_tokens', 0.0):>11.1f} | "
            f"{l1.get('mean_latency_ms', 0.0) / 1000:>10.2f}s"
        )

    l4 = results.get("always_l4", {})
    if l4.get("records"):
        print(
            f"{'Always-L4':<24} | "
            f"{l4.get('accuracy', 0.0) * 100:>8.2f}% | "
            f"{l4.get('mean_tokens', 0.0):>11.1f} | "
            f"{l4.get('mean_latency_ms', 0.0) / 1000:>10.2f}s"
        )

    fc = results.get("fixed_cascade", {})
    if fc.get("records"):
        print(
            f"{'Fixed Cascade (conf>=.95)':<24} | "
            f"{fc.get('accuracy', 0.0) * 100:>8.2f}% | "
            f"{fc.get('mean_tokens', 0.0):>11.1f} | "
            f"{fc.get('mean_latency_ms', 0.0) / 1000:>10.2f}s"
        )

    print(
        f"{'Learned Cascade (Router)':<24} | "
        f"{results.get('final_accuracy', 0.0) * 100:>8.2f}% | "
        f"{results.get('mean_router_tokens', 0.0):>11.1f} | "
        f"{results.get('mean_router_latency_ms', 0.0) / 1000:>10.2f}s"
    )

    oracle = results.get("oracle", {})
    if oracle.get("records"):
        print(
            f"{'Oracle Cascade':<24} | "
            f"{oracle.get('accuracy', 0.0) * 100:>8.2f}% | "
            f"{oracle.get('mean_tokens', 0.0):>11.1f} | "
            f"{oracle.get('mean_latency_ms', 0.0) / 1000:>10.2f}s"
        )

    print("-" * 72)

    print()
    print("SAVINGS vs ALWAYS-L4:")
    print(
        f"  Token Savings   : {results.get('token_savings_pct_vs_l4', 0.0):.2f}%"
    )
    print(
        f"  Latency Savings : {results.get('latency_savings_pct_vs_l4', 0.0):.2f}%"
    )

    print()
    print("ROUTER OPERATIONAL METRICS:")
    print(
        f"  False-stop rate           : {results.get('false_stop_rate', 0.0) * 100:.2f}%"
    )
    print(
        f"  Unnecessary escalation    : {results.get('unnecessary_escalation_rate', 0.0) * 100:.2f}%"
    )
    print(
        f"  Oracle agreement rate     : {results.get('oracle_agreement_rate', 0.0) * 100:.2f}%"
    )

    print()
    print("Stop distribution:")
    for layer, count in results.get("stop_distribution", {}).items():
        print(f"  {layer}: {count}")

    print()
    print("Accuracy by answer type:")
    for answer_type, metrics in results.get("answer_type_metrics", {}).items():
        print(
            f"  {answer_type}: {metrics['accuracy'] * 100:.2f}% "
            f"({metrics['count']} records)"
        )

    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--test",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--routers",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    results = evaluate_test(
        args.test,
        args.routers,
    )

    print_evaluation(
        results
    )

    if args.output is not None:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with args.output.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                results,
                handle,
                indent=2,
            )


if __name__ == "__main__":
    main()