from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from phase4.config import (
    DEFAULT_SEED,
    MAX_FALSE_STOP_RATE,
    PHASE4_MODEL_CONFIG,
    PROJECT_ROOT,
    RESULTS_DIR,
    ROUTERS_DIR,
    TRACES_DIR,
)
from phase4.router import (
    Phase4CascadeRouter,
    Phase4DecisionTreeRouter,
    Phase4HybridRouter,
    extract_layer_data,
)
from phase4.signals import signal_vector


def load_dataset_records(dataset_path: Path) -> List[Dict[str, Any]]:
    """Loads dataset records from either a .json array or a .jsonl trace file."""
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset path not found: {dataset_path}")

    if dataset_path.suffix == ".jsonl":
        records = []
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records
    else:
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "records" in data:
                return data["records"]
            else:
                return [data]


def evaluate_policy_on_records(
    policy_name: str,
    records: List[Dict[str, Any]],
    router_instance: Optional[Any] = None,
    rule_gate_instance: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Evaluates a specific cascade policy on multi-tier evaluation records.
    Computes accuracy, false stop rate, escalation distribution, and token usage.
    """
    layer_order = ["layer_1", "layer_2", "layer_3", "layer_4"]

    total_cases = len(records)
    if total_cases == 0:
        return {"error": "Empty records"}

    correct_count = 0
    false_stops = 0
    total_stops = 0
    layer_stop_counts = {l: 0 for l in layer_order}
    total_tokens_list: List[int] = []
    latencies_list: List[float] = []

    for r in records:
        tier_evals = r.get("tier_evaluations") or r.get("layers", {})
        resource_summary = r.get("resource_summary", {})

        selected_tier = "layer_4"

        if policy_name == "Always-L1":
            selected_tier = "layer_1"

        elif policy_name == "Always-L4":
            selected_tier = "layer_4"

        elif policy_name == "Oracle":
            min_s = r.get("minimum_sufficient_tier")
            if min_s and min_s in layer_order:
                selected_tier = min_s
            else:
                for lk in layer_order:
                    if tier_evals.get(lk, {}).get("offline_overall_pass") or tier_evals.get(lk, {}).get("passed"):
                        selected_tier = lk
                        break

        elif policy_name == "Conservative-Gate":
            # Pure rule-based gate without ML
            for lk in layer_order[:-1]:
                t_data = tier_evals.get(lk, {})
                ver_pass = t_data.get("runtime_verifier_pass")
                if ver_pass is None:
                    ver_pass = t_data.get("deterministic_pass", False)
                judge_verdict = t_data.get("runtime_judge_verdict") or ("PASS" if t_data.get("judge_pass") else "NO_PASS")

                if ver_pass and judge_verdict == "PASS":
                    selected_tier = lk
                    break

        elif router_instance is not None:
            # ML or Hybrid router
            for lk in layer_order[:-1]:
                t_data = tier_evals.get(lk, {})
                signals = t_data.get("runtime_features") or t_data.get("signals") or {}
                ver_res = {
                    "runtime_pass": t_data.get("runtime_verifier_pass", t_data.get("deterministic_pass", True)),
                    "reasons": t_data.get("runtime_verifier_reasons", []),
                }

                if hasattr(router_instance, "should_stop"):
                    should_stop, prob = router_instance.should_stop(
                        layer_key=lk,
                        signals=signals,
                        runtime_verifier_result=ver_res,
                    )
                else:
                    should_stop = False

                if should_stop:
                    selected_tier = lk
                    break

        # Compute metrics for selected tier
        layer_stop_counts[selected_tier] += 1
        t_chosen = tier_evals.get(selected_tier, {})
        is_correct = bool(t_chosen.get("offline_overall_pass") or t_chosen.get("passed", False))
        if is_correct:
            correct_count += 1

        # Check false stop: stopped at tier < layer_4, but answer failed offline validation
        if selected_tier != "layer_4":
            total_stops += 1
            if not is_correct:
                false_stops += 1

        # Cumulative tokens up to chosen tier
        cum_tokens = 0
        cum_latency = 0.0
        for lk in layer_order[: layer_order.index(selected_tier) + 1]:
            lk_eval = tier_evals.get(lk, {})
            cum_tokens += lk_eval.get("generator_tokens") or lk_eval.get("tokens", 0)
            cum_latency += lk_eval.get("generator_latency_ms") or lk_eval.get("latency_ms", 0.0)

        total_tokens_list.append(cum_tokens)
        latencies_list.append(cum_latency)

    accuracy = correct_count / float(total_cases)
    fsr = (false_stops / float(total_stops)) if total_stops > 0 else 0.0

    return {
        "policy": policy_name,
        "total_cases": total_cases,
        "correct_count": correct_count,
        "accuracy": round(accuracy * 100.0, 2),
        "false_stops": false_stops,
        "total_stops": total_stops,
        "false_stop_rate_pct": round(fsr * 100.0, 2),
        "layer_stop_distribution": layer_stop_counts,
        "avg_tokens_per_case": round(float(np.mean(total_tokens_list)), 1) if total_tokens_list else 0.0,
        "avg_latency_ms": round(float(np.mean(latencies_list)), 1) if latencies_list else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase IV Router Training & Benchmark Evaluation")
    parser.add_argument(
        "--train_data",
        type=Path,
        default=PROJECT_ROOT / "results" / "phase4" / "runs" / "genuine_train_full" / "genuine_train_dataset.json",
        help="Path to training dataset JSON or JSONL",
    )
    parser.add_argument(
        "--val_data",
        type=Path,
        default=PROJECT_ROOT / "results" / "phase4" / "runs" / "genuine_validation_full" / "genuine_validation_dataset.json",
        help="Path to validation dataset JSON or JSONL",
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help="Custom output directory for checkpoints and metrics",
    )
    args = parser.parse_args()

    # Fallback to pilot directories if full run files are still in progress
    train_path = args.train_data
    if not train_path.exists():
        # Check trace jsonl
        alt_trace = PROJECT_ROOT / "traces" / "phase4" / "runs" / "genuine_train_full" / "train_traces.jsonl"
        if alt_trace.exists():
            train_path = alt_trace
        else:
            train_path = PROJECT_ROOT / "results" / "phase4" / "runs" / "pilot_train_10" / "genuine_train_dataset.json"

    val_path = args.val_data
    if not val_path.exists():
        alt_trace = PROJECT_ROOT / "traces" / "phase4" / "runs" / "genuine_validation_full" / "validation_traces.jsonl"
        if alt_trace.exists():
            val_path = alt_trace
        else:
            val_path = PROJECT_ROOT / "results" / "phase4" / "runs" / "pilot_val_5" / "genuine_validation_dataset.json"

    print("=======================================================")
    print("Phase IV Router Training Pipeline")
    print(f"Train Source: {train_path}")
    print(f"Val Source:   {val_path}")
    print("=======================================================")

    train_records = load_dataset_records(train_path)
    val_records = load_dataset_records(val_path)

    print(f"Loaded {len(train_records)} training records.")
    print(f"Loaded {len(val_records)} validation records.")

    run_timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.output_dir or (RESULTS_DIR / "runs" / f"router_training_{run_timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Train Logistic Regression Router
    print("\n--- Training Model A: Logistic Regression Router ---")
    logreg_router = Phase4CascadeRouter(routers_dir=out_dir / "logistic_regression")
    logreg_summary = logreg_router.train_on_data(train_records, val_records)
    print(f"Logistic Regression Thresholds: {logreg_router.thresholds}")

    # 2. Train Decision Tree Router
    print("\n--- Training Model B: Shallow Decision Tree Router ---")
    tree_router = Phase4DecisionTreeRouter(routers_dir=out_dir / "decision_tree", max_depth=3)
    tree_summary = tree_router.train_on_data(train_records, val_records)
    print(f"Decision Tree Thresholds: {tree_router.thresholds}")

    # 3. Create Hybrid Router (Gated by runtime verifier)
    print("\n--- Initializing Model C: Hybrid Deterministic-ML Gate ---")
    hybrid_router = Phase4HybridRouter(ml_router=logreg_router)

    # 4. Benchmark All Policies on Validation Set
    print("\n--- Benchmarking All Policies on Validation Records ---")
    policies_to_eval = [
        ("Always-L1", None),
        ("Always-L4", None),
        ("Conservative-Gate", None),
        ("Logistic-Regression-Router", logreg_router),
        ("Decision-Tree-Router", tree_router),
        ("Hybrid-Gate-Router", hybrid_router),
        ("Oracle", None),
    ]

    benchmark_results: List[Dict[str, Any]] = []
    for pol_name, router_inst in policies_to_eval:
        res = evaluate_policy_on_records(
            policy_name=pol_name,
            records=val_records,
            router_instance=router_inst,
        )
        benchmark_results.append(res)
        print(
            f"[{pol_name:28s}] Accuracy: {res['accuracy']:5.1f}% | "
            f"FSR: {res['false_stop_rate_pct']:4.1f}% | "
            f"Avg Tokens: {res['avg_tokens_per_case']:6.1f} | "
            f"Stops: {res['layer_stop_distribution']}"
        )

    # Save benchmark metrics
    benchmark_payload = {
        "timestamp": run_timestamp,
        "train_records_count": len(train_records),
        "validation_records_count": len(val_records),
        "logistic_regression_summary": logreg_summary,
        "decision_tree_summary": tree_summary,
        "validation_benchmark": benchmark_results,
    }

    out_file = out_dir / "router_benchmark.json"
    out_file.write_text(json.dumps(benchmark_payload, indent=2))
    print(f"\nSaved complete benchmark report to: {out_file}")


if __name__ == "__main__":
    main()
