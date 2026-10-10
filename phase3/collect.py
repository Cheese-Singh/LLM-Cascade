from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

from phase3.config import (
    LAYER_KEYS,
    PHASE3_MODEL_CONFIG,
    SEED,
    TATQA_SUBSET_SIZES,
    TEMPERATURE,
    TRACES_ROOT,
)
from phase3.dataset import (
    get_tatqa_split,
    get_tatqa_statistics,
    has_nonempty_gold_answer,
)
from phase3.signals import extract_signals
from phase3.tatqa_verifier import verify_tatqa
from run_baselines import call_model


def build_problem_context(
    problem: dict[str, Any],
    domain: str,
) -> str:
    if domain != "finance":
        return str(
            problem["prompt"]
        )

    table = problem.get(
        "table",
        [],
    )

    paragraphs = problem.get(
        "paragraphs",
        [],
    )

    table_context = "\n".join(
        " | ".join(
            str(cell)
            for cell in row
        )
        for row in table
        if isinstance(
            row,
            list,
        )
    )

    paragraph_context = "\n\n".join(
        str(paragraph)
        for paragraph in paragraphs
    )

    return (
        "Financial table:\n\n"
        f"{table_context}\n\n"
        "Financial report context:\n\n"
        f"{paragraph_context}\n\n"
        "Question:\n\n"
        f"{problem['prompt']}"
    )


def build_prompt(
    problem: dict[str, Any],
    domain: str,
    layer_index: int,
    previous_response: str | None,
) -> tuple[str, str]:
    context = build_problem_context(
        problem,
        domain,
    )

    if layer_index == 0:
        system_prompt = (
            "You are an expert quantitative "
            "problem solver working on a financial "
            "question-answering benchmark.\n\n"
            "Treat the supplied question, table, "
            "and report context as complete for the "
            "purpose of answering the question. "
            "Use only the supplied information. "
            "Do not reject the question merely "
            "because additional real-world information "
            "might normally be useful.\n\n"
            "Identify exactly what the question asks. "
            "For arithmetic and counting questions, "
            "perform the required calculation directly "
            "from the supplied information. For span "
            "questions, identify the requested answer "
            "from the supplied table or text. For "
            "multi-span questions, provide all required "
            "answer spans. For comparisons, compare "
            "the quantities actually requested.\n\n"
            "Give the best answer supported by the "
            "supplied information. If there is a "
            "real-world qualification, give the answer "
            "first and mention the qualification "
            "afterward rather than replacing the answer "
            "with 'cannot be determined'.\n\n"
            "Put the answer itself on a line beginning "
            "exactly with ANSWER:. Include the relevant "
            "unit or scale when appropriate. Keep "
            "reasoning concise. End with exactly one "
            "line beginning CONFIDENCE: followed by "
            "a number from 0.00 to 1.00."
        )
    else:
        system_prompt = (
            "You are an expert quantitative "
            "problem solver reviewing another model's "
            "solution to a financial QA benchmark "
            "question.\n\n"
            "Solve the original question yourself. "
            "Treat the supplied question, table, and "
            "report context as complete for the purpose "
            "of answering it. Use only the information "
            "provided. Do not reject the problem because "
            "additional real-world information might "
            "normally be useful.\n\n"
            "Use the previous response only as additional "
            "context. Correct it when necessary. Do not "
            "receive or infer any verification result, "
            "gold answer, or correctness signal.\n\n"
            "Identify exactly what the question asks. "
            "For arithmetic and counting questions, "
            "perform the required calculation directly "
            "from the supplied information. For span "
            "questions, identify the requested answer "
            "from the supplied table or text. For "
            "multi-span questions, provide all required "
            "answer spans. For comparisons, compare "
            "the quantities actually requested.\n\n"
            "Give the best answer supported by the "
            "supplied information. If there is a "
            "real-world qualification, give the answer "
            "first and mention the qualification "
            "afterward rather than replacing the answer "
            "with 'cannot be determined'.\n\n"
            "Put the answer itself on a line beginning "
            "exactly with ANSWER:. Include the relevant "
            "unit or scale when appropriate. Keep "
            "reasoning concise. End with exactly one "
            "line beginning CONFIDENCE: followed by "
            "a number from 0.00 to 1.00."
        )

    if layer_index == 0:
        return (
            system_prompt,
            context,
        )

    user_prompt = (
        f"{context}\n\n"
        "Previous layer response:\n\n"
        f"{previous_response or ''}"
    )

    return (
        system_prompt,
        user_prompt,
    )


def start_live_timer(
    prefix: str,
    started: float,
) -> tuple[
    threading.Event,
    threading.Thread,
]:
    stop_event = threading.Event()

    def update() -> None:
        while not stop_event.wait(
            0.5
        ):
            elapsed = (
                time.perf_counter()
                - started
            )

            print(
                f"\r{prefix} | "
                f"elapsed={elapsed:.1f}s",
                end="",
                flush=True,
            )

    thread = threading.Thread(
        target=update,
        daemon=True,
    )

    thread.start()

    return (
        stop_event,
        thread,
    )


def call_model_live(
    layer_key: str,
    system_prompt: str,
    user_prompt: str,
    layer_index: int,
) -> dict[str, Any]:
    model_name = PHASE3_MODEL_CONFIG[
        layer_key
    ]["model"]

    layer_number = (
        layer_index + 1
    )

    started = time.perf_counter()

    result_holder: dict[str, Any] = {}
    error_holder: list[BaseException] = []

    def worker() -> None:
        try:
            result_holder[
                "result"
            ] = call_model(
                model_key=layer_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=TEMPERATURE,
                seed=SEED,
            )
        except BaseException as exc:
            error_holder.append(
                exc
            )

    worker_thread = threading.Thread(
        target=worker,
        daemon=True,
    )

    worker_thread.start()

    print(
        f"  L{layer_number} "
        f"{model_name} | RUNNING | "
        f"elapsed=0.0s"
    )

    stop_event, timer_thread = (
        start_live_timer(
            prefix=(
                f"  L{layer_number} "
                f"{model_name} | RUNNING"
            ),
            started=started,
        )
    )

    try:
        while worker_thread.is_alive():
            worker_thread.join(
                timeout=0.25
            )
    except KeyboardInterrupt:
        stop_event.set()

        print()
        print(
            f"  L{layer_number} "
            f"{model_name} | INTERRUPTED"
        )

        raise
    finally:
        stop_event.set()
        timer_thread.join(
            timeout=1.0
        )

    elapsed = (
        time.perf_counter()
        - started
    )

    print(
        f"\r  L{layer_number} "
        f"{model_name} | COMPLETE | "
        f"elapsed={elapsed:.1f}s"
        + " " * 20
    )

    if error_holder:
        raise error_holder[0]

    if "result" not in result_holder:
        raise RuntimeError(
            "Model call completed without a result."
        )

    result = result_holder[
        "result"
    ]

    print(
        f"    tokens="
        f"{result.get('total_tokens', 0)} | "
        f"latency="
        f"{float(result.get('latency_ms', 0.0)) / 1000:.1f}s"
    )

    if result.get(
        "retries",
        0,
    ):
        print(
            f"    retries="
            f"{result['retries']}"
        )

    return result


def extract_answer_line(
    response: str,
) -> str:
    for line in response.splitlines():
        stripped = line.strip()

        if stripped.upper().startswith(
            "ANSWER:"
        ):
            return stripped[
                len("ANSWER:"):
            ].strip()

    return ""


def print_model_output(
    response: str,
) -> None:
    print()
    print(
        "  MODEL OUTPUT"
    )
    print(
        "  " + "-" * 68
    )

    if response.strip():
        print(
            response.rstrip()
        )
    else:
        print(
            "<empty response>"
        )

    print(
        "  " + "-" * 68
    )


def print_verification(
    verification: dict[str, Any],
) -> None:
    passed = bool(
        verification.get(
            "passed",
            False,
        )
    )

    print()
    print(
        "  VERIFICATION"
    )
    print(
        "  " + "-" * 68
    )

    print(
        f"  Result: "
        f"{'PASS' if passed else 'FAIL'}"
    )

    for key, value in verification.items():
        if key == "passed":
            continue

        if isinstance(
            value,
            (dict, list),
        ):
            try:
                rendered = json.dumps(
                    value,
                    ensure_ascii=False,
                )
            except (
                TypeError,
                ValueError,
            ):
                rendered = str(
                    value
                )
        else:
            rendered = str(
                value
            )

        print(
            f"  {key}: {rendered}"
        )

    print(
        "  " + "-" * 68
    )


def run_layer(
    problem: dict[str, Any],
    domain: str,
    layer_index: int,
    previous_response: str | None,
) -> dict[str, Any]:
    layer_key = LAYER_KEYS[
        layer_index
    ]

    system_prompt, user_prompt = (
        build_prompt(
            problem=problem,
            domain=domain,
            layer_index=layer_index,
            previous_response=previous_response,
        )
    )

    result = call_model_live(
        layer_key=layer_key,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        layer_index=layer_index,
    )

    response = str(
        result.get(
            "response",
            "",
        )
    )

    signals = extract_signals(
        response=response,
        problem=problem,
        previous_response=previous_response,
    )

    verification = verify_tatqa(
        problem=problem,
        response=response,
    )

    correct = bool(
        verification.get(
            "passed",
            False,
        )
    )

    print_model_output(
        response
    )

    print(
        f"  Extracted ANSWER: "
        f"{extract_answer_line(response) or '<not found>'}"
    )

    print_verification(
        verification
    )

    print(
        f"  Signals: "
        f"confidence={signals['confidence']:.3f} | "
        f"similarity="
        f"{signals['answer_similarity_previous']:.3f} | "
        f"length="
        f"{signals['response_length_score']:.3f} | "
        f"numeric_density="
        f"{signals['numeric_density']:.3f}"
    )

    return {
        "layer": layer_index + 1,
        "layer_key": layer_key,
        "model": PHASE3_MODEL_CONFIG[
            layer_key
        ]["model"],
        "response": response,
        "final_answer": extract_answer_line(
            response
        ),
        "signals": signals,
        "verification": verification,
        "correct": correct,
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


def collect_adaptive_problem(
    problem: dict[str, Any],
    domain: str,
    problem_index: int,
    total_problems: int,
) -> dict[str, Any]:
    started = time.perf_counter()

    print()
    print("=" * 72)
    print(
        f"PHASE III ADAPTIVE | "
        f"{problem_index}/{total_problems}"
    )
    print(
        f"ID: {problem['id']}"
    )
    print("=" * 72)

    layers: dict[
        str,
        dict[str, Any],
    ] = {}

    previous_response = None
    stopping_layer = None

    for layer_index in range(
        len(LAYER_KEYS)
    ):
        layer_key = LAYER_KEYS[
            layer_index
        ]

        result = run_layer(
            problem=problem,
            domain=domain,
            layer_index=layer_index,
            previous_response=previous_response,
        )

        layers[layer_key] = result

        previous_response = (
            result["response"]
        )

        if result["correct"]:
            stopping_layer = (
                layer_index + 1
            )

            print()
            print(
                f"ORACLE STOP → "
                f"L{stopping_layer}"
            )

            break

        if layer_index < (
            len(LAYER_KEYS) - 1
        ):
            print(
                f"  L{layer_index + 1} "
                f"incorrect → escalating "
                f"to L{layer_index + 2}"
            )

    total_tokens = sum(
        layer["total_tokens"]
        for layer in layers.values()
    )

    elapsed_ms = (
        time.perf_counter()
        - started
    ) * 1000.0

    print(
        f"Layers used: "
        f"{len(layers)}/{len(LAYER_KEYS)}"
    )

    print(
        f"Tokens used: "
        f"{total_tokens}"
    )

    print(
        f"Problem elapsed: "
        f"{elapsed_ms / 1000:.1f}s"
    )

    print(
        "=" * 72
    )

    return {
        "problem_id": str(
            problem["id"]
        ),
        "domain": domain,
        "dataset": "tatqa",
        "split": problem.get(
            "split",
            "",
        ),
        "prompt": problem["prompt"],
        "answer": problem.get(
            "answer"
        ),
        "answer_type": problem.get(
            "answer_type"
        ),
        "answer_from": problem.get(
            "answer_from"
        ),
        "scale": problem.get(
            "scale"
        ),
        "derivation": problem.get(
            "derivation"
        ),
        "req_comparison": problem.get(
            "req_comparison",
            False,
        ),
        "layers": layers,
        "minimum_sufficient_layer": (
            stopping_layer
        ),
        "all_observed_layers_failed": (
            stopping_layer is None
        ),
        "collection_mode": "adaptive",
        "layers_used": len(layers),
        "total_tokens": total_tokens,
        "total_latency_ms": elapsed_ms,
    }


def collect_counterfactual_problem(
    problem: dict[str, Any],
    domain: str,
    problem_index: int,
    total_problems: int,
) -> dict[str, Any]:
    started = time.perf_counter()

    print()
    print("=" * 72)
    print(
        f"PHASE III COUNTERFACTUAL | "
        f"{problem_index}/{total_problems}"
    )
    print(
        f"ID: {problem['id']}"
    )
    print("=" * 72)

    layers: dict[
        str,
        dict[str, Any],
    ] = {}

    previous_response = None

    for layer_index in range(
        len(LAYER_KEYS)
    ):
        layer_key = LAYER_KEYS[
            layer_index
        ]

        result = run_layer(
            problem=problem,
            domain=domain,
            layer_index=layer_index,
            previous_response=previous_response,
        )

        layers[layer_key] = result

        previous_response = (
            result["response"]
        )

        print(
            f"  Counterfactual continuation: "
            f"L{layer_index + 1}"
        )

    blank_layers = [
        layer_key
        for layer_key, layer in layers.items()
        if not str(layer.get("response") or "").strip()
    ]
    if blank_layers:
        raise ValueError(
            f"Counterfactual collection for problem "
            f"{problem['id']} returned blank responses "
            f"for {', '.join(blank_layers)}; the record "
            "will not be saved."
        )

    minimum_sufficient_layer = None

    for layer_index, layer_key in enumerate(
        LAYER_KEYS,
        start=1,
    ):
        if layers[
            layer_key
        ]["correct"]:
            minimum_sufficient_layer = (
                layer_index
            )
            break

    total_tokens = sum(
        layer["total_tokens"]
        for layer in layers.values()
    )

    elapsed_ms = (
        time.perf_counter()
        - started
    ) * 1000.0

    oracle_text = (
        f"L{minimum_sufficient_layer}"
        if minimum_sufficient_layer
        else "NONE"
    )

    print()
    print(
        f"Oracle sufficient layer: "
        f"{oracle_text}"
    )

    print(
        f"Counterfactual tokens: "
        f"{total_tokens}"
    )

    print(
        f"Problem elapsed: "
        f"{elapsed_ms / 1000:.1f}s"
    )

    print(
        "=" * 72
    )

    return {
        "problem_id": str(
            problem["id"]
        ),
        "domain": domain,
        "dataset": "tatqa",
        "split": problem.get(
            "split",
            "",
        ),
        "prompt": problem["prompt"],
        "answer": problem.get(
            "answer"
        ),
        "answer_type": problem.get(
            "answer_type"
        ),
        "answer_from": problem.get(
            "answer_from"
        ),
        "scale": problem.get(
            "scale"
        ),
        "derivation": problem.get(
            "derivation"
        ),
        "req_comparison": problem.get(
            "req_comparison",
            False,
        ),
        "layers": layers,
        "minimum_sufficient_layer": (
            minimum_sufficient_layer
        ),
        "all_observed_layers_failed": (
            minimum_sufficient_layer
            is None
        ),
        "collection_mode": "counterfactual",
        "layers_used": len(LAYER_KEYS),
        "total_tokens": total_tokens,
        "total_latency_ms": elapsed_ms,
    }


def load_existing_records(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line_number, line in enumerate(
            handle,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(
                    line
                )
            except json.JSONDecodeError as exc:
                print(
                    f"WARNING: skipping invalid JSON "
                    f"at {path}:{line_number}: {exc}"
                )
                continue

            if not isinstance(
                record,
                dict,
            ):
                print(
                    f"WARNING: skipping non-object "
                    f"record at {path}:{line_number}"
                )
                continue

            records.append(record)

    return records


def save_record(
    record: dict[str, Any],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    problem_id = str(
        record["problem_id"]
    )

    existing_records = load_existing_records(
        path
    )

    replaced = False
    updated_records = []

    for existing_record in existing_records:
        existing_id = existing_record.get(
            "problem_id"
        )

        if (
            existing_id is not None
            and str(existing_id) == problem_id
        ):
            updated_records.append(
                record
            )
            replaced = True
        else:
            updated_records.append(
                existing_record
            )

    if not replaced:
        updated_records.append(
            record
        )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for updated_record in updated_records:
            handle.write(
                json.dumps(
                    updated_record,
                    ensure_ascii=False,
                )
                + "\n"
            )

        handle.flush()

    temporary_path.replace(
        path
    )

    print()
    print(
        f"SAVED TO DISK: {path}"
    )

    if replaced:
        print(
            f"  Replaced existing record: "
            f"problem_id={problem_id}"
        )
    else:
        print(
            f"  Saved new record: "
            f"problem_id={problem_id}"
        )


def write_jsonl(
    records: list[dict[str, Any]],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )


def collect_split(
    domain: str,
    split: str,
    limit: int | None = None,
    subset_size: int | None = None,
    counterfactual: bool = False,
    skip_ids: set[str] | None = None,
    start_from: int | None = None,
    output_path: Path | None = None,
    ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if domain != "finance":
        raise ValueError(
            f"Unsupported Phase III domain: "
            f"{domain}"
        )

    if skip_ids is None:
        skip_ids = set()

    if ids is None:
        ids = set()

    if subset_size is None:
        subset_size = (
            TATQA_SUBSET_SIZES[
                split
            ]
        )

    records = get_tatqa_split(
        split,
        subset_size=None if ids else subset_size,
        seed=SEED,
    )

    if limit is not None:
        records = records[
            :limit
        ]

    if start_from is not None:
        if start_from <= 0:
            raise ValueError(
                "--start-from must be "
                "a positive integer."
            )

        if start_from > len(records):
            print()
            print(
                f"WARNING: --start-from {start_from} "
                f"is beyond the available "
                f"{len(records)} problems."
            )

            return []

        records = records[
            start_from - 1:
        ]

    original_total = len(records)

    if ids:
        selected_records = [
            record
            for record in records
            if str(
                record["id"]
            ) in ids
        ]

        if counterfactual:
            selected_records = [
                record
                for record in selected_records
                if has_nonempty_gold_answer(record)
            ]

        found_ids = {
            str(record["id"])
            for record in selected_records
        }
        records = [
            record
            for record in selected_records
            if str(record["id"]) not in skip_ids
        ]

        missing_ids = sorted(
            ids - found_ids
        )

        if missing_ids:
            print()
            print(
                "WARNING: The following requested "
                "IDs were not found in the selected "
                f"{split} subset:"
            )

            for problem_id in missing_ids:
                print(
                    f"  {problem_id}"
                )

    elif skip_ids:
        records = [
            record
            for record in records
            if str(
                record["id"]
            ) not in skip_ids
        ]

    total = len(records)

    print()
    print("=" * 72)
    print(
        "PHASE III "
        + (
            "COUNTERFACTUAL"
            if counterfactual
            else "ADAPTIVE"
        )
        + " COLLECTION"
    )
    print("=" * 72)

    print(
        "Dataset : TAT-QA"
    )

    print(
        f"Domain  : {domain}"
    )

    print(
        f"Split   : {split}"
    )

    print(
        f"Subset  : {subset_size}"
    )

    if ids:
        print(
            f"IDs     : {len(ids)} requested"
        )

    if start_from is not None:
        print(
            f"Start   : problem {start_from}"
        )

    print(
        f"Problems: {total}"
    )

    if skip_ids and not ids:
        print(
            f"Skipped : "
            f"{original_total - total} "
            f"already completed"
        )

    print(
        "Mode    : "
        + (
            "counterfactual"
            if counterfactual
            else "adaptive"
        )
    )

    if output_path is not None:
        print(
            f"Output  : {output_path}"
        )

    print("=" * 72)

    if total == 0:
        print()
        print(
            "No problems to collect."
        )
        return []

    results = []

    experiment_start = (
        time.perf_counter()
    )

    try:
        for index, problem in enumerate(
            records,
            start=1,
        ):
            result_index = index

            result_total = total

            if start_from is not None:
                result_index = (
                    start_from - 1
                ) + index

                result_total = (
                    start_from - 1
                ) + total

            if counterfactual:
                result = (
                    collect_counterfactual_problem(
                        problem=problem,
                        domain=domain,
                        problem_index=result_index,
                        total_problems=result_total,
                    )
                )
            else:
                result = (
                    collect_adaptive_problem(
                        problem=problem,
                        domain=domain,
                        problem_index=result_index,
                        total_problems=result_total,
                    )
                )

            results.append(
                result
            )

            if output_path is not None:
                save_record(
                    result,
                    output_path,
                )

            elapsed = (
                time.perf_counter()
                - experiment_start
            )

            rate = index / max(
                elapsed,
                1e-9,
            )

            remaining = total - index

            eta = (
                remaining / rate
                if rate > 0
                else 0.0
            )

            print(
                f"Progress: {index}/{total} "
                f"({100 * index / total:.2f}%) | "
                f"elapsed={elapsed / 60:.1f} min | "
                f"ETA={eta / 60:.1f} min"
            )

    except KeyboardInterrupt:
        print()
        print("=" * 72)
        print(
            "COLLECTION INTERRUPTED"
        )
        print("=" * 72)

        print(
            f"Completed this run: "
            f"{len(results)}"
        )

        if output_path is not None:
            saved_records = load_existing_records(
                output_path
            )

            print(
                f"Records currently on disk: "
                f"{len(saved_records)}"
            )

            print(
                f"Output: {output_path}"
            )

        print(
            "All previously completed problems "
            "were already saved to disk."
        )

        print("=" * 72)

        raise

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--domain",
        default="finance",
        choices=("finance",),
    )

    parser.add_argument(
        "--split",
        default="train",
        choices=(
            "train",
            "validation",
            "test",
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--subset-size",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--start-from",
        type=int,
        default=None,
        help=(
            "Start from this 1-based problem position "
            "in the selected subset. The existing record "
            "for that problem will be overwritten."
        ),
    )

    parser.add_argument(
        "--ids",
        nargs="+",
        default=None,
        help=(
            "Run only the specified problem IDs. "
            "Existing records for these IDs are rerun "
            "and overwritten."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--counterfactual",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if (
        args.limit is not None
        and args.limit <= 0
    ):
        raise ValueError(
            "--limit must be a positive integer."
        )

    if (
        args.subset_size is not None
        and args.subset_size <= 0
    ):
        raise ValueError(
            "--subset-size must be a positive integer."
        )

    if (
        args.start_from is not None
        and args.start_from <= 0
    ):
        raise ValueError(
            "--start-from must be a positive integer."
        )

    requested_ids = (
        {
            str(problem_id)
            for problem_id in args.ids
        }
        if args.ids
        else set()
    )

    suffix = (
        "_counterfactual"
        if args.counterfactual
        else ""
    )

    output_path = (
        args.output
        if args.output is not None
        else (
            TRACES_ROOT
            / f"phase3_tatqa_"
            f"{args.split}"
            f"{suffix}.jsonl"
        )
    )

    existing_records = load_existing_records(
        output_path
    )

    completed_ids = {
        str(record.get("problem_id"))
        for record in existing_records
        if record.get("problem_id") is not None
    }

    if (
        args.start_from is not None
        or requested_ids
    ):
        completed_ids = set()

    records = collect_split(
        domain=args.domain,
        split=args.split,
        limit=args.limit,
        subset_size=args.subset_size,
        counterfactual=args.counterfactual,
        skip_ids=completed_ids,
        start_from=args.start_from,
        output_path=output_path,
        ids=requested_ids,
    )

    saved_records = load_existing_records(
        output_path
    )

    print()
    print("=" * 72)
    print(
        "COLLECTION COMPLETE"
    )
    print("=" * 72)

    print(
        f"Output: {output_path}"
    )

    print(
        f"Records completed this run: "
        f"{len(records)}"
    )

    print(
        f"Records currently saved: "
        f"{len(saved_records)}"
    )

    print(
        f"Full dataset: "
        f"{get_tatqa_statistics()}"
    )

    print("=" * 72)


if __name__ == "__main__":
    main()