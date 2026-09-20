from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_run_id(run_id: str | None) -> str:
    if run_id:
        cleaned = "".join(
            character
            if character.isalnum() or character in "-_"
            else "_"
            for character in run_id
        )

        if cleaned:
            return cleaned

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalise_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalised = []

    for record in records:
        if not isinstance(record, dict):
            continue

        normalised.append(record)

    return normalised


def _records_from_experiment(
    experiment: dict[str, Any],
) -> list[dict[str, Any]]:
    records = experiment.get("records")

    if isinstance(records, list):
        return _normalise_records(records)

    results = experiment.get("results")

    if isinstance(results, dict):
        flattened = []

        for system, system_records in results.items():
            if not isinstance(system_records, list):
                continue

            for record in system_records:
                if not isinstance(record, dict):
                    continue

                item = dict(record)

                if "system" not in item:
                    item["system"] = system

                flattened.append(item)

        return flattened

    if isinstance(results, list):
        flattened = []

        for result in results:
            if not isinstance(result, dict):
                continue

            if isinstance(result.get("records"), list):
                for record in result["records"]:
                    if isinstance(record, dict):
                        flattened.append(record)
            else:
                flattened.append(result)

        return flattened

    return []


def _run_records_from_experiment(
    experiment: dict[str, Any],
) -> list[dict[str, Any]]:
    runs = experiment.get("run_records")

    if isinstance(runs, list):
        return _normalise_records(runs)

    return []


def _system_metrics(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not records:
        return {
            "count": 0,
            "accuracy": 0.0,
            "error_rate": 0.0,
            "mean_tokens": 0.0,
            "median_tokens": 0.0,
            "mean_latency_ms": 0.0,
            "median_latency_ms": 0.0,
            "escalation_rate": 0.0,
            "strong_invocation_rate": 0.0,
            "layer_invocation_rates": {},
        }

    correct_values = [
        bool(record.get("final_correct", False))
        for record in records
    ]

    token_values = [
        int(record.get("total_tokens", 0) or 0)
        for record in records
    ]

    latency_values = [
        float(record.get("total_latency_ms", 0.0) or 0.0)
        for record in records
    ]

    escalated_values = [
        bool(record.get("escalated", False))
        for record in records
    ]

    strong_values = [
        bool(record.get("strong_invoked", False))
        for record in records
    ]

    layer_counts = {
        "layer_1": 0,
        "layer_2": 0,
        "layer_3": 0,
        "layer_4": 0,
    }

    for record in records:
        hops = record.get("hops", [])

        if not isinstance(hops, list):
            continue

        seen_layers = set()

        for hop in hops:
            if not isinstance(hop, dict):
                continue

            model_key = hop.get("model_key")

            if model_key in layer_counts:
                seen_layers.add(model_key)

        for layer in seen_layers:
            layer_counts[layer] += 1

    count = len(records)

    return {
        "count": count,
        "accuracy": sum(correct_values) / count,
        "error_rate": 1.0 - (
            sum(correct_values) / count
        ),
        "mean_tokens": mean(token_values),
        "median_tokens": median(token_values),
        "mean_latency_ms": mean(latency_values),
        "median_latency_ms": median(latency_values),
        "escalation_rate": (
            sum(escalated_values) / count
        ),
        "strong_invocation_rate": (
            sum(strong_values) / count
        ),
        "layer_invocation_rates": {
            layer: layer_counts[layer] / count
            for layer in layer_counts
        },
    }


def summarize_experiment(
    experiment: dict[str, Any],
) -> dict[str, Any]:
    records = _records_from_experiment(experiment)
    run_records = _run_records_from_experiment(experiment)

    mode = experiment.get("mode", "unknown")

    summary: dict[str, Any] = {
        "mode": mode,
        "timestamp": utc_timestamp(),
        "n_records": len(records),
    }

    if isinstance(experiment.get("results"), dict):
        system_summaries = {}

        for system, system_records in experiment["results"].items():
            if not isinstance(system_records, list):
                continue

            clean_records = _normalise_records(
                system_records
            )

            system_summaries[system] = _system_metrics(
                clean_records
            )

        summary["systems"] = system_summaries

    else:
        summary["record_metrics"] = _system_metrics(
            records
        )

    if run_records:
        summary["run_metrics"] = _summarize_run_records(
            run_records
        )

    if not records and not run_records:
        summary["record_metrics"] = _system_metrics([])

    return summary


def _summarize_run_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not records:
        return {
            "count": 0,
            "final_accuracy": 0.0,
            "escalation_rate": 0.0,
            "mean_tokens": 0.0,
            "median_tokens": 0.0,
            "mean_latency_ms": 0.0,
            "median_latency_ms": 0.0,
        }

    final_correct = [
        bool(record.get("final_correct", False))
        for record in records
    ]

    escalated = [
        bool(record.get("escalated", False))
        for record in records
    ]

    tokens = [
        int(record.get("total_tokens", 0) or 0)
        for record in records
    ]

    latency = [
        float(record.get("total_latency_ms", 0.0) or 0.0)
        for record in records
    ]

    count = len(records)

    return {
        "count": count,
        "final_accuracy": sum(final_correct) / count,
        "escalation_rate": sum(escalated) / count,
        "total_tokens": sum(tokens),
        "mean_tokens": mean(tokens),
        "median_tokens": median(tokens),
        "mean_latency_ms": mean(latency),
        "median_latency_ms": median(latency),
    }


def format_summary(
    summary: dict[str, Any],
) -> str:
    lines = []

    lines.append(
        f"Mode: {summary.get('mode', 'unknown')}"
    )

    lines.append(
        f"Records: {summary.get('n_records', 0)}"
    )

    systems = summary.get("systems", {})

    if systems:
        for system, metrics in systems.items():
            lines.append("")
            lines.append(system)

            lines.append(
                f"  Accuracy                 : "
                f"{metrics['accuracy']:.4f}"
            )

            lines.append(
                f"  Error rate               : "
                f"{metrics['error_rate']:.4f}"
            )

            lines.append(
                f"  Mean tokens/query       : "
                f"{metrics['mean_tokens']:.2f}"
            )

            lines.append(
                f"  Median tokens/query     : "
                f"{metrics['median_tokens']:.2f}"
            )

            lines.append(
                f"  Mean latency (ms)       : "
                f"{metrics['mean_latency_ms']:.2f}"
            )

            lines.append(
                f"  Median latency (ms)     : "
                f"{metrics['median_latency_ms']:.2f}"
            )

            lines.append(
                f"  Escalation rate          : "
                f"{metrics['escalation_rate']:.4f}"
            )

            lines.append(
                f"  Strong invocation rate  : "
                f"{metrics['strong_invocation_rate']:.4f}"
            )

            layer_rates = metrics.get(
                "layer_invocation_rates",
                {},
            )

            if layer_rates:
                lines.append(
                    f"  Layer 1 invocation rate : "
                    f"{layer_rates['layer_1']:.4f}"
                )

                lines.append(
                    f"  Layer 2 invocation rate : "
                    f"{layer_rates['layer_2']:.4f}"
                )

                lines.append(
                    f"  Layer 3 invocation rate : "
                    f"{layer_rates['layer_3']:.4f}"
                )

                lines.append(
                    f"  Layer 4 invocation rate : "
                    f"{layer_rates['layer_4']:.4f}"
                )

    else:
        record_metrics = summary.get(
            "record_metrics",
            {},
        )

        if record_metrics:
            lines.append("")
            lines.append("Record metrics:")

            lines.append(
                f"  Accuracy                 : "
                f"{record_metrics.get('accuracy', 0.0):.4f}"
            )

            lines.append(
                f"  Mean tokens/query       : "
                f"{record_metrics.get('mean_tokens', 0.0):.2f}"
            )

            lines.append(
                f"  Median tokens/query     : "
                f"{record_metrics.get('median_tokens', 0.0):.2f}"
            )

            lines.append(
                f"  Mean latency (ms)       : "
                f"{record_metrics.get('mean_latency_ms', 0.0):.2f}"
            )

            lines.append(
                f"  Median latency (ms)     : "
                f"{record_metrics.get('median_latency_ms', 0.0):.2f}"
            )

    run_metrics = summary.get(
        "run_metrics",
        {},
    )

    if run_metrics:
        lines.append("")
        lines.append("Run metrics:")

        lines.append(
            f"  Final accuracy           : "
            f"{run_metrics.get('final_accuracy', 0.0):.4f}"
        )

        lines.append(
            f"  Escalation rate          : "
            f"{run_metrics.get('escalation_rate', 0.0):.4f}"
        )

        lines.append(
            f"  Total tokens             : "
            f"{run_metrics.get('total_tokens', 0)}"
        )

        lines.append(
            f"  Mean tokens/query        : "
            f"{run_metrics.get('mean_tokens', 0.0):.2f}"
        )

        lines.append(
            f"  Median tokens/query      : "
            f"{run_metrics.get('median_tokens', 0.0):.2f}"
        )

        lines.append(
            f"  Mean latency (ms)        : "
            f"{run_metrics.get('mean_latency_ms', 0.0):.2f}"
        )

        lines.append(
            f"  Median latency (ms)      : "
            f"{run_metrics.get('median_latency_ms', 0.0):.2f}"
        )

    return "\n".join(lines)


def save_experiment_results(
    experiment: dict[str, Any],
    summary: dict[str, Any],
    output_dir: str | Path,
    run_id: str | None = None,
) -> list[Path]:
    output_path = Path(output_dir)

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    resolved_run_id = _safe_run_id(run_id)

    experiment_path = (
        output_path
        / f"experiment_{resolved_run_id}.json"
    )

    summary_path = (
        output_path
        / f"summary_{resolved_run_id}.json"
    )

    records_path = (
        output_path
        / f"records_{resolved_run_id}.jsonl"
    )

    experiment_payload = dict(experiment)

    experiment_payload["saved_at"] = utc_timestamp()

    experiment_path.write_text(
        json.dumps(
            experiment_payload,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    summary_path.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    records = _records_from_experiment(
        experiment
    )

    with records_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        for record in records:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

    return [
        experiment_path,
        summary_path,
        records_path,
    ]


def print_summary(
    summary: dict[str, Any],
) -> None:
    print(format_summary(summary))


def build_experiment(
    mode: str,
    results: dict[str, list[dict[str, Any]]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    experiment: dict[str, Any] = {
        "mode": mode,
        "created_at": utc_timestamp(),
        "results": results,
    }

    if metadata:
        experiment["metadata"] = metadata

    records = []

    for system, system_records in results.items():
        if not isinstance(system_records, list):
            continue

        for record in system_records:
            if not isinstance(record, dict):
                continue

            item = dict(record)

            if "system" not in item:
                item["system"] = system

            records.append(item)

    experiment["records"] = records

    return experiment