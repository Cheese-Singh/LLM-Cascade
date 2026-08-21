from __future__ import annotations

import argparse
import ast
import json
import random
import re
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import ollama
except ImportError:
    ollama = None

EXEC_TIMEOUT_S = 5
DEFAULT_JUDGE_MODEL = "qwen3.5:397b-cloud"

INELIGIBLE_JUDGE_MODELS = {
    "gemma4:cloud",
    "nemotron-3-super:cloud",
    "gpt-oss:120b-cloud",
    "minimax-m3:cloud",
    "gemini-3.6-flash",
}

def _isolate_function(block: str, entry_point: str) -> str:
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return block

    func_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not func_defs:
        return block

    target = next((fn for fn in func_defs if fn.name == entry_point), None)
    if target is None and len(func_defs) == 1:
        target = func_defs[0]
        target.name = entry_point
    if target is None:
        return block

    return ast.unparse(target)


def extract_code(text: str, entry_point: str) -> str:
    if not text:
        return ""

    fence_match = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)

    if fence_match:
        for block in fence_match:
            if f"def {entry_point}" in block:
                return _isolate_function(block, entry_point).strip()
        for block in fence_match:
            isolated = _isolate_function(block, entry_point)
            if isolated != block:
                return isolated.strip()
        return fence_match[0].strip()

    idx = text.find(f"def {entry_point}")
    if idx != -1:
        return _isolate_function(text[idx:], entry_point).strip()

    isolated = _isolate_function(text, entry_point)
    if isolated != text:
        return isolated.strip()

    return text.strip()


class TimeoutException(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutException()


def run_check(problem: dict, candidate_code: str) -> tuple[bool, str]:
    if not candidate_code:
        return False, "No code extracted from response"

    full_source = (
        candidate_code + "\n\n" + problem["check"] + f"\n\ncheck({problem['entry_point']})\n"
    )
    namespace: dict = {}
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(EXEC_TIMEOUT_S)
    try:
        exec(full_source, namespace)
        return True, ""
    except TimeoutException:
        return False, f"Execution timed out after {EXEC_TIMEOUT_S}s"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def index_problems(problems: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in problems}


def index_cascade_results(cascade_data: dict) -> dict[str, dict]:
    """spike_results.json -> {problem_id: result}"""
    results = cascade_data.get("results", cascade_data if isinstance(cascade_data, list) else [])
    return {r["id"]: r for r in results}


def index_gemini_results(gemini_data: list[dict]) -> dict[str, dict]:
    """<questions>_token_results.json -> {problem_id: result}"""
    return {r["id"]: r for r in gemini_data if "error" not in r}

JUDGE_SYSTEM = (
    "You are an impartial evaluator. You will be shown a question and two "
    "candidate responses, labeled 'Response A' and 'Response B'. You do not "
    "know which system produced which response, and you must not guess or "
    "state a guess.\n\n"
    "Score EACH response independently on this rubric:\n"
    "  - correctness (0-2): factually/logically correct, no errors\n"
    "  - completeness (0-2): fully answers what was asked, no major gaps\n"
    "  - clarity (0-1): well-organized and easy to follow\n\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "A_CORRECTNESS: <0-2>\n"
    "A_COMPLETENESS: <0-2>\n"
    "A_CLARITY: <0-1>\n"
    "B_CORRECTNESS: <0-2>\n"
    "B_COMPLETENESS: <0-2>\n"
    "B_CLARITY: <0-1>\n"
    "REASONING: <one or two sentences>"
)


def judge_prompt(question: str, resp_a: str, resp_b: str) -> str:
    return (
        f"Question:\n{question}\n\n"
        f"Response A:\n{resp_a}\n\n"
        f"Response B:\n{resp_b}\n\n"
        f"Score both responses per the rubric."
    )


@dataclass
class JudgeScore:
    a_total: float
    b_total: float
    raw: str
    reasoning: str = ""


def parse_judge_response(text: str) -> Optional[JudgeScore]:
    fields = {}
    for key in ("A_CORRECTNESS", "A_COMPLETENESS", "A_CLARITY",
                "B_CORRECTNESS", "B_COMPLETENESS", "B_CLARITY"):
        m = re.search(rf"{key}\s*:\s*(\d)", text)
        if not m:
            return None
        fields[key] = int(m.group(1))

    reasoning_m = re.search(r"REASONING\s*:\s*(.+)", text, re.DOTALL)
    reasoning = reasoning_m.group(1).strip() if reasoning_m else ""

    a_total = fields["A_CORRECTNESS"] + fields["A_COMPLETENESS"] + fields["A_CLARITY"]
    b_total = fields["B_CORRECTNESS"] + fields["B_COMPLETENESS"] + fields["B_CLARITY"]
    return JudgeScore(a_total=a_total, b_total=b_total, raw=text, reasoning=reasoning)


def call_judge(client, judge_model: str, question: str, resp_a: str, resp_b: str) -> JudgeScore:
    response = client.chat(
        model=judge_model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": judge_prompt(question, resp_a, resp_b)},
        ],
        options={"temperature": 0.0},
    )
    content = response.message.content or ""
    parsed = parse_judge_response(content)
    if parsed is None:
        raise ValueError(f"Judge returned unparseable response:\n{content}")
    return parsed


def judge_open_ended(
    problem: dict,
    cascade_text: str,
    gemini_text: str,
    judge_model: str,
    rng: random.Random,
) -> dict:
    """Blinded, randomized-order judging. Returns a dict with winner + raw score."""
    if ollama is None:
        raise RuntimeError(
            "The 'ollama' package is required for open-ended judging. "
            "Install with: pip install ollama --break-system-packages"
        )
    if judge_model in INELIGIBLE_JUDGE_MODELS:
        raise ValueError(
            f"Judge model '{judge_model}' is in INELIGIBLE_JUDGE_MODELS — it's part "
            f"of one of the systems being compared. Choose a different --judge-model."
        )

    client = ollama.Client()

    cascade_is_a = rng.random() < 0.5
    resp_a = cascade_text if cascade_is_a else gemini_text
    resp_b = gemini_text if cascade_is_a else cascade_text

    score = call_judge(client, judge_model, problem["prompt"], resp_a, resp_b)

    cascade_score = score.a_total if cascade_is_a else score.b_total
    gemini_score = score.b_total if cascade_is_a else score.a_total

    return {
        "judge_model": judge_model,
        "cascade_score": cascade_score,
        "gemini_score": gemini_score,
        "cascade_was_label": "A" if cascade_is_a else "B",
        "reasoning": score.reasoning,
        "raw_judge_response": score.raw,
    }


def get_cascade_tokens(cascade_result: Optional[dict]) -> Optional[int]:
    """Per-problem token cost for the cascade. Requires main.py's TokenLedger
    to have been patched to track tokens per-problem (adds a "tokens" field
    to each result in spike_results.json). Returns None if absent, so older
    result files degrade gracefully instead of reporting a wrong number."""
    if cascade_result is None:
        return None
    return cascade_result.get("tokens")


def compare_problem(
    problem: dict,
    cascade_result: Optional[dict],
    gemini_result: Optional[dict],
    judge_model: str,
    rng: random.Random,
) -> dict:
    problem_id = problem["id"]
    is_code = "check" in problem and "entry_point" in problem

    row: dict = {"id": problem_id, "type": "code" if is_code else "open_ended"}

    if cascade_result is None:
        row["cascade_status"] = "missing"
    if gemini_result is None:
        row["gemini_status"] = "missing"

    if is_code:
        cascade_code = cascade_result.get("final_code", "") if cascade_result else ""
        cascade_passed, cascade_err = (
            run_check(problem, cascade_code) if cascade_result else (False, "no result")
        )

        gemini_text = gemini_result.get("response_text", "") if gemini_result else ""
        gemini_code = extract_code(gemini_text, problem["entry_point"])
        gemini_passed, gemini_err = (
            run_check(problem, gemini_code) if gemini_result else (False, "no result")
        )

        row.update({
            "cascade_passed": cascade_passed,
            "cascade_error": cascade_err,
            "cascade_hops": cascade_result.get("n_hops_used") if cascade_result else None,
            "cascade_tokens": get_cascade_tokens(cascade_result),
            "gemini_passed": gemini_passed,
            "gemini_error": gemini_err,
            "gemini_tokens": gemini_result.get("total_tokens") if gemini_result else None,
        })
    else:
        cascade_text = cascade_result.get("final_code", "") if cascade_result else ""
        gemini_text = gemini_result.get("response_text", "") if gemini_result else ""

        if cascade_result and gemini_result:
            judged = judge_open_ended(problem, cascade_text, gemini_text, judge_model, rng)
            row.update(judged)
            row["cascade_won"] = judged["cascade_score"] > judged["gemini_score"]
            row["tie"] = judged["cascade_score"] == judged["gemini_score"]
        else:
            row["judge_skipped_reason"] = "missing cascade or gemini result"

    return row


def build_report(rows: list[dict], cascade_total_tokens: int, gemini_total_tokens: int) -> str:
    code_rows = [r for r in rows if r["type"] == "code"]
    open_rows = [r for r in rows if r["type"] == "open_ended"]

    lines = ["# Cascade vs. Gemini 3.6 Flash — Comparison Report", ""]

    if code_rows:
        cascade_pass = sum(1 for r in code_rows if r.get("cascade_passed"))
        gemini_pass = sum(1 for r in code_rows if r.get("gemini_passed"))
        n = len(code_rows)
        has_cascade_tokens = any(r.get("cascade_tokens") is not None for r in code_rows)

        lines += [
            "## Code problems (execution-graded, no judge involved)",
            "",
            f"- Cascade pass@1: {cascade_pass}/{n} ({cascade_pass/n:.1%})",
            f"- Gemini 3.6 Flash pass@1: {gemini_pass}/{n} ({gemini_pass/n:.1%})",
            "",
        ]

        if has_cascade_tokens:
            lines += [
                "| Problem | Cascade | Gemini | Cascade tokens | Gemini tokens | Token delta |",
                "|---|---|---|---|---|---|",
            ]
            for r in code_rows:
                ct, gt = r.get("cascade_tokens"), r.get("gemini_tokens")
                delta = f"+{ct - gt}" if (ct is not None and gt is not None) else "-"
                lines.append(
                    f"| {r['id']} | {'✅' if r.get('cascade_passed') else '❌'} | "
                    f"{'✅' if r.get('gemini_passed') else '❌'} | "
                    f"{ct if ct is not None else '-'} | {gt if gt is not None else '-'} | {delta} |"
                )
            lines += [
                "",
                "> Token delta = cascade tokens minus Gemini tokens for that problem "
                "(positive = cascade spent more). Read this next to the pass/fail "
                "columns — a positive delta on a problem cascade got right and Gemini "
                "got wrong is the actual 'spent more, got it right' evidence; a "
                "positive delta where both passed is pure overhead worth noting as "
                "a limitation.",
            ]
        else:
            lines += [
                "| Problem | Cascade | Gemini | Cascade hops | Gemini tokens |",
                "|---|---|---|---|---|",
            ]
            for r in code_rows:
                lines.append(
                    f"| {r['id']} | {'✅' if r.get('cascade_passed') else '❌'} | "
                    f"{'✅' if r.get('gemini_passed') else '❌'} | "
                    f"{r.get('cascade_hops', '-')} | {r.get('gemini_tokens', '-')} |"
                )
            lines += [
                "",
                "> Per-problem cascade token counts aren't available in this run "
                "(spike_results.json has no 'tokens' field per result — see "
                "main_py_patch_notes.md to add per-problem token tracking to "
                "main.py). Showing hop count as a rough proxy instead.",
            ]
        lines.append("")

    if open_rows:
        judged = [r for r in open_rows if "cascade_score" in r]
        cascade_wins = sum(1 for r in judged if r.get("cascade_won"))
        ties = sum(1 for r in judged if r.get("tie"))
        gemini_wins = len(judged) - cascade_wins - ties
        lines += [
            "## Open-ended problems (blinded LLM-judge scored)",
            "",
            f"- Judge model: {judged[0]['judge_model'] if judged else 'n/a'} "
            f"(not part of either system under test)",
            f"- Cascade wins: {cascade_wins}/{len(judged)}  |  "
            f"Gemini wins: {gemini_wins}/{len(judged)}  |  Ties: {ties}/{len(judged)}",
            "",
            "| Problem | Cascade score | Gemini score | Winner |",
            "|---|---|---|---|",
        ]
        for r in judged:
            winner = "Tie" if r.get("tie") else ("Cascade" if r.get("cascade_won") else "Gemini")
            lines.append(
                f"| {r['id']} | {r['cascade_score']}/5 | {r['gemini_score']}/5 | {winner} |"
            )
        lines += [
            "",
            "> Judging is blinded (responses labeled A/B, order randomized per question) "
            "and rubric-based (correctness/completeness/clarity). See raw_judge_response "
            "in the JSON output for full reasoning per question. A manual audit sample "
            "should be spot-checked against these scores before citing this result "
            "(see --audit-sample).",
            "",
        ]

    lines += [
        "## Token totals (whole run)",
        "",
        f"- Cascade total tokens: {cascade_total_tokens}",
        f"- Gemini total tokens: {gemini_total_tokens}",
        "",
        "> Cascade tokens reflect every hop across the chain (gemma → nemotron → "
        "gptoss → minimax as needed); Gemini tokens reflect a single call per "
        "question. These are not directly divided into a single 'tokens per point "
        "of accuracy' ratio here because the two systems' unit of work differs — "
        "report both totals and let the pass-rate / win-rate tables above carry "
        "the quality comparison.",
    ]

    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--problems", type=Path, default=Path("all_questions.json"),
                    help="Problems file (default: all_questions.json)")
    p.add_argument("--cascade-results", type=Path, default=Path("spike_results.json"),
                    help="Cascade output from main.py (default: spike_results.json)")
    p.add_argument("--gemini-results", type=Path, default=Path("all_questions_token_results.json"),
                    help="Gemini output from gemini_token_test.py (default: all_questions_token_results.json)")
    p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL,
                    help=f"Third-party judge for open-ended problems (default: {DEFAULT_JUDGE_MODEL}). "
                         f"Must NOT be a model used inside either system under test.")
    p.add_argument("--out-dir", type=Path, default=Path("report"))
    p.add_argument("--seed", type=int, default=42, help="RNG seed for judge A/B order randomization")
    p.add_argument("--audit-sample", type=float, default=0.15,
                    help="Fraction of judged open-ended items to flag for manual audit (default 0.15)")
    return p.parse_args()


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    problems = load_json(args.problems)
    problems_by_id = index_problems(problems)

    cascade_data = load_json(args.cascade_results)
    cascade_by_id = index_cascade_results(cascade_data)
    cascade_total_tokens = cascade_data.get("total_tokens", 0)

    gemini_data = load_json(args.gemini_results)
    gemini_by_id = index_gemini_results(gemini_data)
    gemini_total_tokens = sum(
        r.get("total_tokens", 0) for r in gemini_by_id.values()
    )

    rows = []
    for problem_id, problem in problems_by_id.items():
        cascade_result = cascade_by_id.get(problem_id)
        gemini_result = gemini_by_id.get(problem_id)
        try:
            row = compare_problem(problem, cascade_result, gemini_result, args.judge_model, rng)
        except Exception as e:
            row = {"id": problem_id, "type": "error", "error": str(e)}
            print(f"  [error] {problem_id}: {e}", file=sys.stderr)
        rows.append(row)
        if row["type"] == "code":
            status = f"cascade_passed={row.get('cascade_passed', '?')}"
        elif row["type"] == "open_ended" and "cascade_score" in row:
            status = f"cascade {row['cascade_score']}/5 vs gemini {row['gemini_score']}/5"
        else:
            status = row.get("error", "skipped")
        print(f"  scored {problem_id} ({row['type']}) -> {status}")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    report_md = build_report(rows, cascade_total_tokens, gemini_total_tokens)
    (args.out_dir / "comparison_report.md").write_text(report_md)

    (args.out_dir / "comparison_results.json").write_text(
        json.dumps({"rows": rows, "judge_model": args.judge_model, "seed": args.seed}, indent=2, default=str)
    )

    judged_rows = [r for r in rows if "cascade_score" in r]
    if judged_rows and args.audit_sample > 0:
        k = max(1, round(len(judged_rows) * args.audit_sample))
        audit_ids = [r["id"] for r in rng.sample(judged_rows, k)]
        (args.out_dir / "audit_sample.json").write_text(
            json.dumps({"instructions": "Manually review these judged items and record "
                        "whether you agree with the judge's winner. Report the agreement "
                        "rate in your README.", "ids": audit_ids}, indent=2)
        )
        print(f"\nAudit sample written: {k}/{len(judged_rows)} judged items "
              f"flagged in {args.out_dir / 'audit_sample.json'}")

    print(f"\nReport written to {args.out_dir / 'comparison_report.md'}")
    print(f"Raw results written to {args.out_dir / 'comparison_results.json'}")


if __name__ == "__main__":
    main()