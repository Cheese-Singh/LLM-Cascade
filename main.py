from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_DOMAIN,
    DEFAULT_PROBLEM_FILES,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    EXPERIMENT_MODES,
    SUPPORTED_DOMAINS,
)
from run_baselines import run_baseline_suite


def load_problems(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Problem file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        if "problems" in data:
            data = data["problems"]
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError("Problem file must contain a JSON list or an object containing 'problems'.")

    problems = []

    for index, problem in enumerate(data):
        if not isinstance(problem, dict):
            raise ValueError(f"Problem at index {index} is not a JSON object.")

        if "id" not in problem:
            raise ValueError(f"Problem at index {index} is missing 'id'.")

        problems.append(problem)

    return problems


def resolve_problem_file(
    domain: str,
    explicit_path: str | None,
) -> Path:
    if explicit_path:
        return Path(explicit_path)

    return DEFAULT_PROBLEM_FILES[domain]


def print_summary(
    results: dict[str, list[dict[str, Any]]],
) -> None:
    print()
    print("=" * 72)
    print("EXPERIMENT SUMMARY")
    print("=" * 72)

    for system, records in results.items():
        if not records:
            print(f"{system:<20} no records")
            continue

        correct = sum(
            1
            for record in records
            if record.get("final_correct") is True
        )

        total_tokens = sum(
            record.get("total_tokens", 0)
            for record in records
        )

        escalated = sum(
            1
            for record in records
            if record.get("escalated") is True
        )

        n = len(records)
        accuracy = correct / n * 100.0
        mean_tokens = total_tokens / n
        escalation_rate = escalated / n * 100.0

        print(
            f"{system:<20} "
            f"accuracy={accuracy:6.2f}% "
            f"mean_tokens={mean_tokens:8.2f} "
            f"escalation={escalation_rate:6.2f}%"
        )

    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM-Cascade experiment runner."
    )

    parser.add_argument(
        "--domain",
        choices=SUPPORTED_DOMAINS,
        default=DEFAULT_DOMAIN,
    )

    parser.add_argument(
        "--problems",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--mode",
        choices=EXPERIMENT_MODES,
        default="all",
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
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    args = parser.parse_args()

    problem_path = resolve_problem_file(
        args.domain,
        args.problems,
    )

    problems = load_problems(problem_path)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be greater than 0.")
        problems = problems[:args.limit]

    if not problems:
        raise ValueError("No problems found.")

    print()
    print("=" * 72)
    print("LLM-CASCADE")
    print("=" * 72)
    print(f"Domain      : {args.domain}")
    print(f"Problems    : {len(problems)}")
    print(f"Problem file: {problem_path}")
    print(f"Mode        : {args.mode}")
    print(f"Temperature : {args.temperature}")
    print(f"Seed        : {args.seed}")
    print("=" * 72)

    results = run_baseline_suite(
        problems=problems,
        mode=args.mode,
        temperature=args.temperature,
        seed=args.seed,
        verbose=args.verbose,
    )

    print_summary(results)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, ensure_ascii=False)

        print()
        print(f"Results written to: {output_path}")


if __name__ == "__main__":
    main()