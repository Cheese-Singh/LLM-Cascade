from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metrics import summarize_records, summarize_run_records


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _records_from_experiment(
    experiment: dict[str, Any],
) -> list[dict[str, Any]]:
    records = experiment.get("records")

    if isinstance(records, list):
        return [
            record
            for record in records
            if isinstance(record, dict)
        ]

    results = experiment.get("results")

    if isinstance(results, dict):
        flattened = []

        for system, system_records in results.items():
            if not isinstance(system_records, list):
                continue

            for record in system_records:
                if not isinstance(record, dict):
                    continue

                enriched = dict(record)
                enriched.setdefault("system", system)
                flattened.append(enriched)

        return flattened

    if not isinstance(results, list):
        return []

    flattened = []

    for result in results:
        if not isinstance(result, dict):
            continue

        if isinstance(result.get("runs"), list):
            for run in result["runs"]:
                if isinstance(run, dict):
                    flattened.append(run)

        elif isinstance(result.get("records"), list):
            for record in result["records"]:
                if isinstance(record, dict):
                    flattened.append(record)

        else:
            flattened.append(result)

    return flattened


def _run_records_from_experiment(
    experiment: dict[str, Any],
) -> list[dict[str, Any]]:
    runs = experiment.get("run_records")

    if isinstance(runs, list):
        return [
            record
            for record in runs
            if isinstance(record, dict)
        ]

    results = experiment.get("results")

    if isinstance(results, dict):
        extracted = []

        for system, system_records in results.items():
            if not isinstance(system_records, list):
                continue

            for record in system_records:
                if not isinstance(record, dict):
                    continue

                if {
                    "weak_correct",
                    "final_correct",
                    "escalated",
                }.issubset(record):
                    enriched = dict(record)
                    enriched.setdefault("system", system)
                    extracted.append(enriched)

        return extracted

    if not isinstance(results, list):
        return []

    extracted = []

    for result in results:
        if not isinstance(result, dict):
            continue

        run_records = result.get("run_records")

        if isinstance(run_records, list):
            extracted.extend(
                record
                for record in run_records
                if isinstance(record, dict)
            )
            continue

        if {
            "weak_correct",
            "final_correct",
            "escalated",
        }.issubset(result):
            extracted.append(result)

    return extracted


def summarize_experiment(
    experiment: dict[str, Any],
) -> dict[str, Any]:
    records = _records_from_experiment(experiment)
    run_records = _run_records_from_experiment(experiment)

    mode = experiment.get(
        "mode",
        experiment.get("experiment", "unknown"),
    )

    summary: dict[str, Any] = {
        "mode": mode,
        "timestamp": utc_timestamp(),
        "n_records": len(records),
        "n_run_records": len(run_records),
    }

    if records:
        summary["record_metrics"] = summarize_records(
            records
        )
    else:
        summary["record_metrics"] = summarize_records(
            []
        )

    if run_records:
        summary["run_metrics"] = summarize_run_records(
            run_records
        )

    return summary


def summarize_results_by_system(
    results: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    systems = {}

    for system, records in results.items():
        if not isinstance(records, list):
            continue

        valid_records = [
            record
            for record in records
            if isinstance(record, dict)
        ]

        systems[system] = {
            "n_records": len(valid_records),
            "metrics": summarize_records(
                valid_records
            ),
        }

    return {
        "timestamp": utc_timestamp(),
        "systems": systems,
    }


def format_summary(
    summary: dict[str, Any],
) -> str:
    lines = [
        f"Mode: {summary.get('mode', 'unknown')}",
        f"Records: {summary.get('n_records', 0)}",
        f"Run records: {summary.get('n_run_records', 0)}",
    ]

    record_metrics = summary.get(
        "record_metrics",
        {},
    )

    if record_metrics:
        lines.append("")
        lines.append("Record metrics:")

        accuracy = record_metrics.get(
            "accuracy",
            0.0,
        )
        mean_tokens = record_metrics.get(
            "mean_tokens",
            0.0,
        )
        median_tokens = record_metrics.get(
            "median_tokens",
            0.0,
        )
        mean_latency = record_metrics.get(
            "mean_latency_ms",
            0.0,
        )
        median_latency = record_metrics.get(
            "median_latency_ms",
            0.0,
        )
        strong_rate = record_metrics.get(
            "strong_invocation_rate",
            0.0,
        )
        escalation_rate = record_metrics.get(
            "escalation_rate",
            0.0,
        )

        lines.extend(
            [
                f"  Accuracy                : {accuracy:.4f}",
                f"  Mean tokens/query      : {mean_tokens:.2f}",
                f"  Median tokens/query    : {median_tokens:.2f}",
                f"  Mean latency (ms)      : {mean_latency:.2f}",
                f"  Median latency (ms)    : {median_latency:.2f}",
                f"  Escalation rate        : {escalation_rate:.4f}",
                f"  Strong invocation rate : {strong_rate:.4f}",
            ]
        )

    run_metrics = summary.get(
        "run_metrics",
        {},
    )

    if run_metrics:
        lines.append("")
        lines.append("Run metrics:")

        weak_accuracy = run_metrics.get(
            "weak_accuracy",
            0.0,
        )
        final_accuracy = run_metrics.get(
            "final_accuracy",
            0.0,
        )
        recovery_rate = run_metrics.get(
            "error_recovery_rate",
            0.0,
        )
        escalation = run_metrics.get(
            "escalation_rate",
            0.0,
        )
        total_tokens = run_metrics.get(
            "total_tokens",
            0,
        )
        mean_tokens = run_metrics.get(
            "mean_tokens",
            0.0,
        )
        median_tokens = run_metrics.get(
            "median_tokens",
            0.0,
        )
        mean_latency = run_metrics.get(
            "mean_latency_ms",
            0.0,
        )
        median_latency = run_metrics.get(
            "median_latency_ms",
            0.0,
        )

        lines.extend(
            [
                f"  Weak accuracy           : {weak_accuracy:.4f}",
                f"  Final accuracy          : {final_accuracy:.4f}",
                f"  Error recovery rate     : {recovery_rate:.4f}",
                f"  Escalation rate         : {escalation:.4f}",
                f"  Total tokens            : {total_tokens}",
                f"  Mean tokens/query       : {mean_tokens:.2f}",
                f"  Median tokens/query     : {median_tokens:.2f}",
                f"  Mean latency (ms)       : {mean_latency:.2f}",
                f"  Median latency (ms)     : {median_latency:.2f}",
            ]
        )

    return "\n".join(lines)


def _safe_run_id(
    run_id: str | None,
) -> str:
    if run_id:
        cleaned = "".join(
            character
            if character.isalnum()
            or character in "-_"
            else "_"
            for character in run_id
        )

        if cleaned:
            return cleaned

    return datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )


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

    resolved_run_id = _safe_run_id(
        run_id
    )

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

    experiment_payload = dict(
        experiment
    )
    experiment_payload["saved_at"] = (
        utc_timestamp()
    )

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
    print(
        format_summary(summary)
    )


def build_experiment(
    mode: str,
    results: dict[str, list[dict[str, Any]]] | list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    experiment: dict[str, Any] = {
        "mode": mode,
        "created_at": utc_timestamp(),
        "results": results,
    }

    if metadata:
        experiment["metadata"] = metadata

    records: list[dict[str, Any]] = []

    if isinstance(results, dict):
        for system, system_records in results.items():
            if not isinstance(system_records, list):
                continue

            for record in system_records:
                if not isinstance(record, dict):
                    continue

                enriched = dict(record)
                enriched.setdefault(
                    "system",
                    system,
                )
                records.append(enriched)

    else:
        for result in results:
            if not isinstance(result, dict):
                continue

            nested_records = result.get(
                "records"
            )

            if isinstance(
                nested_records,
                list,
            ):
                records.extend(
                    record
                    for record in nested_records
                    if isinstance(
                        record,
                        dict,
                    )
                )
            else:
                records.append(
                    result
                )

    if records:
        experiment["records"] = records

    return experiment