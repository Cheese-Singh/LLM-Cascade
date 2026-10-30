from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama

from phase4.accounting import CascadeResourceTracker, ModelCallRecord
from phase4.config import DEFAULT_JUDGE_MODEL, OLLAMA_HOST, PHASE4_MODEL_CONFIG
from phase4.judge import call_llm_judge, call_runtime_judge, combine_verifications
from phase4.runner import call_tier_model_detailed
from phase4.signals import extract_signals, signal_vector
from phase4.verifier import verify_deterministic, verify_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TIER_ORDER = ["layer_1", "layer_2", "layer_3", "layer_4"]


def format_duration(seconds: float) -> str:
    """Format duration in human-readable MM:SS or HH:MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


class GenuineDatasetCollector:
    """
    Collects genuine LLM responses across tiers L1-L4 for the benchmark dataset,
    capturing leak-free runtime features, complete provider resource accounting,
    and deriving trustworthy minimum sufficient tier (L*) labels via offline reference evaluation.
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        reuse_historical_calls: bool = True,
    ):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_id = run_id or f"dataset_collection_{timestamp}"
        self.traces_dir = PROJECT_ROOT / "traces" / "phase4" / "runs" / self.run_id
        self.results_dir = PROJECT_ROOT / "results" / "phase4" / "runs" / self.run_id
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self.client = ollama.Client(host=OLLAMA_HOST)
        self.benchmark_path = PROJECT_ROOT / "phase4" / "data" / "benchmark_120.json"
        with open(self.benchmark_path, "r", encoding="utf-8") as f:
            self.benchmark_data = {d["query_id"]: d for d in json.load(f)}

        self.reuse_historical_calls = reuse_historical_calls
        self.historical_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if reuse_historical_calls:
            self._load_historical_cache()

    def _load_historical_cache(self) -> None:
        """Load verified genuine calls from previous runs if available."""
        pilot_calls_path = PROJECT_ROOT / "traces" / "phase4" / "runs" / "pilot_20261020_live_eval" / "genuine_calls.jsonl"
        if pilot_calls_path.exists():
            try:
                with open(pilot_calls_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        qid = record.get("query_id")
                        tier = record.get("tier")
                        if qid and tier and record.get("execution_mode") == "genuine_ollama_call" and record.get("raw_response"):
                            self.historical_cache[(qid, tier)] = record
                logger.info(f"Loaded {len(self.historical_cache)} verified historical calls into memory cache.")
            except Exception as e:
                logger.warning(f"Could not load historical calls: {e}")

    def collect_dataset_for_split(
        self,
        split_name: str,  # "train" or "validation"
        max_cases: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes genuine inference and offline evaluation across all tiers for cases in specified split.
        Saves intermediate traces incrementally to support resumption.
        """
        assert split_name in ("train", "validation"), f"Refusing to collect for split '{split_name}'. Test split must remain separate!"

        cases = [c for c in self.benchmark_data.values() if c.get("split") == split_name]
        if max_cases is not None:
            cases = cases[:max_cases]

        trace_file = self.traces_dir / f"{split_name}_traces.jsonl"
        completed_qids: Set[str] = set()

        # Check for resumption
        existing_cases = []
        if trace_file.exists():
            with open(trace_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        c_dict = json.loads(line)
                        existing_cases.append(c_dict)
                        completed_qids.add(c_dict["query_id"])
            logger.info(f"Resuming: found {len(completed_qids)} already completed {split_name} cases in {trace_file.name}")

        remaining_cases = [c for c in cases if c["query_id"] not in completed_qids]
        total_cases = len(cases)
        completed_count = len(completed_qids)

        print(f"\n=======================================================")
        print(f"Collecting Genuine Dataset: Split '{split_name.upper()}' ({total_cases} cases)")
        print(f"Run ID: {self.run_id}")
        print(f"Trace Output: {trace_file}")
        print(f"Already completed: {completed_count}/{total_cases} | Remaining: {len(remaining_cases)}")
        print(f"=======================================================\n")

        start_time = time.perf_counter()
        case_durations: List[float] = []

        for idx, case in enumerate(remaining_cases, completed_count + 1):
            qid = case["query_id"]
            query = case["query"]
            evidence = case["evidence"]
            ground_truth = case["ground_truth"]
            scenario = case["scenario_type"]

            case_start = time.perf_counter()
            elapsed_total = case_start - start_time

            # ETA calculation
            if case_durations:
                avg_case_time = sum(case_durations) / len(case_durations)
                remaining_time = avg_case_time * (total_cases - idx + 1)
                eta_str = format_duration(remaining_time)
            else:
                eta_str = "Estimating..."

            print(
                f"[{split_name.upper()}] Case {idx}/{total_cases} ({idx/total_cases*100:.1f}%) | "
                f"ID: {qid} ({scenario}) | Elapsed: {format_duration(elapsed_total)} | ETA: {eta_str}",
                flush=True,
            )

            tier_evaluations: Dict[str, Any] = {}
            prev_resp: Optional[str] = None
            prev_feedback: Optional[str] = None

            case_tracker = CascadeResourceTracker()

            for tier in TIER_ORDER:
                model_name = PHASE4_MODEL_CONFIG[tier]["model"]
                print(f"  -> {tier} ({model_name}) ...", end="", flush=True)

                # Check historical cache first
                hist = self.historical_cache.get((qid, tier))
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
                        client=self.client,
                        force_mock=False,
                    )
                    if is_mock:
                        raise RuntimeError(f"FATAL: Tier model {model_name} fell back to mock for {qid}!")

                # Record generator call
                gen_call = ModelCallRecord(
                    model=model_name,
                    role="tier_generator",
                    tier=tier,
                    prompt_tokens=gen_stats.get("prompt_tokens"),
                    completion_tokens=gen_stats.get("completion_tokens"),
                    total_tokens=gen_stats.get("total_tokens"),
                    wall_latency_ms=gen_stats.get("wall_latency_ms", 0.0),
                    engine_total_duration_ns=gen_stats.get("engine_total_duration_ns"),
                    execution_mode=gen_stats.get("execution_mode", "genuine_ollama_call"),
                )
                case_tracker.record_call(gen_call)

                # Runtime Leak-Free Verification
                runtime_ver = verify_runtime(
                    answer_text=ans_text,
                    evidence=evidence,
                    query=query,
                )

                # Runtime Leak-Free Judge Call
                runtime_judge = call_runtime_judge(
                    query=query,
                    answer=ans_text,
                    evidence=evidence,
                    judge_model=DEFAULT_JUDGE_MODEL,
                    client=self.client,
                    allow_mock_fallback=False,
                    runtime_verifier_result=runtime_ver,
                )
                runtime_comb = combine_verifications(runtime_ver, runtime_judge)

                # Record runtime judge call
                judge_call = ModelCallRecord(
                    model=DEFAULT_JUDGE_MODEL,
                    role="runtime_judge",
                    tier=tier,
                    prompt_tokens=runtime_judge.get("prompt_tokens"),
                    completion_tokens=runtime_judge.get("completion_tokens"),
                    total_tokens=runtime_judge.get("total_tokens"),
                    wall_latency_ms=runtime_judge.get("latency_ms", 0.0),
                    execution_mode="genuine_ollama_call",
                )
                case_tracker.record_call(judge_call)

                # Offline Benchmark Reference Evaluation (Uses Gold Ground Truth ONLY here)
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
                    client=self.client,
                    allow_mock_fallback=False,
                    deterministic_result=offline_det,
                )
                offline_comb = combine_verifications(offline_det, offline_judge)

                # Record offline judge call
                off_judge_call = ModelCallRecord(
                    model=DEFAULT_JUDGE_MODEL,
                    role="offline_judge",
                    tier=tier,
                    prompt_tokens=offline_judge.get("prompt_tokens"),
                    completion_tokens=offline_judge.get("completion_tokens"),
                    total_tokens=offline_judge.get("total_tokens"),
                    wall_latency_ms=offline_judge.get("latency_ms", 0.0),
                    execution_mode="genuine_ollama_call",
                )
                case_tracker.record_call(off_judge_call)

                # Runtime Router Features (derived strictly from input + candidate answer)
                signals = extract_signals(
                    response=ans_text,
                    problem={"query": query, "evidence": evidence},
                    previous_response=prev_resp,
                )
                feat_vec = signal_vector(signals)

                # Check offline sufficiency
                offline_is_pass = bool(offline_comb["overall_pass"])

                tier_evaluations[tier] = {
                    "model": model_name,
                    "response": ans_text,
                    "generator_tokens": gen_stats.get("total_tokens"),
                    "generator_latency_ms": gen_stats.get("wall_latency_ms"),
                    "runtime_features": signals,
                    "runtime_feature_vector": feat_vec,
                    "runtime_verifier_pass": runtime_ver["runtime_pass"],
                    "runtime_verifier_reasons": runtime_ver["reasons"],
                    "runtime_judge_verdict": runtime_judge.get("overall_verdict"),
                    "runtime_judge_reasoning": runtime_judge.get("reasoning"),
                    "runtime_overall_pass": runtime_comb["overall_pass"],
                    "offline_deterministic_pass": offline_det["deterministic_pass"],
                    "offline_deterministic_reasons": offline_det["reasons"],
                    "offline_judge_verdict": offline_judge.get("overall_verdict"),
                    "offline_judge_reasoning": offline_judge.get("reasoning"),
                    "offline_overall_pass": offline_is_pass,
                    "offline_disagreement": offline_comb["disagreement"],
                    "offline_disagreement_type": offline_comb["disagreement_type"],
                }

                status_tag = "PASS" if offline_is_pass else "FAIL"
                print(f" {status_tag} (det={offline_det['deterministic_pass']}, judge={offline_judge.get('overall_verdict')})", flush=True)

                prev_resp = ans_text
                prev_feedback = "; ".join(runtime_ver.get("reasons", [])) or runtime_judge.get("reasoning", "")

            case_tracker.close()
            resource_summary = case_tracker.get_summary()

            # -------------------------------------------------------------
            # Derive Minimum Sufficient Tier (L*)
            # -------------------------------------------------------------
            min_sufficient_tier = None
            label_derivation_notes = []
            manual_review_status = "CERTIFIED"

            for tier in TIER_ORDER:
                t_eval = tier_evaluations[tier]
                if t_eval["offline_overall_pass"]:
                    min_sufficient_tier = tier
                    label_derivation_notes.append(f"Lowest tier achieving both offline deterministic and judge pass: {tier}")
                    break

            # If no tier achieved a full consensus pass
            if min_sufficient_tier is None:
                # Check if L3 or L4 had deterministic pass or judge pass
                det_passes = [t for t in TIER_ORDER if tier_evaluations[t]["offline_deterministic_pass"]]
                judge_passes = [t for t in TIER_ORDER if tier_evaluations[t]["offline_judge_verdict"] == "PASS"]

                if det_passes and judge_passes:
                    overlap = [t for t in det_passes if t in judge_passes]
                    if overlap:
                        min_sufficient_tier = overlap[0]
                        label_derivation_notes.append(f"Selected lowest overlapping pass: {min_sufficient_tier}")
                    else:
                        min_sufficient_tier = "layer_4"
                        manual_review_status = "REQUIRES_REVIEW"
                        label_derivation_notes.append("Judge and deterministic verifier disagreed at all tiers; escalated to terminal layer_4 for review.")
                else:
                    min_sufficient_tier = "layer_4"
                    manual_review_status = "REQUIRES_REVIEW"
                    label_derivation_notes.append("No evaluated tier satisfied complete offline reference criteria; assigned layer_4 with review flag.")

            case_duration = time.perf_counter() - case_start
            case_durations.append(case_duration)

            case_record = {
                "query_id": qid,
                "split": split_name,
                "scenario_type": scenario,
                "query": query,
                "evidence": evidence,
                "ground_truth_reference": ground_truth,
                "minimum_sufficient_tier": min_sufficient_tier,
                "label_derivation_notes": label_derivation_notes,
                "manual_review_status": manual_review_status,
                "tier_evaluations": tier_evaluations,
                "resource_summary": resource_summary,
            }

            # Append incrementally to trace file
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(case_record) + "\n")

            existing_cases.append(case_record)
            print(f"  => Assigned L*: {min_sufficient_tier} [{manual_review_status}] (duration: {case_duration:.1f}s)\n")

        # Save complete split dataset JSON
        dataset_json_path = self.results_dir / f"genuine_{split_name}_dataset.json"
        with open(dataset_json_path, "w", encoding="utf-8") as f:
            json.dump(existing_cases, f, indent=2)

        # Also save summary metrics
        label_distribution = {}
        for c in existing_cases:
            l_star = c["minimum_sufficient_tier"]
            label_distribution[l_star] = label_distribution.get(l_star, 0) + 1

        review_count = sum(1 for c in existing_cases if c["manual_review_status"] == "REQUIRES_REVIEW")
        total_time = time.perf_counter() - start_time

        summary_data = {
            "split": split_name,
            "total_cases": len(existing_cases),
            "label_distribution": label_distribution,
            "requires_manual_review_count": review_count,
            "certified_count": len(existing_cases) - review_count,
            "total_wall_time_s": round(total_time, 2),
            "dataset_file": str(dataset_json_path),
            "traces_file": str(trace_file),
        }
        with open(self.results_dir / f"{split_name}_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

        print(f"\n=======================================================")
        print(f"Completed Collection for {split_name.upper()} in {format_duration(total_time)}")
        print(f"Label distribution: {label_distribution}")
        print(f"Certified: {len(existing_cases) - review_count} | Requires Review: {review_count}")
        print(f"Dataset saved to: {dataset_json_path}")
        print(f"=======================================================\n")

        return existing_cases


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect genuine LLM training traces and derive L* labels.")
    parser.add_argument("--split", choices=["train", "validation", "both"], default="both")
    parser.add_argument("--max_cases", type=int, default=None)
    parser.add_argument("--run_id", type=str, default=None)
    args = parser.parse_args()

    collector = GenuineDatasetCollector(run_id=args.run_id)
    if args.split in ("train", "both"):
        collector.collect_dataset_for_split("train", max_cases=args.max_cases)
    if args.split in ("validation", "both"):
        collector.collect_dataset_for_split("validation", max_cases=args.max_cases)
