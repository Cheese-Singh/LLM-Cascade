import json
import sys
from pathlib import Path

from evaluation import (
    build_experiment,
    format_summary,
    save_experiment_results,
    summarize_experiment,
)


def merge(stream_path: Path, system: str, output_dir: Path, run_id: str) -> None:
    latest = {}

    with stream_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            latest[row["problem_id"]] = row

    records = list(latest.values())

    print(f"{len(records)} unique problems")
    print(f"correct: {sum(bool(r['final_correct']) for r in records)}")

    experiment = build_experiment(
        mode=system,
        results={system: records},
        metadata={
            "source": str(stream_path),
            "merged": True,
            "n_problems": len(records),
        },
    )

    summary = summarize_experiment(experiment)

    print()
    print(format_summary(summary))

    paths = save_experiment_results(
        experiment=experiment,
        summary=summary,
        output_dir=output_dir,
        run_id=run_id,
    )

    print()
    print("Saved:")

    for path in paths:
        print(f"  {path}")


if __name__ == "__main__":
    stream = Path(sys.argv[1])
    system_name = sys.argv[2]
    out_dir = Path(sys.argv[3])
    rid = sys.argv[4]
    merge(stream, system_name, out_dir, rid)