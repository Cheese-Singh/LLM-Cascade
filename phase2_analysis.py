from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from metrics import summarize_counterfactual_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "traces/counterfactual.jsonl"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "traces/phase2_analysis.json"
        ),
    )

    return parser.parse_args()


def load_jsonl(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Counterfactual file not found: {path}"
        )

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
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}: "
                    f"{exc}"
                ) from exc

            if not isinstance(
                record,
                dict,
            ):
                raise ValueError(
                    f"Line {line_number} is not a JSON object."
                )

            records.append(record)

    return records


def build_analysis(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = summarize_counterfactual_records(
        records
    )

    domains = {}

    for record in records:
        domain = record.get(
            "domain",
            "unknown",
        )

        domains.setdefault(
            domain,
            [],
        ).append(record)

    domain_metrics = {
        domain: summarize_counterfactual_records(
            domain_records
        )
        for domain, domain_records in domains.items()
    }

    return {
        "n_records": len(records),
        "overall": metrics,
        "by_domain": domain_metrics,
    }


def write_analysis(
    analysis: dict[str, Any],
    output: Path,
) -> None:
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            analysis,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def print_analysis(
    analysis: dict[str, Any],
) -> None:
    overall = analysis["overall"]

    print()
    print(
        f"Problems: {overall['n']}"
    )

    print()
    print("Independent layer accuracy:")

    for layer in range(1, 5):
        key = f"layer_{layer}"
        metrics = overall["layers"][key]

        print(
            f"  Layer {layer}: "
            f"{metrics['correct']}/"
            f"{metrics['n']} "
            f"({metrics['accuracy']:.2%}) "
            f"mean_tokens="
            f"{metrics['mean_tokens']:.2f}"
        )

    print()
    print(
        "Minimum sufficient layer:"
    )

    counts = overall[
        "minimum_sufficient_layer_counts"
    ]

    rates = overall[
        "minimum_sufficient_layer_rates"
    ]

    for layer in range(1, 5):
        key = str(layer)

        print(
            f"  Layer {layer}: "
            f"{counts[key]} "
            f"({rates[key]:.2%})"
        )

    print(
        f"  None: "
        f"{counts['none']} "
        f"({rates['none']:.2%})"
    )

    print()
    print(
        "Beneficial escalations: "
        f"{overall['beneficial_escalations']} "
        f"({overall['beneficial_escalation_rate']:.2%})"
    )

    print(
        "Oracle cascade accuracy: "
        f"{overall['oracle_cascade_accuracy']:.2%}"
    )

    print(
        "Oracle cascade mean tokens: "
        f"{overall['oracle_cascade_mean_tokens']:.2f}"
    )

    print(
        "Oracle cascade median tokens: "
        f"{overall['oracle_cascade_median_tokens']:.2f}"
    )

    print(
        "Oracle cascade mean latency: "
        f"{overall['oracle_cascade_mean_latency_ms']:.2f} ms"
    )

    print()

    for domain, metrics in analysis[
        "by_domain"
    ].items():
        print(
            f"{domain}: "
            f"{metrics['n']} problems, "
            f"oracle accuracy="
            f"{metrics['oracle_cascade_accuracy']:.2%}"
        )


def main() -> int:
    args = parse_args()

    records = load_jsonl(
        args.input
    )

    if not records:
        raise ValueError(
            "Counterfactual dataset is empty."
        )

    analysis = build_analysis(
        records
    )

    write_analysis(
        analysis,
        args.output,
    )

    print_analysis(
        analysis
    )

    print(
        f"\nAnalysis written to {args.output}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )