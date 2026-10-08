from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from phase3.collect import (
    collect_split,
    load_existing_records,
)
from phase3.config import (
    MAX_FALSE_STOP_RATE,
    ROUTERS_ROOT,
    TATQA_SUBSET_SIZES,
    TRACES_ROOT,
)
from phase3.evaluation import (
    evaluate_test,
    print_evaluation,
)
from phase3.router import (
    load_records,
    train_all_routers,
)


def collect_command(
    split: str,
    limit: int | None,
    subset_size: int | None,
    counterfactual: bool,
    output: Path | None,
    start_from: int | None,
    end_at: int | None,
) -> None:
    suffix = (
        "_counterfactual"
        if counterfactual
        else ""
    )

    output_path = (
        output
        if output is not None
        else (
            TRACES_ROOT
            / f"phase3_tatqa_{split}"
            f"{suffix}.jsonl"
        )
    )

    if (
        start_from is not None
        and end_at is not None
        and end_at < start_from
    ):
        raise ValueError(
            "--end-at must be greater than or equal "
            "to --start-from."
        )

    if (
        end_at is not None
        and limit is not None
    ):
        raise ValueError(
            "Use either --limit or --end-at, not both."
        )

    effective_limit = (
        end_at
        if end_at is not None
        else limit
    )

    existing_records = load_existing_records(
        output_path
    )

    completed_ids = {
        str(
            record.get(
                "problem_id"
            )
        )
        for record in existing_records
        if record.get(
            "problem_id"
        )
        is not None
    }

    if start_from is not None:
        completed_ids = set()

        print()
        print(
            "=" * 72
        )
        print(
            "START-FROM MODE"
        )
        print(
            "=" * 72
        )
        print(
            f"Starting from problem: "
            f"{start_from}"
        )

        if end_at is not None:
            print(
                f"Ending at problem: "
                f"{end_at}"
            )

        print(
            "Existing records will be preserved "
            "except for problems that are rerun."
        )
        print(
            "The rerun problem ID will be replaced "
            "in the JSONL file."
        )
        print(
            f"Output: {output_path}"
        )
        print(
            "=" * 72
        )

    elif existing_records:
        print()
        print(
            "=" * 72
        )
        print(
            "RESUME CHECKPOINT"
        )
        print(
            "=" * 72
        )
        print(
            f"Existing records : "
            f"{len(existing_records)}"
        )
        print(
            f"Completed IDs    : "
            f"{len(completed_ids)}"
        )
        print(
            f"Output           : "
            f"{output_path}"
        )
        print(
            "Already completed records will be skipped."
        )
        print(
            "=" * 72
        )

    records = collect_split(
        domain="finance",
        split=split,
        limit=effective_limit,
        subset_size=subset_size,
        counterfactual=counterfactual,
        skip_ids=completed_ids,
        start_from=start_from,
        output_path=output_path,
    )

    saved_records = load_existing_records(
        output_path
    )

    print()
    print(
        "=" * 72
    )
    print(
        "COLLECTION COMPLETE"
    )
    print(
        "=" * 72
    )
    print(
        f"New/rerun records : "
        f"{len(records)}"
    )
    print(
        f"Previously/currently saved: "
        f"{len(saved_records)}"
    )
    print(
        f"Output            : "
        f"{output_path}"
    )
    print(
        "=" * 72
    )


def train_command(
    train_path: Path,
    validation_path: Path,
    output_directory: Path,
) -> None:
    train_records = load_records(
        train_path
    )

    validation_records = load_records(
        validation_path
    )

    results = train_all_routers(
        train_records,
        validation_records,
        output_directory,
        MAX_FALSE_STOP_RATE,
    )

    print()
    print(
        json.dumps(
            results,
            indent=2,
        )
    )


def test_command(
    test_path: Path,
    router_directory: Path,
    output: Path | None,
) -> None:
    results = evaluate_test(
        test_path,
        router_directory,
    )

    print_evaluation(
        results
    )

    if output is not None:
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with output.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                results,
                handle,
                indent=2,
            )


def _layer_number(
    layer_key: str,
) -> int:
    match = (
        __import__("re")
        .search(
            r"(\d+)$",
            str(layer_key),
        )
    )

    if match:
        return int(
            match.group(1)
        )

    return 999


def _is_failed_layer(
    layer: dict[str, Any],
) -> bool:
    verification = layer.get(
        "verification",
        {},
    )

    if "correct" in layer:
        return not bool(
            layer.get(
                "correct"
            )
        )

    if isinstance(
        verification,
        dict,
    ) and "passed" in verification:
        return not bool(
            verification.get(
                "passed"
            )
        )

    return False


def _get_verification(
    layer: dict[str, Any],
) -> dict[str, Any]:
    verification = layer.get(
        "verification",
        {},
    )

    if isinstance(
        verification,
        dict,
    ):
        return verification

    return {}


def _print_failure(
    record: dict[str, Any],
    layer_key: str,
    layer: dict[str, Any],
    show_response: bool,
) -> None:
    verification = _get_verification(
        layer
    )

    print()
    print(
        "-" * 72
    )
    print(
        f"Problem ID : "
        f"{record.get('problem_id')}"
    )
    print(
        f"Question ID: "
        f"{record.get('question_id')}"
    )
    print(
        f"Layer      : "
        f"{layer_key}"
    )
    print(
        f"Answer type: "
        f"{record.get('answer_type')}"
    )
    print(
        f"Scale      : "
        f"{record.get('scale') or 'none'}"
    )
    print(
        f"Minimum sufficient layer: "
        f"{record.get('minimum_sufficient_layer')}"
    )
    print(
        "-" * 72
    )
    print(
        "QUESTION:"
    )
    print(
        record.get(
            "question",
            "",
        )
    )
    print()
    print(
        "GOLD ANSWER:"
    )
    print(
        json.dumps(
            record.get(
                "answer"
            ),
            ensure_ascii=False,
        )
    )
    print()
    print(
        "CANDIDATE ANSWER:"
    )
    print(
        verification.get(
            "candidate_answer",
            layer.get(
                "answer",
                "",
            ),
        )
    )
    print()
    print(
        "VERIFIER RESULT:"
    )
    print(
        f"passed   : "
        f"{verification.get('passed', layer.get('correct'))}"
    )
    print(
        f"reason   : "
        f"{verification.get('reason', '')}"
    )

    if "error" in verification:
        print(
            f"error    : "
            f"{verification.get('error')}"
        )

    if "model" in verification:
        print(
            f"model    : "
            f"{verification.get('model')}"
        )

    if show_response:
        print()
        print(
            "FULL MODEL RESPONSE:"
        )
        print(
            layer.get(
                "response",
                "",
            )
        )

    print(
        "-" * 72
    )


def analyze_failures(
    records: list[dict[str, Any]],
    split: str,
    show_response: bool,
) -> None:
    failure_count = 0
    failure_by_type: Counter[str] = Counter()
    failure_by_reason: Counter[str] = Counter()
    failure_by_layer: Counter[str] = Counter()

    for index, record in enumerate(
        records,
        start=1,
    ):
        layers = record.get(
            "layers",
            {},
        )

        if not isinstance(
            layers,
            dict,
        ):
            continue

        for layer_key in sorted(
            layers,
            key=_layer_number,
        ):
            layer = layers.get(
                layer_key
            )

            if not isinstance(
                layer,
                dict,
            ):
                continue

            if not _is_failed_layer(
                layer
            ):
                continue

            failure_count += 1

            answer_type = str(
                record.get(
                    "answer_type",
                    "unknown",
                )
            )

            verification = _get_verification(
                layer
            )

            reason = str(
                verification.get(
                    "reason",
                    "unknown",
                )
            )

            failure_by_type[
                answer_type
            ] += 1

            failure_by_reason[
                reason
            ] += 1

            failure_by_layer[
                layer_key
            ] += 1

            _print_failure(
                record,
                layer_key,
                layer,
                show_response,
            )

    print()
    print(
        "=" * 72
    )
    print(
        f"{split.upper()} FAILURE SUMMARY"
    )
    print(
        "=" * 72
    )
    print(
        f"Failed layer evaluations: "
        f"{failure_count}"
    )

    print()
    print(
        "BY ANSWER TYPE:"
    )

    for answer_type, count in sorted(
        failure_by_type.items()
    ):
        print(
            f"  {answer_type}: "
            f"{count}"
        )

    print()
    print(
        "BY VERIFIER REASON:"
    )

    for reason, count in sorted(
        failure_by_reason.items()
    ):
        print(
            f"  {reason}: "
            f"{count}"
        )

    print()
    print(
        "BY LAYER:"
    )

    for layer_key, count in sorted(
        failure_by_layer.items(),
        key=lambda item: _layer_number(
            item[0]
        ),
    ):
        print(
            f"  {layer_key}: "
            f"{count}"
        )

    print(
        "=" * 72
    )


def analyze_command(
    train_path: Path,
    validation_path: Path,
    test_path: Path,
    failures: bool,
    show_response: bool,
) -> None:
    paths = {
        "train": train_path,
        "validation": validation_path,
        "test": test_path,
    }

    print()
    print(
        "=" * 72
    )
    print(
        "PHASE III TAT-QA TRACE ANALYSIS"
    )
    print(
        "=" * 72
    )

    for split, path in paths.items():
        if not path.exists():
            print()
            print(
                f"{split.upper()}: MISSING"
            )
            print(
                f"  {path}"
            )
            continue

        records = load_records(
            path
        )

        print()
        print(
            f"{split.upper()}: "
            f"{len(records)} records"
        )

        answer_types: Counter[str] = Counter()

        for record in records:
            answer_type = str(
                record.get(
                    "answer_type",
                    "unknown",
                )
            )

            answer_types[
                answer_type
            ] += 1

        for answer_type, count in sorted(
            answer_types.items()
        ):
            print(
                f"  {answer_type}: "
                f"{count}"
            )

        if failures:
            analyze_failures(
                records,
                split,
                show_response,
            )

    print()
    print(
        "=" * 72
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    collect_parser = (
        subparsers.add_parser(
            "collect"
        )
    )

    collect_parser.add_argument(
        "--split",
        choices=(
            "train",
            "validation",
            "test",
        ),
        required=True,
    )

    collect_parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    collect_parser.add_argument(
        "--subset-size",
        type=int,
        default=None,
        help=(
            "Defaults to the configured "
            f"TAT-QA subset size: "
            f"{TATQA_SUBSET_SIZES}"
        ),
    )

    collect_parser.add_argument(
        "--counterfactual",
        action="store_true",
    )

    collect_parser.add_argument(
        "--start-from",
        type=int,
        default=None,
        help=(
            "Start from this 1-based problem position "
            "and overwrite rerun records."
        ),
    )

    collect_parser.add_argument(
        "--end-at",
        type=int,
        default=None,
        help=(
            "End at this 1-based problem position, "
            "inclusive."
        ),
    )

    collect_parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    train_parser = (
        subparsers.add_parser(
            "train"
        )
    )

    train_parser.add_argument(
        "--train",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_train.jsonl"
        ),
    )

    train_parser.add_argument(
        "--validation",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_validation"
            "_counterfactual.jsonl"
        ),
    )

    train_parser.add_argument(
        "--output",
        type=Path,
        default=ROUTERS_ROOT,
    )

    test_parser = (
        subparsers.add_parser(
            "test"
        )
    )

    test_parser.add_argument(
        "--test",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_test"
            "_counterfactual.jsonl"
        ),
    )

    test_parser.add_argument(
        "--routers",
        type=Path,
        default=ROUTERS_ROOT,
    )

    test_parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )

    analyze_parser = (
        subparsers.add_parser(
            "analyze"
        )
    )

    analyze_parser.add_argument(
        "--train",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_train.jsonl"
        ),
    )

    analyze_parser.add_argument(
        "--validation",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_validation"
            "_counterfactual.jsonl"
        ),
    )

    analyze_parser.add_argument(
        "--test",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_test"
            "_counterfactual.jsonl"
        ),
    )

    analyze_parser.add_argument(
        "--failures",
        action="store_true",
        help=(
            "Print every layer-level verification "
            "failure and summarize failure reasons."
        ),
    )

    analyze_parser.add_argument(
        "--show-response",
        action="store_true",
        help=(
            "Print the complete model response "
            "for every failed layer."
        ),
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "collect":
        if (
            args.limit is not None
            and args.limit <= 0
        ):
            raise ValueError(
                "--limit must be positive."
            )

        if (
            args.subset_size is not None
            and args.subset_size <= 0
        ):
            raise ValueError(
                "--subset-size must be positive."
            )

        if (
            args.start_from is not None
            and args.start_from <= 0
        ):
            raise ValueError(
                "--start-from must be positive."
            )

        if (
            args.end_at is not None
            and args.end_at <= 0
        ):
            raise ValueError(
                "--end-at must be positive."
            )

        if (
            args.limit is not None
            and args.end_at is not None
        ):
            raise ValueError(
                "Use either --limit or --end-at, not both."
            )

        if (
            args.start_from is not None
            and args.end_at is not None
            and args.end_at < args.start_from
        ):
            raise ValueError(
                "--end-at must be greater than or equal "
                "to --start-from."
            )

        collect_command(
            split=args.split,
            limit=args.limit,
            subset_size=args.subset_size,
            counterfactual=args.counterfactual,
            output=args.output,
            start_from=args.start_from,
            end_at=args.end_at,
        )

    elif args.command == "train":
        train_command(
            train_path=args.train,
            validation_path=args.validation,
            output_directory=args.output,
        )

    elif args.command == "test":
        test_command(
            test_path=args.test,
            router_directory=args.routers,
            output=args.output,
        )

    elif args.command == "analyze":
        if (
            args.show_response
            and not args.failures
        ):
            raise ValueError(
                "--show-response requires --failures."
            )

        analyze_command(
            train_path=args.train,
            validation_path=args.validation,
            test_path=args.test,
            failures=args.failures,
            show_response=args.show_response,
        )


if __name__ == "__main__":
    main()