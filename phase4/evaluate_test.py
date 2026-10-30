"""
Phase IV — Final Held-Out Test Evaluation
==========================================
Evaluates the FROZEN Logistic Regression router on the 43 held-out test cases.
The router thresholds are loaded from disk and are NOT modified here.

Usage:
    python3 phase4/evaluate_test.py

Output:
    results/phase4/test_evaluation/test_results.json
    results/phase4/test_evaluation/test_summary.json
    traces/phase4/test_evaluation/test_traces.jsonl
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import ollama

from phase4.accounting import CascadeResourceTracker, ModelCallRecord
from phase4.config import (
    BENCHMARK_PATH,
    DEFAULT_JUDGE_MODEL,
    OLLAMA_HOST,
    PHASE4_MODEL_CONFIG,
    PROJECT_ROOT as CFG_ROOT,
    RESULTS_DIR,
    TRACES_DIR,
)
from phase4.gate import ConservativeEscalationGate
from phase4.judge import call_llm_judge, call_runtime_judge, combine_verifications
from phase4.router import Phase4CascadeRouter
from phase4.runner import call_tier_model_detailed
from phase4.signals import extract_signals, signal_vector
from phase4.verifier import verify_deterministic, verify_runtime

# ── Constants ──────────────────────────────────────────────────────────────────
FROZEN_ROUTER_DIR = (
    PROJECT_ROOT
    / "results"
    / "phase4"
    / "runs"
    / "router_training_20261029_154818"
    / "logistic_regression"
)
TIER_ORDER: List[str] = ["layer_1", "layer_2", "layer_3", "layer_4"]
OUT_DIR = RESULTS_DIR / "test_evaluation"
TRACE_DIR = TRACES_DIR / "test_evaluation"

# ── Helpers ───────────────────────────────────────────────────────────────────

def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def load_test_cases() -> List[Dict[str, Any]]:
    with open(BENCHMARK_PATH, encoding="utf-8") as f:
        all_cases = json.load(f)
    test_cases = [c for c in all_cases if c.get("split") == "test"]
    assert len(test_cases) == 43, f"Expected 43 test cases, got {len(test_cases)}"
    return test_cases


def evaluate_one_case(
    case: Dict[str, Any],
    router: Phase4CascadeRouter,
    client: Any,
    gate: ConservativeEscalationGate,
    historical_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    qid = case["query_id"]
    query = case["query"]
    evidence = case["evidence"]
    ground_truth = case["ground_truth"]

    tracker = CascadeResourceTracker()
    tier_evals: Dict[str, Any] = {}
    prev_resp: Optional[str] = None
    prev_feedback: Optional[str] = None

    # Cascade: run all 4 tiers for full reproducibility;
    # record router stop decision separately so test labels aren't leaked to routing.
    for tier in TIER_ORDER:
        model_name = PHASE4_MODEL_CONFIG[tier]["model"]

        hist = historical_cache.get((qid, tier))
        if hist:
            ans_text = hist["raw_response"]
            gen_stats = {
                "prompt_tokens": hist.get("prompt_tokens"),
                "completion_tokens": hist.get("completion_tokens"),
                "total_tokens": hist.get("total_tokens"),
                "wall_latency_ms": hist.get("wall_latency_ms", 0.0),
                "engine_total_duration_ns": None,
                "error": None,
                "execution_mode": "genuine_ollama_call_cached",
            }
        else:
            ans_text, gen_stats, is_mock = call_tier_model_detailed(
                problem={"query": query, "evidence": evidence},
                layer_key=tier,
                previous_response=prev_resp,
                feedback=prev_feedback,
                client=client,
                force_mock=False,
            )
            if is_mock:
                raise RuntimeError(
                    f"FATAL: Tier model {model_name} fell back to mock for {qid}. "
                    "Aborting — test evaluation must use genuine model calls only."
                )

        tracker.record_call(ModelCallRecord(
            model=model_name,
            role="tier_generator",
            tier=tier,
            prompt_tokens=gen_stats.get("prompt_tokens"),
            completion_tokens=gen_stats.get("completion_tokens"),
            total_tokens=gen_stats.get("total_tokens"),
            wall_latency_ms=gen_stats.get("wall_latency_ms", 0.0),
            engine_total_duration_ns=gen_stats.get("engine_total_duration_ns"),
            execution_mode=gen_stats.get("execution_mode", "genuine_ollama_call"),
        ))

        # Runtime evaluation (no ground truth)
        runtime_ver = verify_runtime(answer_text=ans_text, evidence=evidence, query=query)
        runtime_judge = call_runtime_judge(
            query=query,
            answer=ans_text,
            evidence=evidence,
            judge_model=DEFAULT_JUDGE_MODEL,
            client=client,
            allow_mock_fallback=False,
            runtime_verifier_result=runtime_ver,
        )
        runtime_comb = combine_verifications(runtime_ver, runtime_judge)

        tracker.record_call(ModelCallRecord(
            model=DEFAULT_JUDGE_MODEL,
            role="runtime_judge",
            tier=tier,
            prompt_tokens=runtime_judge.get("prompt_tokens"),
            completion_tokens=runtime_judge.get("completion_tokens"),
            total_tokens=runtime_judge.get("total_tokens"),
            wall_latency_ms=runtime_judge.get("latency_ms", 0.0),
            execution_mode="genuine_ollama_call",
        ))

        # Offline reference evaluation (uses gold ground truth — for measurement only)
        offline_det = verify_deterministic(
            answer_text=ans_text,
            status_reported="DISCREPANCY" if "discrepan" in ans_text.lower() else "MATCHED",
            ground_truth=ground_truth,
            evidence=evidence,
        )
        offline_judge = call_llm_judge(
            query=query,
            answer=ans_text,
            evidence=evidence,
            ground_truth=ground_truth,
            judge_model=DEFAULT_JUDGE_MODEL,
            client=client,
            allow_mock_fallback=False,
            deterministic_result=offline_det,
        )
        offline_comb = combine_verifications(offline_det, offline_judge)

        tracker.record_call(ModelCallRecord(
            model=DEFAULT_JUDGE_MODEL,
            role="offline_judge",
            tier=tier,
            prompt_tokens=offline_judge.get("prompt_tokens"),
            completion_tokens=offline_judge.get("completion_tokens"),
            total_tokens=offline_judge.get("total_tokens"),
            wall_latency_ms=offline_judge.get("latency_ms", 0.0),
            execution_mode="genuine_ollama_call",
        ))

        signals = extract_signals(
            response=ans_text,
            problem={"query": query, "evidence": evidence},
            previous_response=prev_resp,
        )
        feat_vec = signal_vector(signals)

        tier_evals[tier] = {
            "model": model_name,
            "response": ans_text,
            "generator_tokens": gen_stats.get("total_tokens"),
            "generator_latency_ms": gen_stats.get("wall_latency_ms"),
            "execution_mode": gen_stats.get("execution_mode", "genuine_ollama_call"),
            "runtime_features": signals,
            "runtime_feature_vector": feat_vec,
            "runtime_verifier_pass": runtime_ver["runtime_pass"],
            "runtime_verifier_reasons": runtime_ver.get("reasons", []),
            "runtime_judge_verdict": runtime_judge.get("overall_verdict"),
            "runtime_overall_pass": runtime_comb["overall_pass"],
            "offline_deterministic_pass": offline_det["deterministic_pass"],
            "offline_deterministic_reasons": offline_det.get("reasons", []),
            "offline_judge_verdict": offline_judge.get("overall_verdict"),
            "offline_overall_pass": bool(offline_comb["overall_pass"]),
            "offline_disagreement": offline_comb.get("disagreement", False),
        }

        prev_resp = ans_text
        prev_feedback = "; ".join(runtime_ver.get("reasons", [])) or runtime_judge.get("reasoning", "")

    tracker.close()
    resource_summary = tracker.get_summary()

    # ── Routing decision: use FROZEN router (runtime only, no ground truth) ───
    router_stop_tier = "layer_4"
    router_stop_prob: Optional[float] = None
    for tier in TIER_ORDER[:-1]:
        te = tier_evals[tier]
        sigs = te["runtime_features"]
        runtime_ver_res = {
            "runtime_pass": te["runtime_verifier_pass"],
            "reasons": te["runtime_verifier_reasons"],
        }
        should_stop, prob = router.should_stop(
            layer_key=tier,
            signals=sigs,
            runtime_verifier_result=runtime_ver_res,
        )
        if should_stop:
            router_stop_tier = tier
            router_stop_prob = prob
            break

    # ── Cascade answer is the response at the router's chosen tier ────────────
    chosen = tier_evals[router_stop_tier]
    is_correct = chosen["offline_overall_pass"]

    return {
        "query_id": qid,
        "split": "test",
        "scenario_type": case.get("scenario_type", "unknown"),
        "query": query,
        "evidence": evidence,
        "ground_truth_reference": ground_truth,
        "router_stop_tier": router_stop_tier,
        "router_stop_probability": router_stop_prob,
        "cascade_answer": chosen["response"],
        "cascade_correct": is_correct,
        "tier_evaluations": tier_evals,
        "resource_summary": resource_summary,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    trace_path = TRACE_DIR / "test_traces.jsonl"
    if trace_path.exists():
        print(f"WARNING: Test traces already exist at {trace_path}. They will be appended to.")

    # ── Load frozen router — thresholds fixed from validation calibration ─────
    router = Phase4CascadeRouter(routers_dir=FROZEN_ROUTER_DIR)
    print(f"Frozen router loaded from: {FROZEN_ROUTER_DIR}")
    print(f"Thresholds: {router.thresholds}")
    assert abs(router.thresholds.get("layer_1", 0) - 0.68) < 0.01, \
        f"Unexpected L1 threshold {router.thresholds.get('layer_1')} — router may not be frozen correctly."

    gate = ConservativeEscalationGate()

    # ── Load historical cache (from pilot runs — no test data in there) ───────
    historical_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
    hist_paths = [
        PROJECT_ROOT / "traces" / "phase4" / "runs" / "pilot_20261020_live_eval" / "genuine_calls.jsonl",
    ]
    for hp in hist_paths:
        if hp.exists():
            with open(hp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        qid_h = rec.get("query_id")
                        tier_h = rec.get("tier")
                        if (
                            qid_h and tier_h
                            and rec.get("execution_mode") == "genuine_ollama_call"
                            and rec.get("raw_response")
                        ):
                            historical_cache[(qid_h, tier_h)] = rec
                    except json.JSONDecodeError:
                        pass
    print(f"Historical cache entries: {len(historical_cache)}")

    # Safety: confirm no test IDs are in the cache (they shouldn't be — pilot used train IDs)
    test_cases = load_test_cases()
    test_qids = {c["query_id"] for c in test_cases}
    cache_test_overlap = {k[0] for k in historical_cache if k[0] in test_qids}
    if cache_test_overlap:
        raise RuntimeError(
            f"FATAL: Historical cache contains test-split IDs: {cache_test_overlap}. "
            "This would constitute data leakage. Aborting."
        )
    print(f"Cache ∩ test split = ∅ ✓ (no test data in historical cache)")

    from phase4.runner import call_tier_model_detailed  # noqa: already imported above
    client = ollama.Client(host=OLLAMA_HOST)

    total = len(test_cases)
    results: List[Dict[str, Any]] = []
    start_time = time.perf_counter()
    case_durations: List[float] = []

    print(f"\n{'='*60}")
    print(f"Phase IV — Final Held-Out Test Evaluation")
    print(f"Router:     Logistic Regression (FROZEN — no threshold changes)")
    print(f"Test cases: {total}")
    print(f"{'='*60}\n")

    for idx, case in enumerate(test_cases, 1):
        qid = case["query_id"]
        scenario = case.get("scenario_type", "unknown")

        elapsed = time.perf_counter() - start_time
        if case_durations:
            eta = (sum(case_durations) / len(case_durations)) * (total - idx + 1)
            eta_str = format_duration(eta)
        else:
            eta_str = "Estimating..."

        print(
            f"[TEST] Case {idx}/{total} ({100*idx/total:.1f}%) | "
            f"{qid} ({scenario}) | Elapsed: {format_duration(elapsed)} | ETA: {eta_str}",
            flush=True,
        )

        case_start = time.perf_counter()
        result = evaluate_one_case(case, router, client, gate, historical_cache)
        case_dur = time.perf_counter() - case_start
        case_durations.append(case_dur)

        stop_tag = "✓" if result["cascade_correct"] else "✗"
        print(
            f"  {stop_tag} stopped at {result['router_stop_tier']} | "
            f"correct={result['cascade_correct']} | {case_dur:.1f}s",
            flush=True,
        )

        results.append(result)

        # Incremental save
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, default=str) + "\n")

    total_elapsed = time.perf_counter() - start_time

    # ── Compute metrics ────────────────────────────────────────────────────────
    n = len(results)
    correct = sum(1 for r in results if r["cascade_correct"])
    accuracy = correct / n

    stop_dist: Dict[str, int] = {t: 0 for t in TIER_ORDER}
    false_stops = 0
    total_stops_before_l4 = 0
    per_scenario: Dict[str, Dict[str, int]] = {}
    avg_tokens_list: List[float] = []

    for r in results:
        stop_dist[r["router_stop_tier"]] += 1
        if r["router_stop_tier"] != "layer_4":
            total_stops_before_l4 += 1
            if not r["cascade_correct"]:
                false_stops += 1
        sc = r["scenario_type"]
        if sc not in per_scenario:
            per_scenario[sc] = {"total": 0, "correct": 0}
        per_scenario[sc]["total"] += 1
        per_scenario[sc]["correct"] += int(r["cascade_correct"])
        rs = r.get("resource_summary", {})
        tokens = rs.get("prompt_tokens", 0) + rs.get("completion_tokens", 0)
        avg_tokens_list.append(tokens)

    fsr = false_stops / total_stops_before_l4 if total_stops_before_l4 > 0 else 0.0

    summary = {
        "evaluation_type": "held_out_test",
        "router": "Phase4CascadeRouter (Logistic Regression)",
        "frozen_router_dir": str(FROZEN_ROUTER_DIR),
        "frozen_thresholds": router.thresholds,
        "total_cases": n,
        "correct_cases": correct,
        "accuracy_pct": round(accuracy * 100, 2),
        "false_stop_rate_pct": round(fsr * 100, 2),
        "false_stops": false_stops,
        "total_stops_before_l4": total_stops_before_l4,
        "stop_distribution": stop_dist,
        "avg_tokens_per_case": round(float(np.mean(avg_tokens_list)), 1) if avg_tokens_list else 0.0,
        "total_wall_time_s": round(total_elapsed, 2),
        "per_scenario_accuracy": {
            sc: {
                "total": v["total"],
                "correct": v["correct"],
                "accuracy_pct": round(100 * v["correct"] / v["total"], 1),
            }
            for sc, v in per_scenario.items()
        },
    }

    results_path = OUT_DIR / "test_results.json"
    summary_path = OUT_DIR / "test_summary.json"
    results_path.write_text(json.dumps(results, indent=2, default=str))
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\n{'='*60}")
    print(f"FINAL TEST RESULTS  (n={n})")
    print(f"{'='*60}")
    print(f"Accuracy:         {accuracy*100:.1f}%  ({correct}/{n} correct)")
    print(f"False Stop Rate:  {fsr*100:.1f}%  ({false_stops}/{total_stops_before_l4})")
    print(f"Stop distribution: {stop_dist}")
    print(f"Avg tokens/case:  {np.mean(avg_tokens_list):.0f}")
    print(f"Wall time:        {format_duration(total_elapsed)}")
    print(f"\nPer-scenario accuracy:")
    for sc, v in sorted(per_scenario.items()):
        print(f"  {sc:35s} {v['correct']}/{v['total']}  ({100*v['correct']/v['total']:.0f}%)")
    print(f"\nResults saved to: {results_path}")
    print(f"Summary saved to: {summary_path}")
    print(f"{'='*60}\n")


def _unused_approx_placeholder() -> bool:
    """Removed — threshold assertion now uses direct float comparison."""
    return True


if __name__ == "__main__":
    main()
