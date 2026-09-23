from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_DOMAIN,
    DEFAULT_PROBLEM_FILES,
    DEFAULT_PROBLEMS_FILE,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    MODEL_CONFIG,
)
from run_baselines import call_model, extract_code
from verifiers import verify


LAYER_KEYS = (
    "layer_1",
    "layer_2",
    "layer_3",
    "layer_4",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--domain",
        choices=("code", "math", "finance"),
        default=DEFAULT_DOMAIN,
    )

    parser.add_argument(
        "--start-from",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--end-at",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--problems-file",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("traces/counterfactual.jsonl"),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    return parser.parse_args()


def load_problems(
    path: Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Problems file not found: {path}"
        )

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if isinstance(data, dict):
        data = data.get(
            "problems",
            [data],
        )

    if not isinstance(data, list):
        raise ValueError(
            "Problems file must contain a JSON list."
        )

    problems = []

    for problem in data:
        if not isinstance(problem, dict):
            continue

        if (
            "id" not in problem
            or "prompt" not in problem
        ):
            continue

        problems.append(problem)

    if limit is not None:
        problems = problems[:limit]

    return problems


def resolve_problem_file(
    domain: str,
    explicit_path: Path | None,
) -> Path:
    if explicit_path is not None:
        return explicit_path

    if domain in DEFAULT_PROBLEM_FILES:
        return DEFAULT_PROBLEM_FILES[domain]

    return DEFAULT_PROBLEMS_FILE


def extract_answer_text(
    response: str,
) -> str:
    text = response.strip()

    import re

    boxed = re.findall(
        r"\\boxed\{([^{}]+)\}",
        text,
        flags=re.DOTALL,
    )

    if boxed:
        return boxed[-1].strip()

    matches = re.findall(
        r"(?:final answer|answer)\s*[:=]\s*(.+)",
        text,
        flags=re.IGNORECASE,
    )

    if matches:
        return matches[-1].strip()

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return lines[-1] if lines else text


def run_elapsed_timer(
    start_time: float,
    stop_event: threading.Event,
    prefix: str,
) -> None:
    while not stop_event.wait(1.0):
        elapsed = time.perf_counter() - start_time
        print(
            f"\r{prefix} | elapsed={elapsed:.1f}s",
            end="",
            flush=True,
        )


def build_first_prompt(
    problem: dict[str, Any],
    domain: str,
) -> tuple[str, str]:
    if domain == "code":
        system_prompt = (
            "You are an expert Python programmer. "
            "Complete the given function. Return ONLY "
            "the complete function implementation in a "
            "single Python code block. Do not include "
            "test code, example usage, or explanations. "
            "The function must be named exactly as specified."
        )

        user_prompt = (
            f"Complete this function. It must be named "
            f"exactly '{problem['entry_point']}':\n\n"
            f"```python\n{problem['prompt']}\n```"
        )

        return system_prompt, user_prompt

    if domain == "finance":
        system_prompt = (
            "You are an expert financial problem solver. "
            "Solve the given quantitative finance problem "
            "accurately. Give the final numerical answer "
            "clearly. You may show concise reasoning."
        )

        user_prompt = (
            "Solve the following finance problem and provide "
            "the final numerical answer:\n\n"
            f"{problem['prompt']}"
        )

        return system_prompt, user_prompt

    system_prompt = (
        "You are an expert mathematical problem solver. "
        "Solve the given problem accurately. "
        "Give the final numerical answer clearly. "
        "You may show concise reasoning."
    )

    user_prompt = (
        "Solve the following mathematical problem and provide "
        "the final numerical answer:\n\n"
        f"{problem['prompt']}"
    )

    return system_prompt, user_prompt


def build_contextual_prompt(
    problem: dict[str, Any],
    domain: str,
    previous_result: dict[str, Any],
) -> tuple[str, str]:
    previous_answer = previous_result.get(
        "answer",
        "",
    )

    previous_response = previous_result.get(
        "response",
        "",
    )

    previous_correct = bool(
        previous_result.get(
            "correct",
            False,
        )
    )

    previous_verification = previous_result.get(
        "verification",
        {},
    )

    previous_error = previous_verification.get(
        "error",
        "",
    )

    if domain == "code":
        system_prompt = (
            "You are an expert Python programmer reviewing "
            "a solution produced by another model. You are "
            "given the original problem, the previous model's "
            "response, the extracted candidate implementation, "
            "and the objective verification result. Carefully "
            "check the previous solution and produce the best "
            "corrected complete function implementation. "
            "Do not assume the previous solution is correct. "
            "Return ONLY the complete function implementation "
            "in a single Python code block. Do not include "
            "test code, example usage, or explanations."
        )

        user_prompt = (
            f"Original problem:\n\n"
            f"```python\n{problem['prompt']}\n```\n\n"
            f"Previous model response:\n\n"
            f"{previous_response}\n\n"
            f"Extracted candidate implementation:\n\n"
            f"```python\n{previous_answer}\n```\n\n"
            f"Previous verification result: "
            f"{'PASS' if previous_correct else 'FAIL'}\n"
            f"Verification error: {previous_error}\n\n"
            f"Review the previous solution and return a "
            f"correct implementation of "
            f"'{problem['entry_point']}'."
        )

        return system_prompt, user_prompt

    if domain == "finance":
        system_prompt = (
            "You are an expert financial problem solver "
            "reviewing an answer produced by another model. "
            "You are given the original finance problem and "
            "the previous model's response and extracted "
            "answer. Carefully recompute the result yourself. "
            "Do not assume the previous answer is correct. "
            "Correct any mistake and provide the final "
            "numerical answer clearly."
        )

        user_prompt = (
            f"Original finance problem:\n\n"
            f"{problem['prompt']}\n\n"
            f"Previous model response:\n\n"
            f"{previous_response}\n\n"
            f"Previous extracted answer:\n\n"
            f"{previous_answer}\n\n"
            f"Previous verification result: "
            f"{'PASS' if previous_correct else 'FAIL'}\n"
            f"Verification error: {previous_error}\n\n"
            f"Independently recompute the answer and "
            f"provide the corrected final numerical answer."
        )

        return system_prompt, user_prompt

    system_prompt = (
        "You are an expert mathematical problem solver "
        "reviewing an answer produced by another model. "
        "You are given the original problem and the previous "
        "model's response and extracted answer. Carefully "
        "check the previous solution yourself. Do not assume "
        "the previous answer is correct. Correct any mistake "
        "you find and provide the final answer clearly."
    )

    user_prompt = (
        f"Original problem:\n\n"
        f"{problem['prompt']}\n\n"
        f"Previous model response:\n\n"
        f"{previous_response}\n\n"
        f"Previous extracted answer:\n\n"
        f"{previous_answer}\n\n"
        f"Previous verification result: "
        f"{'PASS' if previous_correct else 'FAIL'}\n"
        f"Verification error: {previous_error}\n\n"
        f"Independently check the previous answer and "
        f"provide the corrected final answer."
    )

    return system_prompt, user_prompt


def run_model(
    problem: dict[str, Any],
    model_key: str,
    domain: str,
    temperature: float,
    seed: int,
    previous_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if previous_result is None:
        system_prompt, user_prompt = build_first_prompt(
            problem,
            domain,
        )
    else:
        system_prompt, user_prompt = build_contextual_prompt(
            problem,
            domain,
            previous_result,
        )

    result = call_model(
        model_key=model_key,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        seed=seed,
    )

    raw_response = result.get(
        "response",
        "",
    )

    if domain == "code":
        candidate = extract_code(
            raw_response,
            problem["entry_point"],
        )
    else:
        candidate = extract_answer_text(
            raw_response
        )

    verification = verify(
        domain,
        problem,
        candidate,
    )

    return {
        "model_key": model_key,
        "model": result.get(
            "model",
            MODEL_CONFIG[model_key]["model"],
        ),
        "response": raw_response,
        "answer": candidate,
        "correct": bool(
            verification.get(
                "passed",
                False,
            )
        ),
        "verification": verification,
        "input_tokens": int(
            result.get(
                "input_tokens",
                0,
            )
            or 0
        ),
        "output_tokens": int(
            result.get(
                "output_tokens",
                0,
            )
            or 0
        ),
        "total_tokens": int(
            result.get(
                "total_tokens",
                0,
            )
            or 0
        ),
        "latency_ms": float(
            result.get(
                "latency_ms",
                0.0,
            )
            or 0.0
        ),
        "attempts": int(
            result.get(
                "attempts",
                1,
            )
            or 1
        ),
        "retries": int(
            result.get(
                "retries",
                0,
            )
            or 0
        ),
    }


def build_layer_trace(
    problem: dict[str, Any],
    domain: str,
    layer_index: int,
    result: dict[str, Any],
    previous_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "problem_id": problem["id"],
        "domain": domain,
        "layer": layer_index,
        "model_key": result["model_key"],
        "model": result["model"],
        "previous_layer": (
            layer_index - 1
            if previous_result is not None
            else None
        ),
        "previous_answer": (
            previous_result.get("answer")
            if previous_result is not None
            else None
        ),
        "response": result["response"],
        "answer": result["answer"],
        "correct": result["correct"],
        "verification": result["verification"],
        "verification_passed": bool(
            result["verification"].get(
                "passed",
                False,
            )
        ),
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "total_tokens": result["total_tokens"],
        "latency_ms": result["latency_ms"],
        "attempts": result["attempts"],
        "retries": result["retries"],
        "escalated": layer_index > 1,
    }


def first_correct_layer(
    layer_results: dict[str, dict[str, Any]],
) -> int | None:
    for index, layer_key in enumerate(
        LAYER_KEYS,
        start=1,
    ):
        if layer_results[layer_key]["correct"]:
            return index

    return None


def build_counterfactual_record(
    problem: dict[str, Any],
    domain: str,
    layer_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    minimum_layer = first_correct_layer(
        layer_results
    )

    layer_correctness = {
        layer_key: bool(
            layer_results[layer_key]["correct"]
        )
        for layer_key in LAYER_KEYS
    }

    layer_traces = {}

    previous_result = None

    for index, layer_key in enumerate(
        LAYER_KEYS,
        start=1,
    ):
        layer_traces[layer_key] = build_layer_trace(
            problem,
            domain,
            index,
            layer_results[layer_key],
            previous_result,
        )

        previous_result = layer_results[layer_key]

    total_tokens = sum(
        layer_results[layer_key]["total_tokens"]
        for layer_key in LAYER_KEYS
    )

    total_latency_ms = sum(
        layer_results[layer_key]["latency_ms"]
        for layer_key in LAYER_KEYS
    )

    return {
        "problem_id": problem["id"],
        "domain": domain,
        "layers": layer_traces,
        "layer_correctness": layer_correctness,
        "minimum_sufficient_layer": minimum_layer,
        "all_layers_failed": minimum_layer is None,
        "layer_1_correct": layer_correctness[
            "layer_1"
        ],
        "layer_4_correct": layer_correctness[
            "layer_4"
        ],
        "beneficial_escalation": (
            not layer_correctness["layer_1"]
            and minimum_layer is not None
        ),
        "total_counterfactual_tokens": total_tokens,
        "total_counterfactual_latency_ms": (
            total_latency_ms
        ),
    }


def write_jsonl(
    records: list[dict[str, Any]],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing_records: dict[str, dict[str, Any]] = {}

    if path.exists():
        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                record = json.loads(line)
                problem_id = record.get("problem_id")

                if problem_id is not None:
                    existing_records[str(problem_id)] = record

    for record in records:
        problem_id = record.get("problem_id")

        if problem_id is not None:
            existing_records[str(problem_id)] = record

    merged_records = list(existing_records.values())

    merged_records.sort(
        key=lambda record: str(
            record.get("problem_id", "")
        )
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for record in merged_records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )


def write_metadata(
    path: Path,
    *,
    domain: str,
    problem_file: Path,
    problem_count: int,
    temperature: float,
    seed: int,
) -> None:
    metadata = {
        "created_at": time.time(),
        "domain": domain,
        "problem_file": str(problem_file),
        "problem_count": problem_count,
        "temperature": temperature,
        "seed": seed,
        "contextual_layers": True,
        "layers": {
            layer_key: {
                "model_key": MODEL_CONFIG[
                    layer_key
                ]["key"],
                "model": MODEL_CONFIG[
                    layer_key
                ]["model"],
                "name": MODEL_CONFIG[
                    layer_key
                ]["name"],
            }
            for layer_key in LAYER_KEYS
        },
    }

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()

    if (
        args.limit is not None
        and args.limit <= 0
    ):
        raise ValueError(
            "--limit must be a positive integer."
        )

    if args.temperature < 0:
        raise ValueError(
            "--temperature must be >= 0."
        )

    problem_file = resolve_problem_file(
        args.domain,
        args.problems_file,
    )

    problems = load_problems(
        problem_file,
        args.limit,
    )

    if args.start_from is not None:
        start_index = next(
            (
                index
                for index, problem in enumerate(problems)
                if str(problem["id"]) == args.start_from
            ),
            None,
        )

        if start_index is None:
            raise ValueError(
                f"Problem ID not found: {args.start_from}"
            )

        problems = problems[start_index:]

    if args.end_at is not None:
        end_index = next(
            (
                index
                for index, problem in enumerate(problems)
                if str(problem["id"]) == args.end_at
            ),
            None,
        )

        if end_index is None:
            raise ValueError(
                f"Problem ID not found: {args.end_at}"
            )

        problems = problems[:end_index + 1]

    if not problems:
        raise ValueError(
            "No valid problems were loaded."
        )

    print(
        f"Loaded {len(problems)} problems."
    )

    for index, layer_key in enumerate(
        LAYER_KEYS,
        start=1,
    ):
        print(
            f"Layer {index}: "
            f"{MODEL_CONFIG[layer_key]['model']}"
        )

    experiment_start = time.perf_counter()

    print(
        f"Experiment started at "
        f"{time.strftime('%H:%M:%S')}"
    )
    print()

    records = []

    for index, problem in enumerate(
        problems,
        start=1,
    ):
        problem_id = str(
            problem["id"]
        )

        problem_start = time.perf_counter()

        if args.verbose:
            print(
                f"[{index}/{len(problems)}] "
                f"{problem_id} | "
                f"total elapsed="
                f"{problem_start - experiment_start:.1f}s"
            )

        layer_results = {}

        previous_result = None

        for layer_index, layer_key in enumerate(
            LAYER_KEYS,
            start=1,
        ):
            layer_start = time.perf_counter()

            if args.verbose:
                print(
                    f"  Layer {layer_index}/4 "
                    f"({MODEL_CONFIG[layer_key]['model']}) "
                    f"| total elapsed="
                    f"{layer_start - experiment_start:.1f}s"
                )

            stop_event = threading.Event()
            timer_thread = None

            if args.verbose:
                timer_thread = threading.Thread(
                    target=run_elapsed_timer,
                    args=(
                        layer_start,
                        stop_event,
                        f"  Layer {layer_index}/4",
                    ),
                    daemon=True,
                )
                timer_thread.start()

            try:
                layer_results[layer_key] = run_model(
                    problem=problem,
                    model_key=layer_key,
                    domain=args.domain,
                    temperature=args.temperature,
                    seed=args.seed,
                    previous_result=previous_result,
                )
            finally:
                if timer_thread is not None:
                    stop_event.set()
                    timer_thread.join()

            previous_result = layer_results[layer_key]

            result = layer_results[layer_key]
            layer_elapsed = (
                time.perf_counter() - layer_start
            )
            total_elapsed = (
                time.perf_counter()
                - experiment_start
            )

            if args.verbose:
                print(
                    f"\r  Layer {layer_index}/4 "
                    f"completed | "
                    f"elapsed={layer_elapsed:.1f}s | "
                    f"total elapsed="
                    f"{total_elapsed:.1f}s | "
                    f"correct={result['correct']} | "
                    f"tokens={result['total_tokens']} | "
                    f"latency="
                    f"{result['latency_ms']:.0f}ms"
                )

        record = build_counterfactual_record(
            problem=problem,
            domain=args.domain,
            layer_results=layer_results,
        )

        records.append(record)

        problem_elapsed = (
            time.perf_counter() - problem_start
        )
        total_elapsed = (
            time.perf_counter()
            - experiment_start
        )

        if args.verbose:
            print(
                f"  minimum_sufficient_layer="
                f"{record['minimum_sufficient_layer']} | "
                f"problem elapsed="
                f"{problem_elapsed:.1f}s | "
                f"total elapsed="
                f"{total_elapsed:.1f}s"
            )
            print()

    experiment_elapsed = (
        time.perf_counter() - experiment_start
    )

    write_jsonl(
        records,
        args.output,
    )

    summary_output = args.output.with_name(
        f"{args.output.stem}_summary"
        f"{args.output.suffix}"
    )

    summary_records = []

    for record in records:
        correctness = record[
            "layer_correctness"
        ]

        summary_records.append(
            {
                "problem_id": record[
                    "problem_id"
                ],
                "domain": record[
                    "domain"
                ],
                "layer_1_correct": correctness[
                    "layer_1"
                ],
                "layer_2_correct": correctness[
                    "layer_2"
                ],
                "layer_3_correct": correctness[
                    "layer_3"
                ],
                "layer_4_correct": correctness[
                    "layer_4"
                ],
                "minimum_sufficient_layer": record[
                    "minimum_sufficient_layer"
                ],
                "all_layers_failed": record[
                    "all_layers_failed"
                ],
            }
        )

    write_jsonl(
        summary_records,
        summary_output,
    )

    metadata_output = args.output.with_name(
        f"{args.output.stem}_metadata.json"
    )

    write_metadata(
        metadata_output,
        domain=args.domain,
        problem_file=problem_file,
        problem_count=len(problems),
        temperature=args.temperature,
        seed=args.seed,
    )

    layer_counts = {
        layer_key: sum(
            1
            for record in records
            if record["layer_correctness"][
                layer_key
            ]
        )
        for layer_key in LAYER_KEYS
    }

    minimum_counts: dict[str, int] = {}

    for record in records:
        layer = record[
            "minimum_sufficient_layer"
        ]

        key = (
            str(layer)
            if layer is not None
            else "none"
        )

        minimum_counts[key] = (
            minimum_counts.get(
                key,
                0,
            )
            + 1
        )

    print()
    print(
        f"Problems evaluated: {len(records)}"
    )

    for index, layer_key in enumerate(
        LAYER_KEYS,
        start=1,
    ):
        print(
            f"Layer {index} correct: "
            f"{layer_counts[layer_key]}/"
            f"{len(records)}"
        )

    print(
        "Minimum sufficient layer:"
    )

    for index in range(1, 5):
        print(
            f"  Layer {index}: "
            f"{minimum_counts.get(str(index), 0)}"
        )

    print(
        f"  None: "
        f"{minimum_counts.get('none', 0)}"
    )

    print()
    print(
        f"Total experiment time: "
        f"{experiment_elapsed:.1f}s "
        f"({experiment_elapsed / 60:.1f} min)"
    )
    print(
        f"Counterfactual records: {args.output}"
    )
    print(
        f"Summary records: {summary_output}"
    )
    print(
        f"Metadata: {metadata_output}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())