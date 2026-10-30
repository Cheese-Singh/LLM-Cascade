from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from phase4.benchmark import generate_benchmark_cases, load_benchmark, save_benchmark
from phase4.config import BENCHMARK_PATH, RESULTS_DIR, TRACES_DIR
from phase4.db.sqlite_fixture import get_sqlite_connection, init_sqlite_db, insert_business, insert_invoice
from phase4.db.supabase_adapter import DatabaseAdapter
from phase4.evaluate import run_full_evaluation
from phase4.router import Phase4CascadeRouter
from phase4.runner import call_tier_model
from phase4.verifier import verify_deterministic
from phase4.judge import call_llm_judge, combine_verifications


def cmd_db_init(args: argparse.Namespace) -> None:
    adapter = DatabaseAdapter()
    ping = adapter.ping()
    print("Database Connection Status:", json.dumps(ping, indent=2))
    if ping.get("status") == "connected":
        print("Database initialized successfully.")


def cmd_db_seed(args: argparse.Namespace) -> None:
    cases = load_benchmark()
    conn = get_sqlite_connection()
    init_sqlite_db(conn)

    seen_biz = set()
    b_count, inv_count = 0, 0

    for c in cases:
        ev = c["evidence"]
        biz = ev.get("business", {})
        inv = ev.get("invoice", {})

        b_id = biz.get("business_id")
        if b_id and b_id not in seen_biz:
            insert_business(conn, biz)
            seen_biz.add(b_id)
            b_count += 1

        if inv and b_id:
            insert_invoice(conn, inv, business_id=b_id)
            inv_count += 1

    print(f"Seeded SQLite development fixture: {b_count} businesses, {inv_count} invoices.")


def cmd_db_verify(args: argparse.Namespace) -> None:
    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM business;")
    b_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM invoice;")
    inv_count = cur.fetchone()[0]
    print(f"Database Verification: {b_count} business records, {inv_count} invoice records found.")


def cmd_benchmark_gen(args: argparse.Namespace) -> None:
    cases = save_benchmark()
    print(f"Generated {len(cases)} benchmark cases saved to {BENCHMARK_PATH}")
    train_c = sum(1 for c in cases if c["split"] == "train")
    val_c = sum(1 for c in cases if c["split"] == "validation")
    test_c = sum(1 for c in cases if c["split"] == "test")
    print(f"Splits: Train={train_c}, Validation={val_c}, Held-out Test={test_c}")


def cmd_train_router(args: argparse.Namespace) -> None:
    cases = load_benchmark()
    train_cases = [c for c in cases if c["split"] == "train"]
    val_cases = [c for c in cases if c["split"] == "validation"]

    print("Generating training traces on train split...")
    # Generate traces for train & validation
    router = Phase4CascadeRouter()
    run_full_evaluation(train_cases, router=router, force_mock=True, split_filter="train")
    run_full_evaluation(val_cases, router=router, force_mock=True, split_filter="validation")

    train_traces = [json.loads(line) for line in (TRACES_DIR / "phase4_evaluation_train.jsonl").open()]
    val_traces = [json.loads(line) for line in (TRACES_DIR / "phase4_evaluation_validation.jsonl").open()]

    summary = router.train_on_data(train_traces, val_traces)
    print("Trained Logistic Regression Router with Calibrated Thresholds:")
    print(json.dumps(summary, indent=2))


def cmd_evaluate(args: argparse.Namespace) -> None:
    cases = load_benchmark()
    router = Phase4CascadeRouter()
    print(f"Evaluating 4-Tier Cascade across {args.split} split...")
    metrics = run_full_evaluation(
        benchmark_cases=cases,
        router=router,
        force_mock=not args.live,
        split_filter=args.split,
    )
    print("\n=== Phase IV Benchmark Comparative Evaluation ===")
    print(json.dumps(metrics, indent=2))


def cmd_audit_single(args: argparse.Namespace) -> None:
    """Run an end-to-end audit on a single query and evidence file."""
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input file not found: {in_path}")
        sys.exit(1)

    data = json.loads(in_path.read_text())
    query = data["query"]
    evidence = data["evidence"]
    prob = {"query": query, "evidence": evidence, "ground_truth": data.get("ground_truth", {})}

    router = Phase4CascadeRouter()
    layer_order = ["layer_1", "layer_2", "layer_3", "layer_4"]

    prev_resp = None
    prev_feedback = None
    final_ans = None

    for l_key in layer_order:
        ans, tokens, lat, is_mock = call_tier_model(prob, l_key, prev_resp, prev_feedback, force_mock=not args.live)
        det_res = verify_deterministic(ans, "DISCREPANCY" if "discrepancy" in ans.lower() else "MATCHED", prob["ground_truth"], evidence)
        judge_res = call_llm_judge(query, ans, evidence, prob["ground_truth"], allow_mock_fallback=True, deterministic_result=det_res)
        combined = combine_verifications(det_res, judge_res)
        signals = {"stated_confidence": 0.95 if combined["overall_pass"] else 0.4}

        stop, prob_val = router.should_stop(l_key, signals)
        print(f"[{l_key.upper()}] Verification: {'PASS' if combined['overall_pass'] else 'FAIL'} | Stopping Prob: {prob_val:.3f} | Stop: {stop}")

        if stop or l_key == "layer_4":
            final_ans = {
                "tier": l_key,
                "answer": ans,
                "overall_pass": combined["overall_pass"],
                "deterministic_pass": combined["deterministic_pass"],
                "judge_pass": combined["judge_pass"],
                "tokens": tokens,
                "latency_ms": lat,
            }
            break
        prev_resp = ans
        prev_feedback = "; ".join(det_res.get("reasons", []))

    print("\n--- Final Audited Answer ---")
    print(json.dumps(final_ans, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase IV LLM-Cascade for Financial Auditing")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("db-init", help="Initialize database fixture")
    subparsers.add_parser("db-seed", help="Seed database with synthetic business and invoice records")
    subparsers.add_parser("db-verify", help="Verify records in database")
    subparsers.add_parser("benchmark-gen", help="Generate 120 synthetic benchmark cases")
    subparsers.add_parser("train-router", help="Train router and calibrate thresholds")

    eval_p = subparsers.add_parser("evaluate", help="Evaluate cascade on benchmark")
    eval_p.add_argument("--split", choices=["train", "validation", "test", "all"], default="test")
    eval_p.add_argument("--live", action="store_true", help="Attempt live Ollama model calls")

    audit_p = subparsers.add_parser("audit", help="Audit single input JSON")
    audit_p.add_argument("--input", required=True, help="Path to input JSON containing query and evidence")
    audit_p.add_argument("--live", action="store_true", help="Attempt live Ollama model calls")

    args = parser.parse_args()
    if args.command == "db-init":
        cmd_db_init(args)
    elif args.command == "db-seed":
        cmd_db_seed(args)
    elif args.command == "db-verify":
        cmd_db_verify(args)
    elif args.command == "benchmark-gen":
        cmd_benchmark_gen(args)
    elif args.command == "train-router":
        cmd_train_router(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    elif args.command == "audit":
        cmd_audit_single(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
