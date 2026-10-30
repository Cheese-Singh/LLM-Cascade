from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama

from phase4.accounting import CascadeResourceTracker, ModelCallRecord
from phase4.config import DEFAULT_JUDGE_MODEL, OLLAMA_HOST, PHASE4_MODEL_CONFIG
from phase4.gate import ConservativeEscalationGate
from phase4.judge import call_runtime_judge, combine_verifications
from phase4.runner import call_tier_model_detailed
from phase4.verifier import verify_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_live_smoke_test(
    cases_to_run: Optional[List[str]] = None,
    output_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Executes a genuine live smoke test with strictly leak-free runtime checks.
    Every call is a real Ollama invocation (zero mock fallbacks permitted).
    """
    if cases_to_run is None:
        cases_to_run = ["AUDIT_POC_004", "AUDIT_POC_009", "AUDIT_POC_008"]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"smoke_test_{timestamp}"

    trace_dir = (output_base_dir or PROJECT_ROOT) / "traces" / "phase4" / "runs" / run_id
    results_dir = (output_base_dir or PROJECT_ROOT) / "results" / "phase4" / "runs" / run_id
    trace_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    trace_file = trace_dir / "smoke_test_execution.jsonl"
    calls_file = trace_dir / "smoke_test_calls.jsonl"
    summary_file = results_dir / "smoke_test_summary.json"

    bench_path = PROJECT_ROOT / "phase4" / "data" / "benchmark_120.json"
    with open(bench_path, "r", encoding="utf-8") as f:
        bench_cases = {item["query_id"]: item for item in json.load(f)}

    client = ollama.Client(host=OLLAMA_HOST)
    gate = ConservativeEscalationGate()

    case_summaries = []
    all_call_records: List[Dict[str, Any]] = []

    print(f"\n=======================================================")
    print(f"Starting Genuine Live Smoke Test: {run_id}")
    print(f"Target cases: {cases_to_run}")
    print(f"Trace Directory: {trace_dir}")
    print(f"=======================================================\n")

    overall_start = time.perf_counter()

    for idx, qid in enumerate(cases_to_run, 1):
        case_data = bench_cases[qid]
        query = case_data["query"]
        evidence = case_data["evidence"]
        scenario = case_data["scenario_type"]

        print(f"--- Case {idx}/{len(cases_to_run)}: {qid} ({scenario}) ---")

        resource_tracker = CascadeResourceTracker()
        case_trace = []
        stopped_tier = None
        final_answer = None
        stop_reason = None

        prev_resp = None
        prev_feedback = None

        for tier in ["layer_1", "layer_2", "layer_3", "layer_4"]:
            model_name = PHASE4_MODEL_CONFIG[tier]["model"]
            print(f"  [Evaluating {tier}: {model_name}] ...", end="", flush=True)

            # 1. Genuine Generator Call
            ans_text, gen_stats, is_mock = call_tier_model_detailed(
                problem={"query": query, "evidence": evidence},
                layer_key=tier,
                previous_response=prev_resp,
                feedback=prev_feedback,
                client=client,
                force_mock=False,
            )
            if is_mock:
                raise RuntimeError(f"FATAL: Tier model {model_name} fell back to mock during live smoke test!")

            gen_call = ModelCallRecord(
                model=model_name,
                role="tier_generator",
                tier=tier,
                prompt_tokens=gen_stats.get("prompt_tokens"),
                completion_tokens=gen_stats.get("completion_tokens"),
                total_tokens=gen_stats.get("total_tokens"),
                wall_latency_ms=gen_stats.get("wall_latency_ms", 0.0),
                engine_total_duration_ns=gen_stats.get("engine_total_duration_ns"),
                execution_mode="genuine_ollama_call",
            )
            resource_tracker.record_call(gen_call)
            all_call_records.append(gen_call.to_dict())

            # 2. Runtime Verification (ZERO GROUND TRUTH)
            runtime_ver = verify_runtime(
                answer_text=ans_text,
                evidence=evidence,
                query=query,
            )

            # 3. Runtime Judge Call (ZERO GROUND TRUTH)
            judge_res = call_runtime_judge(
                query=query,
                answer=ans_text,
                evidence=evidence,
                judge_model=DEFAULT_JUDGE_MODEL,
                client=client,
                allow_mock_fallback=False,
                runtime_verifier_result=runtime_ver,
            )
            if judge_res.get("is_mock", False):
                raise RuntimeError("FATAL: Runtime judge fell back to mock during live smoke test!")

            judge_call = ModelCallRecord(
                model=DEFAULT_JUDGE_MODEL,
                role="runtime_judge",
                tier=tier,
                prompt_tokens=judge_res.get("prompt_tokens"),
                completion_tokens=judge_res.get("completion_tokens"),
                total_tokens=judge_res.get("total_tokens"),
                wall_latency_ms=judge_res.get("latency_ms", 0.0),
                execution_mode="genuine_ollama_call",
            )
            resource_tracker.record_call(judge_call)
            all_call_records.append(judge_call.to_dict())

            # 4. Strict Combined Verification
            combined = combine_verifications(runtime_ver, judge_res)

            # 5. Leak-Free Conservative Escalation Gate Decision
            stop_decision, decision_reason, risk_flags = gate.evaluate_stop_or_escalate(
                tier=tier,
                runtime_verifier_result=runtime_ver,
                runtime_judge_result=judge_res,
                combined_result=combined,
                evidence=evidence,
                query=query,
                raw_response=ans_text,
            )

            step_record = {
                "tier": tier,
                "model": model_name,
                "generator_tokens": gen_stats.get("total_tokens"),
                "generator_latency_ms": gen_stats.get("wall_latency_ms"),
                "judge_tokens": judge_res.get("total_tokens"),
                "judge_latency_ms": judge_res.get("latency_ms"),
                "response": ans_text,
                "runtime_verifier_pass": runtime_ver["runtime_pass"],
                "runtime_verifier_reasons": runtime_ver["reasons"],
                "judge_verdict": judge_res["overall_verdict"],
                "judge_reasoning": judge_res.get("reasoning"),
                "combined_pass": combined["overall_pass"],
                "gate_stop_decision": stop_decision,
                "gate_decision_reason": decision_reason,
                "gate_risk_flags": risk_flags,
            }
            case_trace.append(step_record)

            ver_symbol = "PASS" if combined["overall_pass"] else "FAIL"
            print(f" -> {ver_symbol} | Gate Stop: {stop_decision} ({decision_reason[:45]}...)")

            if stop_decision or tier == "layer_4":
                stopped_tier = tier
                final_answer = ans_text
                stop_reason = decision_reason
                break

            prev_resp = ans_text
            prev_feedback = "; ".join(runtime_ver.get("reasons", [])) or judge_res.get("reasoning", "")

        resource_tracker.close()
        resource_summary = resource_tracker.get_summary()

        case_summary = {
            "query_id": qid,
            "scenario_type": scenario,
            "stopped_tier": stopped_tier,
            "stop_reason": stop_reason,
            "steps_count": len(case_trace),
            "final_answer": final_answer,
            "final_pass": case_trace[-1]["combined_pass"],
            "resource_summary": resource_summary,
            "trace": case_trace,
        }
        case_summaries.append(case_summary)

        # Append to live traces
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(case_summary) + "\n")

    # Save calls log
    with open(calls_file, "w", encoding="utf-8") as f:
        for c in all_call_records:
            f.write(json.dumps(c) + "\n")

    total_time = round(time.perf_counter() - overall_start, 2)

    total_gen_calls = sum(s["resource_summary"]["generator_calls"] for s in case_summaries)
    total_judge_calls = sum(s["resource_summary"]["runtime_judge_calls"] for s in case_summaries)
    total_prompt_tok = sum(s["resource_summary"]["prompt_tokens"] for s in case_summaries)
    total_comp_tok = sum(s["resource_summary"]["completion_tokens"] for s in case_summaries)
    total_all_tok = sum(s["resource_summary"]["total_tokens"] for s in case_summaries)

    smoke_test_report = {
        "run_id": run_id,
        "timestamp": timestamp,
        "cases_tested": len(case_summaries),
        "total_wall_time_s": total_time,
        "total_generator_calls": total_gen_calls,
        "total_judge_calls": total_judge_calls,
        "total_prompt_tokens": total_prompt_tok,
        "total_completion_tokens": total_comp_tok,
        "total_tokens": total_all_tok,
        "all_outputs_genuine": True,
        "runtime_gate_leak_free": True,
        "case_summaries": [
            {
                "query_id": cs["query_id"],
                "scenario": cs["scenario_type"],
                "stopped_tier": cs["stopped_tier"],
                "final_pass": cs["final_pass"],
                "stop_reason": cs["stop_reason"],
                "steps": cs["steps_count"],
                "generator_calls": cs["resource_summary"]["generator_calls"],
                "judge_calls": cs["resource_summary"]["runtime_judge_calls"],
                "total_tokens": cs["resource_summary"]["total_tokens"],
            }
            for cs in case_summaries
        ],
    }

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(smoke_test_report, f, indent=2)

    print(f"\n=======================================================")
    print(f"Smoke Test Completed in {total_time}s")
    print(f"Total Calls: {total_gen_calls} generator + {total_judge_calls} judge = {total_gen_calls + total_judge_calls}")
    print(f"Total Tokens: {total_all_tok} ({total_prompt_tok} prompt, {total_comp_tok} completion)")
    print(f"Results written to: {summary_file}")
    print(f"=======================================================\n")

    return smoke_test_report


if __name__ == "__main__":
    run_live_smoke_test()
