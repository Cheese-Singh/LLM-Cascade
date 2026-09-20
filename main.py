from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_PROBLEMS_FILE,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    EXPERIMENT_MODES,
    MODEL_CONFIG,
)
from evaluation import (
    build_experiment,
    format_summary,
    save_experiment_results,
    summarize_experiment,
)
from run_baselines import run_baseline_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LLM-Cascade Phase I experiments."
    )

    parser.add_argument(
        "--problems-file",
        type=Path,
        default=DEFAULT_PROBLEMS_FILE,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--mode",
        choices=EXPERIMENT_MODES,
        default="all",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results"),
    )

    parser.add_argument(
        "--run-id",
        type=str,
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
        "--custom-prompt",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--custom-check",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--custom-entry-point",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
    )

    parser.add_argument(
        "--problem-ids",
        type=str,
        default=None,
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be greater than 0.")

    if args.temperature < 0:
        raise ValueError("--temperature must be non-negative.")

    custom_values = (
        args.custom_prompt,
        args.custom_check,
        args.custom_entry_point,
    )

    custom_count = sum(value is not None for value in custom_values)

    if custom_count not in (0, 3):
        raise ValueError(
            "--custom-prompt, --custom-check, and "
            "--custom-entry-point must be provided together."
        )

    if custom_count == 0:
        if not args.problems_file.exists():
            raise FileNotFoundError(
                f"Problems file not found: {args.problems_file}"
            )

        if not args.problems_file.is_file():
            raise ValueError(
                f"Problems path is not a file: {args.problems_file}"
            )


def load_problems(
    problems_file: Path,
    limit: int | None = None,
    custom_prompt: str | None = None,
    custom_check: str | None = None,
    custom_entry_point: str | None = None,
    problem_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    if (
        custom_prompt is not None
        and custom_check is not None
        and custom_entry_point is not None
    ):
        problems = [
            {
                "id": "custom_problem",
                "name": "custom_problem",
                "prompt": custom_prompt,
                "check": custom_check,
                "entry_point": custom_entry_point,
            }
        ]
    else:
        with problems_file.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("Problems file must contain a JSON list.")

        problems = data

    if limit is not None:
        problems = problems[:limit]

    if not problems:
        raise ValueError("No problems loaded.")

    if problem_ids:
        wanted = set(problem_ids)
        problems = [problem for problem in problems if problem["id"] in wanted]
        missing = wanted - {problem["id"] for problem in problems}

        if missing:
            raise ValueError(f"Unknown problem ids: {sorted(missing)}")

    return problems


def print_experiment_header(
    problems: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    print()
    print("=" * 80)
    print("LLM-CASCADE — PHASE I EXPERIMENT")
    print("=" * 80)
    print(f"Problems       : {len(problems)}")
    print(f"Problem source : {args.problems_file}")
    print(f"Mode           : {args.mode}")

    for index in range(1, 5):
        layer = MODEL_CONFIG[f"layer_{index}"]
        print(f"Layer {index}        : {layer['model']}")

    print(f"Temperature    : {args.temperature}")
    print(f"Seed           : {args.seed}")
    print(f"Output         : {args.output_dir}")
    print("=" * 80)
    print()


def print_loaded_problems(
    problems: list[dict[str, Any]],
) -> None:
    print(f"Problems loaded: {len(problems)}")

    preview = problems[:5]

    for index, problem in enumerate(preview, start=1):
        print(
            f"  {index}. "
            f"{problem.get('id', 'unknown')} "
            f"({problem.get('entry_point', 'unknown')})"
        )

    remaining = len(problems) - len(preview)

    if remaining > 0:
        print(f"  ... and {remaining} more")

    print()


def main() -> int:
    args = parse_args()

    try:
        validate_args(args)

        problems = load_problems(
            problems_file=args.problems_file,
            limit=args.limit,
            custom_prompt=args.custom_prompt,
            custom_check=args.custom_check,
            custom_entry_point=args.custom_entry_point,
            problem_ids=(
                [item.strip() for item in args.problem_ids.split(",") if item.strip()]
                if args.problem_ids
                else None
            ),
        )

        print_experiment_header(
            problems=problems,
            args=args,
        )

        print_loaded_problems(problems)

        results = run_baseline_suite(
            problems=problems,
            mode=args.mode,
            temperature=args.temperature,
            seed=args.seed,
            verbose=not args.quiet,
            stream_dir=args.output_dir,
            resume=args.resume,
        )

        experiment = build_experiment(
            mode=args.mode,
            results=results,
            metadata={
                "problems_file": str(args.problems_file),
                "temperature": args.temperature,
                "seed": args.seed,
                "n_problems": len(problems),
            },
        )

        summary = summarize_experiment(experiment)

        print()
        print("=" * 80)
        print("EXPERIMENT SUMMARY")
        print("=" * 80)
        print(format_summary(summary))

        output_paths = save_experiment_results(
            experiment=experiment,
            summary=summary,
            output_dir=args.output_dir,
            run_id=args.run_id,
        )

        print()
        print("Saved outputs:")

        for path in output_paths:
            print(f"  {path}")

        print()
        print("Experiment completed successfully.")

        return 0

    except KeyboardInterrupt:
        print(
            "\nExperiment interrupted. Partial records are in the "
            "stream_*.jsonl files in the output directory.",
            file=sys.stderr,
        )
        return 130

    except Exception as exc:
        print(
            f"\nExperiment failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())