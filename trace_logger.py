from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_trace(
    problem_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    latency_ms: float,
    answer: str,
    correct: bool | None = None,
    escalated: bool = False,
    verification_passed: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    trace = {
        "timestamp": utc_timestamp(),
        "problem_id": problem_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "answer": answer,
        "correct": correct,
        "escalated": escalated,
        "verification_passed": verification_passed,
    }
    trace.update(extra)
    return trace


def create_run_trace(
    problem_id: str,
    weak_correct: bool | None,
    strong_correct: bool | None,
    final_correct: bool | None,
    total_tokens: int,
    total_latency_ms: float,
    escalated: bool,
    **extra: Any,
) -> dict[str, Any]:
    trace = {
        "timestamp": utc_timestamp(),
        "problem_id": problem_id,
        "weak_correct": weak_correct,
        "strong_correct": strong_correct,
        "final_correct": final_correct,
        "total_tokens": total_tokens,
        "total_latency_ms": total_latency_ms,
        "escalated": escalated,
    }
    trace.update(extra)
    return trace


def save_jsonl(
    records: list[dict[str, Any]],
    path: str | Path,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        for record in records:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )

    return output_path


def append_jsonl(
    record: dict[str, Any],
    path: str | Path,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
            + "\n"
        )

    return output_path


def load_jsonl(
    path: str | Path,
) -> list[dict[str, Any]]:
    input_path = Path(path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Trace file not found: {input_path}"
        )

    records = []

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} "
                    f"of {input_path}: {exc}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Line {line_number} of {input_path} "
                    "must contain a JSON object."
                )

            records.append(record)

    return records