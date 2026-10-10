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
    PHASE3_ROOT,
    EXTRA_TRAIN_SELECTION_PATH,
    EXTRA_TRAIN_TRACE_PATH,
    MAX_FALSE_STOP_RATE,
    ROUTERS_ROOT,
    TATQA_SUBSET_SIZES,
    TRACES_ROOT,
)
from phase3.dataset import (
    get_tatqa_split,
    has_nonempty_gold_answer,
    select_additional_training_records,
)
from phase3.evaluation import (
    evaluate_test,
    print_evaluation,
)
from phase3.router import (
    audit_features,
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
    additional_training_count: int | None,
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
            EXTRA_TRAIN_TRACE_PATH
            if additional_training_count is not None
            else (
                TRACES_ROOT
                / f"phase3_tatqa_{split}"
                f"{suffix}.jsonl"
            )
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

    if additional_training_count is not None:
        if split != "train" or not counterfactual:
            raise ValueError(
                "--additional-training-count requires "
                "--split train and --counterfactual."
            )
        if any(
            value is not None
            for value in (limit, subset_size, start_from, end_at)
        ):
            raise ValueError(
                "--additional-training-count cannot be combined "
                "with --limit, --subset-size, --start-from, or --end-at."
            )
        if additional_training_count <= 0:
            raise ValueError(
                "--additional-training-count must be positive."
            )
        if not output_path.resolve().is_relative_to(
            PHASE3_ROOT.resolve()
        ):
            raise ValueError(
                "Additional training traces must be saved under "
                f"{PHASE3_ROOT}."
            )

        selection_path = EXTRA_TRAIN_SELECTION_PATH

        if selection_path.exists():
            selection_records = load_existing_records(
                selection_path
            )
            if len(selection_records) != additional_training_count:
                raise ValueError(
                    f"Existing selection file {selection_path} contains "
                    f"{len(selection_records)} records, but "
                    f"{additional_training_count} were requested."
                )
            if any(
                record.get("split") != "train"
                or record.get("gold_answer_nonempty") is not True
                or not str(record.get("problem_id") or "")
                for record in selection_records
            ):
                raise ValueError(
                    f"Selection manifest {selection_path} contains "
                    "invalid or non-training entries."
                )
        else:
            training_pool = get_tatqa_split(
                "train",
                subset_size=None,
            )
            base_path = (
                TRACES_ROOT
                / "phase3_tatqa_train.jsonl"
            )
            counterfactual_path = (
                TRACES_ROOT
                / "phase3_tatqa_train_counterfactual.jsonl"
            )
            known_records = (
                load_existing_records(base_path)
                + load_existing_records(counterfactual_path)
                + load_existing_records(output_path)
            )
            excluded_ids = {
                str(record.get("problem_id"))
                for record in known_records
                if record.get("problem_id") is not None
            }
            eligible_count = sum(
                has_nonempty_gold_answer(record)
                and str(record.get("id", "")) not in excluded_ids
                for record in training_pool
            )
            selected = select_additional_training_records(
                training_pool,
                additional_training_count,
                excluded_ids,
            )
            selection_records = [
                {
                    "problem_id": record["id"],
                    "split": "train",
                    "answer_type": record.get("answer_type"),
                    "gold_answer_nonempty": True,
                }
                for record in selected
            ]
            selection_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            with selection_path.open(
                "w",
                encoding="utf-8",
            ) as handle:
                for record in selection_records:
                    handle.write(
                        json.dumps(record) + "\n"
                    )
            print(
                f"Selected {len(selected)} of {eligible_count} "
                "eligible new training questions with non-empty gold answers."
            )
            print(
                f"Selection manifest: {selection_path}"
            )

        requested_ids = {
            str(record["problem_id"])
            for record in selection_records
        }
        if len(requested_ids) != additional_training_count:
            raise ValueError(
                "The saved selection manifest contains duplicate or missing IDs."
            )

        existing_records = load_existing_records(
            output_path
        )
        completed_ids = {
            str(record.get("problem_id"))
            for record in existing_records
            if record.get("problem_id") is not None
            and set(record.get("layers", {})) == set(
                ("layer_1", "layer_2", "layer_3", "layer_4")
            )
            and all(
                str(layer.get("response") or "").strip()
                for layer in record.get("layers", {}).values()
            )
        }
        records = collect_split(
            domain="finance",
            split="train",
            counterfactual=True,
            skip_ids=completed_ids,
            output_path=output_path,
            ids=requested_ids,
        )
        saved_records = load_existing_records(
            output_path
        )
        saved_by_id = {
            str(record.get("problem_id")): record
            for record in saved_records
        }
        invalid_ids = [
            problem_id
            for problem_id in requested_ids
            if problem_id in saved_by_id
            and (
                not has_nonempty_gold_answer(
                    {"answer": saved_by_id[problem_id].get("answer")}
                )
                or set(saved_by_id[problem_id].get("layers", {}))
                != {"layer_1", "layer_2", "layer_3", "layer_4"}
                or any(
                    not str(layer.get("response") or "").strip()
                    for layer in saved_by_id[problem_id]
                    .get("layers", {})
                    .values()
                )
            )
        ]
        if invalid_ids:
            raise ValueError(
                "The additional training trace contains incomplete records: "
                + ", ".join(sorted(invalid_ids))
            )
        missing_ids = requested_ids - set(saved_by_id)
        if missing_ids:
            raise RuntimeError(
                "The additional training collection is incomplete; "
                f"{len(missing_ids)} selected IDs have no saved trace: "
                + ", ".join(sorted(missing_ids))
            )
        print(
            f"Collected {len(records)} additional records; "
            f"{len(saved_by_id)} of {len(requested_ids)} are saved."
        )
        return

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

    cf_companion = (
        train_path.parent
        / (train_path.stem + "_counterfactual.jsonl")
    )
    if (
        cf_companion.exists()
        and cf_companion.resolve() != train_path.resolve()
    ):
        print(f"Merging counterfactual training trace: {cf_companion.name}")
        train_records.extend(load_records(cf_companion))

    if EXTRA_TRAIN_TRACE_PATH.exists():
        extra_records = load_records(
            EXTRA_TRAIN_TRACE_PATH
        )
        print(
            f"Merging additional counterfactual training data: "
            f"{len(extra_records)} records from "
            f"{EXTRA_TRAIN_TRACE_PATH}"
        )
        train_records.extend(extra_records)

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


def audit_command(
    train_path: Path,
    validation_path: Path,
) -> None:
    train_records = load_records(train_path)
    cf_companion = (
        train_path.parent
        / (train_path.stem + "_counterfactual.jsonl")
    )
    if (
        cf_companion.exists()
        and cf_companion.resolve() != train_path.resolve()
    ):
        train_records.extend(load_records(cf_companion))
    if EXTRA_TRAIN_TRACE_PATH.exists():
        train_records.extend(load_records(EXTRA_TRAIN_TRACE_PATH))

    validation_records = load_records(validation_path)
    results = audit_features(
        train_records,
        validation_records,
    )
    print(json.dumps(results, indent=2))


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
        "--additional-training-count",
        type=int,
        default=None,
        help=(
            "Select and collect this many new, unique train-split "
            "questions with non-empty gold answers. Requires "
            "--split train --counterfactual."
        ),
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

    audit_parser = subparsers.add_parser(
        "audit",
        help="Measure validation-set feature ablations for each router.",
    )
    audit_parser.add_argument(
        "--train",
        type=Path,
        default=TRACES_ROOT / "phase3_tatqa_train.jsonl",
    )
    audit_parser.add_argument(
        "--validation",
        type=Path,
        default=(
            TRACES_ROOT
            / "phase3_tatqa_validation_counterfactual.jsonl"
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
            additional_training_count=args.additional_training_count,
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

    elif args.command == "audit":
        audit_command(
            train_path=args.train,
            validation_path=args.validation,
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