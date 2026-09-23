from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


REQUIRED_KEYS = {
    "id",
    "prompt",
    "check",
    "entry_point",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "benchmarks/code_100.json"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "traces/benchmark_audit.json"
        ),
    )

    return parser.parse_args()


def load_problems(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Benchmark not found: {path}"
        )

    data = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if isinstance(data, dict):
        data = data.get(
            "problems",
            [],
        )

    if not isinstance(
        data,
        list,
    ):
        raise ValueError(
            "Benchmark must contain a JSON list."
        )

    return [
        problem
        for problem in data
        if isinstance(
            problem,
            dict,
        )
    ]


def audit_problem(
    problem: dict[str, Any],
) -> dict[str, Any]:
    problem_id = problem.get(
        "id",
        "<missing>",
    )

    missing = sorted(
        REQUIRED_KEYS
        - set(problem.keys())
    )

    errors = []

    if missing:
        errors.append(
            "Missing keys: "
            + ", ".join(missing)
        )

    prompt = problem.get(
        "prompt"
    )

    if not isinstance(
        prompt,
        str,
    ) or not prompt.strip():
        errors.append(
            "Prompt is empty or not a string."
        )

    entry_point = problem.get(
        "entry_point"
    )

    if not isinstance(
        entry_point,
        str,
    ) or not entry_point.strip():
        errors.append(
            "Entry point is empty or not a string."
        )

    check = problem.get(
        "check"
    )

    if not isinstance(
        check,
        str,
    ) or not check.strip():
        errors.append(
            "Check is empty or not a string."
        )
    else:
        try:
            tree = ast.parse(
                check
            )

            check_functions = [
                node
                for node in ast.walk(tree)
                if isinstance(
                    node,
                    ast.FunctionDef,
                )
                and node.name == "check"
            ]

            if not check_functions:
                errors.append(
                    "Check does not define "
                    "a function named 'check'."
                )

        except SyntaxError as exc:
            errors.append(
                "Check has syntax error: "
                f"{exc}"
            )

    return {
        "id": problem_id,
        "valid": not errors,
        "errors": errors,
    }


def audit_benchmark(
    problems: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [
        audit_problem(problem)
        for problem in problems
    ]

    ids = [
        problem.get("id")
        for problem in problems
    ]

    duplicates = sorted(
        {
            problem_id
            for problem_id in ids
            if ids.count(problem_id) > 1
        }
    )

    invalid = [
        result
        for result in results
        if not result["valid"]
    ]

    return {
        "n_problems": len(problems),
        "expected_n_problems": 100,
        "count_correct": (
            len(problems) == 100
        ),
        "duplicate_ids": duplicates,
        "duplicate_id_count": len(
            duplicates
        ),
        "invalid_problem_count": len(
            invalid
        ),
        "valid_problem_count": (
            len(problems) - len(invalid)
        ),
        "problems": results,
    }


def main() -> int:
    args = parse_args()

    problems = load_problems(
        args.input
    )

    audit = audit_benchmark(
        problems
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        json.dumps(
            audit,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Problems: {audit['n_problems']}"
    )

    print(
        f"Count correct: "
        f"{audit['count_correct']}"
    )

    print(
        f"Duplicate IDs: "
        f"{audit['duplicate_id_count']}"
    )

    print(
        f"Invalid problems: "
        f"{audit['invalid_problem_count']}"
    )

    if audit["invalid_problem_count"]:
        print()
        print("Invalid problems:")

        for problem in audit["problems"]:
            if not problem["valid"]:
                print(
                    f"  {problem['id']}: "
                    f"{'; '.join(problem['errors'])}"
                )

    print()
    print(
        f"Audit written to {args.output}"
    )

    if (
        not audit["count_correct"]
        or audit["duplicate_id_count"] > 0
        or audit["invalid_problem_count"] > 0
    ):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )