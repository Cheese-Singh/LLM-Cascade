from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(
        self,
        trace_dir: str | Path = "traces",
        run_id: str | None = None,
    ) -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if run_id is None:
            run_id = time.strftime(
                "%Y%m%d_%H%M%S"
            )

        self.run_id = run_id
        self.records: list[dict[str, Any]] = []

    def start_problem(
        self,
        problem_id: str,
        domain: str = "code",
    ) -> dict[str, Any]:
        trace = {
            "problem_id": problem_id,
            "domain": domain,
            "started_at": time.time(),
            "hops": [],
        }

        self.records.append(trace)
        return trace

    def log_hop(
        self,
        trace: dict[str, Any],
        *,
        layer: int,
        model_key: str,
        model: str,
        candidate: str,
        verification_passed: bool,
        verification_error: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float = 0.0,
        attempts: int = 1,
        retries: int = 0,
        stop_reason: str | None = None,
    ) -> dict[str, Any]:
        hop = {
            "layer": layer,
            "model_key": model_key,
            "model": model,
            "candidate": candidate,
            "verification_passed": verification_passed,
            "verification_error": verification_error,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "attempts": attempts,
            "retries": retries,
            "stop_reason": stop_reason,
            "timestamp": time.time(),
        }

        trace.setdefault(
            "hops",
            [],
        ).append(hop)

        return hop

    def finish_problem(
        self,
        trace: dict[str, Any],
        *,
        final_correct: bool,
        final_error: str = "",
        stop_reason: str = "",
    ) -> dict[str, Any]:
        trace["finished_at"] = time.time()
        trace["duration_ms"] = (
            trace["finished_at"]
            - trace["started_at"]
        ) * 1000.0

        trace["final_correct"] = final_correct
        trace["final_error"] = final_error
        trace["stop_reason"] = stop_reason
        trace["n_hops_used"] = len(
            trace.get("hops", [])
        )

        trace["total_tokens"] = sum(
            hop.get(
                "total_tokens",
                0,
            )
            for hop in trace.get(
                "hops",
                [],
            )
        )

        trace["total_latency_ms"] = sum(
            hop.get(
                "latency_ms",
                0.0,
            )
            for hop in trace.get(
                "hops",
                [],
            )
        )

        trace["escalated"] = (
            trace["n_hops_used"] > 1
        )

        trace["strong_invoked"] = any(
            hop.get("layer", 1) >= 3
            for hop in trace.get(
                "hops",
                [],
            )
        )

        return trace

    def log_record(
        self,
        record: dict[str, Any],
    ) -> None:
        self.records.append(record)

    def write(
        self,
        filename: str | None = None,
    ) -> Path:
        if filename is None:
            filename = (
                f"trace_{self.run_id}.json"
            )

        path = self.trace_dir / filename

        payload = {
            "run_id": self.run_id,
            "created_at": time.time(),
            "n_records": len(
                self.records
            ),
            "records": self.records,
        }

        path.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

        return path

    def write_jsonl(
        self,
        filename: str | None = None,
    ) -> Path:
        if filename is None:
            filename = (
                f"trace_{self.run_id}.jsonl"
            )

        path = self.trace_dir / filename

        with path.open(
            "w",
            encoding="utf-8",
        ) as handle:
            for record in self.records:
                handle.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )

        return path


def load_trace_file(
    path: str | Path,
) -> dict[str, Any]:
    path = Path(path)

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def flatten_trace_records(
    trace_data: dict[str, Any],
) -> list[dict[str, Any]]:
    records = trace_data.get(
        "records",
        [],
    )

    if not isinstance(
        records,
        list,
    ):
        return []

    return [
        record
        for record in records
        if isinstance(
            record,
            dict,
        )
    ]