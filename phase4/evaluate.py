from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np

from phase4.config import PHASE4_MODEL_CONFIG, RESULTS_DIR, TRACES_DIR
from phase4.router import Phase4CascadeRouter
from phase4.runner import call_tier_model
from phase4.signals import extract_signals
from phase4.verifier import verify_deterministic
from phase4.judge import call_llm_judge, combine_verifications


def run_full_evaluation(
    benchmark_cases: List[Dict[str, Any]],
    router: Optional[Phase4CascadeRouter] = None,
    client: Optional[Any] = None,
    force_mock: bool = True,
    split_filter: Optional[str] = "test",
) -> Dict[str, Any]:
    """
    Evaluate all 5 policies across benchmark cases:
    - Always-L1
    - Always-L4
    - Fixed Cascade
    - Learned Cascade
    - Offline Oracle Baseline
    """
    if split_filter:
        cases = [c for c in benchmark_cases if c.get("split") == split_filter]
    else:
        cases = benchmark_cases

    if router is None:
        router = Phase4CascadeRouter()

    records = []
    total_cases = len(cases)
    start_time = time.perf_counter()

    for idx, prob in enumerate(cases, 1):
        case_id = prob["query_id"]
        ev = prob["evidence"]
        gt = prob["ground_truth"]

        layer_outputs = {}
        prev_resp = None
        prev_feedback = None

        # Execute 4 tiers contextually
        for l_idx, l_key in enumerate(["layer_1", "layer_2", "layer_3", "layer_4"]):
            elapsed = time.perf_counter() - start_time
            rate = ((idx - 1) * 4 + l_idx) / max(elapsed, 1e-9)
            remaining_steps = (total_cases * 4) - ((idx - 1) * 4 + l_idx)
            eta = remaining_steps / rate if rate > 0 else 0.0

            time_str = f"{elapsed / 60:.1f}m" if elapsed >= 60 else f"{elapsed:.1f}s"
            eta_str = f"{eta / 60:.1f}m" if eta >= 60 else f"{eta:.1f}s"
            pct = 100.0 * ((idx - 1) * 4 + l_idx) / max(total_cases * 4, 1)

            print(
                f"[Stage: Evaluate ({split_filter or 'all'})] Case {idx}/{total_cases} ({pct:.1f}%) | "
                f"Question: {case_id} | Layer: {l_key} | elapsed={time_str} | ETA={eta_str}",
                flush=True
            )

            ans, tokens, latency, is_mock = call_tier_model(
                problem=prob,
                layer_key=l_key,
                previous_response=prev_resp,
                feedback=prev_feedback,
                client=client,
                force_mock=force_mock,
            )

            # Verification
            det_res = verify_deterministic(
                answer_text=ans,
                status_reported="DISCREPANCY" if "discrepancy" in ans.lower() else "MATCHED",
                ground_truth=gt,
                evidence=ev,
            )

            judge_res = call_llm_judge(
                query=prob["query"],
                answer=ans,
                evidence=ev,
                ground_truth=gt,
                client=client,
                allow_mock_fallback=True,
                deterministic_result=det_res,
            )

            combined = combine_verifications(det_res, judge_res)
            signals = extract_signals(ans, prob, previous_response=prev_resp)

            layer_outputs[l_key] = {
                "response": ans,
                "tokens": tokens,
                "latency_ms": latency,
                "is_mock": is_mock,
                "passed": combined["overall_pass"],
                "deterministic_pass": combined["deterministic_pass"],
                "judge_pass": combined["judge_pass"],
                "disagreement": combined["disagreement"],
                "disagreement_type": combined["disagreement_type"],
                "signals": signals,
            }

            prev_resp = ans
            prev_feedback = "; ".join(det_res.get("reasons", [])) if not combined["overall_pass"] else None

        records.append({
            "query_id": case_id,
            "split": prob.get("split"),
            "scenario_type": prob.get("scenario_type"),
            "layers": layer_outputs,
        })

    # Save evaluation traces
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = TRACES_DIR / f"phase4_evaluation_{split_filter or 'all'}.jsonl"
    with trace_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    # Compute comparative policy metrics
    metrics = compute_policy_comparisons(records, router)
    metrics["num_cases"] = len(records)
    metrics["split"] = split_filter

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    res_path = RESULTS_DIR / f"phase4_metrics_{split_filter or 'all'}.json"
    res_path.write_text(json.dumps(metrics, indent=2))

    return metrics


def compute_policy_comparisons(
    records: List[Dict[str, Any]],
    router: Phase4CascadeRouter,
) -> Dict[str, Any]:
    """Compute performance, tokens, latency, cost, and routing error rates."""
    policies = {
        "Always-L1": {"correct": 0, "tokens": [], "latencies": [], "costs": []},
        "Always-L4": {"correct": 0, "tokens": [], "latencies": [], "costs": []},
        "Fixed-Cascade": {"correct": 0, "tokens": [], "latencies": [], "costs": [], "stops": []},
        "Learned-Cascade": {"correct": 0, "tokens": [], "latencies": [], "costs": [], "stops": [], "false_stops": 0, "unnecessary_escalations": 0},
        "Oracle-Cascade": {"correct": 0, "tokens": [], "latencies": [], "costs": [], "stops": []},
    }

    layer_order = ["layer_1", "layer_2", "layer_3", "layer_4"]

    for r in records:
        layers = r["layers"]

        # 1. Always-L1
        l1 = layers["layer_1"]
        policies["Always-L1"]["correct"] += int(l1["passed"])
        policies["Always-L1"]["tokens"].append(l1["tokens"])
        policies["Always-L1"]["latencies"].append(l1["latency_ms"])
        policies["Always-L1"]["costs"].append(l1["tokens"] * (PHASE4_MODEL_CONFIG["layer_1"]["cost_per_1k_tokens"] / 1000.0))

        # 2. Always-L4 (cumulative tokens of L4 execution)
        l4 = layers["layer_4"]
        policies["Always-L4"]["correct"] += int(l4["passed"])
        policies["Always-L4"]["tokens"].append(l4["tokens"])
        policies["Always-L4"]["latencies"].append(l4["latency_ms"])
        policies["Always-L4"]["costs"].append(l4["tokens"] * (PHASE4_MODEL_CONFIG["layer_4"]["cost_per_1k_tokens"] / 1000.0))

        # 3. Oracle minimum sufficient layer L*(q)
        oracle_tier = None
        for lk in layer_order:
            if layers[lk]["passed"]:
                oracle_tier = lk
                break
        if oracle_tier is None:
            oracle_tier = "layer_4"

        # Oracle metrics (up to oracle_tier)
        oracle_tok = sum(layers[lk]["tokens"] for lk in layer_order[:layer_order.index(oracle_tier) + 1])
        oracle_lat = sum(layers[lk]["latency_ms"] for lk in layer_order[:layer_order.index(oracle_tier) + 1])
        oracle_cost = sum(layers[lk]["tokens"] * (PHASE4_MODEL_CONFIG[lk]["cost_per_1k_tokens"] / 1000.0) for lk in layer_order[:layer_order.index(oracle_tier) + 1])
        policies["Oracle-Cascade"]["correct"] += int(layers[oracle_tier]["passed"])
        policies["Oracle-Cascade"]["tokens"].append(oracle_tok)
        policies["Oracle-Cascade"]["latencies"].append(oracle_lat)
        policies["Oracle-Cascade"]["costs"].append(oracle_cost)
        policies["Oracle-Cascade"]["stops"].append(oracle_tier)

        # 4. Fixed Cascade (e.g. stops if confidence >= 0.90)
        fixed_stop = "layer_4"
        for lk in layer_order[:-1]:
            conf = layers[lk]["signals"].get("stated_confidence", 0.5)
            if conf >= 0.90:
                fixed_stop = lk
                break
        fixed_tok = sum(layers[lk]["tokens"] for lk in layer_order[:layer_order.index(fixed_stop) + 1])
        fixed_lat = sum(layers[lk]["latency_ms"] for lk in layer_order[:layer_order.index(fixed_stop) + 1])
        fixed_cost = sum(layers[lk]["tokens"] * (PHASE4_MODEL_CONFIG[lk]["cost_per_1k_tokens"] / 1000.0) for lk in layer_order[:layer_order.index(fixed_stop) + 1])
        policies["Fixed-Cascade"]["correct"] += int(layers[fixed_stop]["passed"])
        policies["Fixed-Cascade"]["tokens"].append(fixed_tok)
        policies["Fixed-Cascade"]["latencies"].append(fixed_lat)
        policies["Fixed-Cascade"]["costs"].append(fixed_cost)
        policies["Fixed-Cascade"]["stops"].append(fixed_stop)

        # 5. Learned Cascade
        learned_stop = "layer_4"
        for lk in layer_order[:-1]:
            should_stop, prob = router.should_stop(lk, layers[lk]["signals"])
            if should_stop:
                learned_stop = lk
                break

        learned_tok = sum(layers[lk]["tokens"] for lk in layer_order[:layer_order.index(learned_stop) + 1])
        learned_lat = sum(layers[lk]["latency_ms"] for lk in layer_order[:layer_order.index(learned_stop) + 1])
        learned_cost = sum(layers[lk]["tokens"] * (PHASE4_MODEL_CONFIG[lk]["cost_per_1k_tokens"] / 1000.0) for lk in layer_order[:layer_order.index(learned_stop) + 1])
        learned_passed = layers[learned_stop]["passed"]

        policies["Learned-Cascade"]["correct"] += int(learned_passed)
        policies["Learned-Cascade"]["tokens"].append(learned_tok)
        policies["Learned-Cascade"]["latencies"].append(learned_lat)
        policies["Learned-Cascade"]["costs"].append(learned_cost)
        policies["Learned-Cascade"]["stops"].append(learned_stop)

        # False stop: stopped at learned_stop, but learned_stop was WRONG
        if not learned_passed and learned_stop != "layer_4":
            policies["Learned-Cascade"]["false_stops"] += 1

        # Unnecessary escalation: escalated past oracle tier when oracle tier was earlier
        if layer_order.index(learned_stop) > layer_order.index(oracle_tier) and layers[oracle_tier]["passed"]:
            policies["Learned-Cascade"]["unnecessary_escalations"] += 1

    N = max(len(records), 1)
    results = {}

    for name, p in policies.items():
        results[name] = {
            "accuracy": round((p["correct"] / N) * 100.0, 2),
            "mean_tokens": round(float(np.mean(p["tokens"])), 1),
            "median_tokens": round(float(np.median(p["tokens"])), 1),
            "mean_latency_ms": round(float(np.mean(p["latencies"])), 1),
            "median_latency_ms": round(float(np.median(p["latencies"])), 1),
            "p95_latency_ms": round(float(np.percentile(p["latencies"], 95)), 1),
            "mean_cost_per_query_usd": round(float(np.mean(p["costs"])), 6),
        }
        if "stops" in p:
            stops_count = {}
            for s in p["stops"]:
                stops_count[s] = stops_count.get(s, 0) + 1
            results[name]["stop_distribution"] = stops_count

    # Learned cascade diagnostic metrics
    fsr = policies["Learned-Cascade"]["false_stops"] / float(N)
    uer = policies["Learned-Cascade"]["unnecessary_escalations"] / float(N)
    results["Learned-Cascade"]["false_stop_rate"] = round(fsr, 4)
    results["Learned-Cascade"]["unnecessary_escalation_rate"] = round(uer, 4)

    # Token and latency savings relative to Always-L4
    l4_tokens = results["Always-L4"]["mean_tokens"]
    learned_tokens = results["Learned-Cascade"]["mean_tokens"]
    results["Learned-Cascade"]["token_savings_vs_L4"] = round(((l4_tokens - learned_tokens) / l4_tokens) * 100.0, 2)

    l4_lat = results["Always-L4"]["mean_latency_ms"]
    learned_lat = results["Learned-Cascade"]["mean_latency_ms"]
    results["Learned-Cascade"]["latency_savings_vs_L4"] = round(((l4_lat - learned_lat) / l4_lat) * 100.0, 2)

    return results
