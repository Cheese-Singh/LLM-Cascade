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
DEFAULT_JUDGE_MODEL = "gpt-oss:120b-cloud"

INELIGIBLE_JUDGE_MODELS = {
    "gemma4:cloud",
    "nemotron-3-super:cloud",
    "gpt-oss:20b-cloud",
    "nemotron-3-ultra:cloud",
    "gemini-3.6-flash"
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

    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    return ast.unparse(ast.Module(body=[*imports, target], type_ignores=[]))


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


def index_cascade_results(cascade_data: Any) -> dict[str, dict]:
    """Accept either a list of results or the legacy dict wrapper from spike_results.json."""
    if isinstance(cascade_data, dict):
        results = cascade_data.get("results", [])
    elif isinstance(cascade_data, list):
        results = cascade_data
    else:
        results = []
    return {r["id"]: r for r in results if isinstance(r, dict) and "id" in r}


def index_gemini_results(gemini_data: Any) -> dict[str, dict]:
    """Index either the legacy Gemini list or gemini_conditions.py output."""
    results = gemini_data.get("results", gemini_data) if isinstance(gemini_data, dict) else gemini_data
    return {r["id"]: r for r in results if "error" not in r}


def gemini_code(result: Optional[dict], entry_point: str) -> str:
    """Extract code from either a condition result or the legacy baseline."""
    if not result:
        return ""
    code = result.get("final_code")
    if code is not None:
        return code
    return extract_code(result.get("response_text", ""), entry_point)


def gemini_tokens(result: Optional[dict]) -> Optional[int]:
    if not result:
        return None
    usage = result.get("usage")
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is not None:
            return total
    return result.get("tokens", result.get("total_tokens"))


def cascade_model_tokens(result: Optional[dict]) -> str:
    """Format per-hop model totals for the human-readable report."""
    if not result:
        return "-"
    if result.get("tokens_by_model"):
        return ", ".join(
            f"{model}: {total}"
            for model, total in result["tokens_by_model"].items()
        )
    parts = []
    for hop in result.get("hops", []):
        usage = hop.get("usage", {})
        total = usage.get("total_tokens")
        if total is not None:
            parts.append(f"{hop.get('model', '?')}: {total}")
    return ", ".join(parts) or "-"

JUDGE_SYSTEM = (
    "You are an impartial evaluator. You will be shown a question and two "
    "candidate responses, labeled 'Response A' and 'Response B'. You do not "
    "know which system produced which response, and you must not guess or "
    "state a guess.\n\n"
    "Score EACH response independently on this rubric:\n"
    "  - correctness (0-2): factually/logically correct, no errors\n"
    "  - completeness (0-2): fully answers what was asked, no major gaps\n"
    "  - clarity (0-1): well-organized and easy to follow\n\n"
    "Also give a fuzzy preference between A and B, as a percentage split that sums to 100.\n"
    "Example: if you prefer A a bit more, use A_PREFERENCE: 61 and B_PREFERENCE: 39.\n\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "A_CORRECTNESS: <0-2>\n"
    "A_COMPLETENESS: <0-2>\n"
    "A_CLARITY: <0-1>\n"
    "B_CORRECTNESS: <0-2>\n"
    "B_COMPLETENESS: <0-2>\n"
    "B_CLARITY: <0-1>\n"
    "A_PREFERENCE: <0-100>\n"
    "B_PREFERENCE: <0-100>\n"
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
    a_pref: int
    b_pref: int
    raw: str
    reasoning: str = ""


def parse_judge_response(text: str) -> Optional[JudgeScore]:
    fields = {}
    for key in ("A_CORRECTNESS", "A_COMPLETENESS", "A_CLARITY",
                "B_CORRECTNESS", "B_COMPLETENESS", "B_CLARITY",
                "A_PREFERENCE", "B_PREFERENCE"):
        m = re.search(rf"{key}\s*:\s*(\d+)", text)
        if not m:
            return None
        fields[key] = int(m.group(1))

    reasoning_m = re.search(r"REASONING\s*:\s*(.+)", text, re.DOTALL)
    reasoning = reasoning_m.group(1).strip() if reasoning_m else ""

    a_total = fields["A_CORRECTNESS"] + fields["A_COMPLETENESS"] + fields["A_CLARITY"]
    b_total = fields["B_CORRECTNESS"] + fields["B_COMPLETENESS"] + fields["B_CLARITY"]
    return JudgeScore(
        a_total=a_total,
        b_total=b_total,
        a_pref=fields["A_PREFERENCE"],
        b_pref=fields["B_PREFERENCE"],
        raw=text,
        reasoning=reasoning,
    )


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

    cascade_pref = score.a_pref if cascade_is_a else score.b_pref
    gemini_pref = score.b_pref if cascade_is_a else score.a_pref
    tie = abs(cascade_pref - gemini_pref) <= 2
    cascade_won = (not tie) and (cascade_pref > gemini_pref)

    return {
        "judge_model": judge_model,
        "cascade_score": cascade_score,
        "gemini_score": gemini_score,
        "cascade_was_label": "A" if cascade_is_a else "B",
        "cascade_pref_pct": cascade_pref,
        "gemini_pref_pct": gemini_pref,
        "cascade_won": cascade_won,
        "tie": tie,
        "preference_margin_pct": abs(cascade_pref - gemini_pref),
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
    gemini_results: dict[str, Optional[dict]],
    judge_model: str,
    rng: random.Random,
) -> dict:
    problem_id = problem["id"]
    is_code = "check" in problem and "entry_point" in problem

    row: dict = {"id": problem_id, "type": "code" if is_code else "open_ended"}

    if cascade_result is None:
        row["cascade_status"] = "missing"
    if is_code:
        cascade_code = cascade_result.get("final_code", "") if cascade_result else ""
        cascade_passed, cascade_err = (
            run_check(problem, cascade_code) if cascade_result else (False, "no result")
        )
        row.update({
            "cascade_passed": cascade_passed,
            "cascade_error": cascade_err,
            "cascade_hops": cascade_result.get("n_hops_used") if cascade_result else None,
            "cascade_tokens": get_cascade_tokens(cascade_result),
            "cascade_model_tokens": cascade_model_tokens(cascade_result),
        })

        for condition, result in gemini_results.items():
            code = gemini_code(result, problem["entry_point"])
            passed, error = run_check(problem, code) if result else (False, "no result")
            row[f"{condition}_passed"] = passed
            row[f"{condition}_error"] = error
            row[f"{condition}_tokens"] = gemini_tokens(result)
    else:
        cascade_text = (
            cascade_result.get("final_code", cascade_result.get("final_answer", cascade_result.get("response_text", "")))
            if cascade_result else ""
        )
        for condition, result in gemini_results.items():
            if cascade_result and result:
                gemini_text = result.get("final_code", result.get("final_answer", result.get("response_text", "")))
                judged = judge_open_ended(problem, cascade_text, gemini_text, judge_model, rng)
                row[f"{condition}_judged"] = judged
                row[f"{condition}_score"] = judged.get("cascade_score")
                row[f"{condition}_winner"] = "Cascade" if judged.get("cascade_won") else "Gemini" if not judged.get("tie") else "Tie"
                row["cascade_score"] = judged.get("cascade_score")
                row["gemini_score"] = judged.get("gemini_score")
                row["cascade_won"] = judged.get("cascade_won")
                row["tie"] = judged.get("tie")
                row["cascade_pref_pct"] = judged.get("cascade_pref_pct")
                row["gemini_pref_pct"] = judged.get("gemini_pref_pct")
                row["preference_margin_pct"] = judged.get("preference_margin_pct")
                row["judge_model"] = judge_model
            else:
                row[f"{condition}_judge_skipped_reason"] = "missing cascade or Gemini result"

    return row


def build_report(
    rows: list[dict], cascade_total_tokens: int, cascade_model_totals: dict[str, dict],
    gemini_totals: dict[str, int]
) -> str:
    code_rows = [r for r in rows if r["type"] == "code"]
    open_rows = [r for r in rows if r["type"] == "open_ended"]

    lines = ["# Cascade vs. Gemini Conditions — Comparison Report", ""]

    if code_rows:
        cascade_pass = sum(1 for r in code_rows if r.get("cascade_passed"))
        n = len(code_rows)
        has_cascade_tokens = any(r.get("cascade_tokens") is not None for r in code_rows)

        lines += [
            "## Code problems (execution-graded, no judge involved)",
            "",
            f"- Cascade pass@1: {cascade_pass}/{n} ({cascade_pass/n:.1%})",
        ]
        for condition in gemini_totals:
            passed = sum(1 for r in code_rows if r.get(f"{condition}_passed"))
            lines.append(f"- {condition} pass@1: {passed}/{n} ({passed/n:.1%})")
        lines.append("")

        if has_cascade_tokens:
            conditions = list(gemini_totals)
            headers = ["Problem", "Cascade"] + conditions + ["Cascade model tokens", "Cascade tokens"] + [f"{c} tokens" for c in conditions]
            lines += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
            for r in code_rows:
                values = [r["id"], "✅" if r.get("cascade_passed") else "❌"]
                values += ["✅" if r.get(f"{c}_passed") else "❌" for c in conditions]
                values += [r.get("cascade_model_tokens", "-"), str(r.get("cascade_tokens", "-"))]
                values += [str(r.get(f"{c}_tokens", "-")) for c in conditions]
                lines.append("| " + " | ".join(values) + " |")
            lines += [
                "",
                "> Token columns are per-problem totals. Compare them alongside the "
                "pass/fail columns; a condition may improve correctness at additional cost.",
            ]
        else:
            conditions = list(gemini_totals)
            headers = ["Problem", "Cascade"] + conditions + ["Cascade hops"] + [f"{c} tokens" for c in conditions]
            lines += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
            for r in code_rows:
                values = [r["id"], "✅" if r.get("cascade_passed") else "❌"]
                values += ["✅" if r.get(f"{c}_passed") else "❌" for c in conditions]
                values += [str(r.get("cascade_hops", "-"))]
                values += [str(r.get(f"{c}_tokens", "-")) for c in conditions]
                lines.append("| " + " | ".join(values) + " |")
            lines += [
                "",
                "> Per-problem cascade token counts are unavailable; cascade hops are shown instead.",
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
            "| Problem | Cascade score | Gemini score | Preference | Winner |",
            "|---|---:|---:|---:|---|",
        ]
        for r in judged:
            winner = "Tie" if r.get("tie") else ("Cascade" if r.get("cascade_won") else "Gemini")
            pref = "Tie"
            if not r.get("tie"):
                pref = (
                    f"Cascade +{r.get('cascade_pref_pct', 50)}%"
                    if r.get("cascade_won")
                    else f"Gemini +{r.get('gemini_pref_pct', 50)}%"
                )
            lines.append(
                f"| {r['id']} | {r['cascade_score']}/5 | {r['gemini_score']}/5 | {pref} | {winner} |"
            )
        lines += [
            "",
            "> Judging is blinded (responses labeled A/B, order randomized per question) "
            "and rubric-based (correctness/completeness/clarity), with a fuzzy percentage preference "
            "between the two responses. See raw_judge_response in the JSON output for the full judge text.",
            "",
        ]

    lines += [
        "## Token totals (whole run)",
        "",
        f"- Cascade total tokens: {cascade_total_tokens}",
    ]
    for condition, total in gemini_totals.items():
        lines.append(f"- {condition} total tokens: {total}")
    lines += ["",]
    lines += [
        "",
        "### Cascade by model",
        "",
        "| Model | Calls | Prompt tokens | Completion tokens | Total tokens |",
        "|---|---:|---:|---:|---:|",
    ]
    for model, counts in cascade_model_totals.items():
        prompt = counts.get("prompt", 0)
        completion = counts.get("completion", 0)
        lines.append(
            f"| {model} | {counts.get('calls', 0)} | {prompt} | {completion} | {prompt + completion} |"
        )
    lines += [
        "",
        "> Cascade tokens include every model hop. Gemini condition totals include all calls used by that condition.",
    ]

    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--problems", type=Path, default=Path("all_questions.json"),
                    help="Problems file (default: all_questions.json)")
    p.add_argument("--cascade-results", type=Path, default=Path("spike_results.json"),
                    help="Cascade output from main.py (default: spike_results.json)")
    p.add_argument("--gemini-results", type=Path, default=None,
                    help="Legacy one-shot Gemini result file; overrides --gemini-one-shot")
    p.add_argument("--gemini-one-shot", type=Path, default=Path("gemini_one_shot_results.json"),
                    help="Gemini one-shot result file")
    p.add_argument("--gemini-blind", type=Path, default=Path("gemini_blind_results.json"),
                    help="Gemini blind-iteration result file")
    p.add_argument("--gemini-context", type=Path, default=Path("gemini_context_results.json"),
                    help="Gemini context-iteration result file")
    p.add_argument("--open-ended-problems", type=Path, default=None,
                    help="Optional separate problem set containing open-ended questions only.")
    p.add_argument("--open-ended-cascade-results", type=Path, default=Path("open_ended/cascade_answers.json"),
                    help="Cascade answer file for open-ended problems (default: open_ended/cascade_answers.json)")
    p.add_argument("--open-ended-gemini-results", type=Path, default=Path("open_ended/gemini_answers.json"),
                    help="Gemini answer file for open-ended problems (default: open_ended/gemini_answers.json)")
    p.add_argument("--open-ended-out-dir", type=Path, default=Path("open_ended/report"),
                    help="Output directory for the separate open-ended judge report.")
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

    if args.open_ended_problems is not None:
        problems = load_json(args.open_ended_problems)
        problems_by_id = index_problems(problems)
        cascade_data = load_json(args.open_ended_cascade_results)
        cascade_by_id = index_cascade_results(cascade_data)
        cascade_total_tokens = 0
        gemini_paths = {"judge_only": args.open_ended_gemini_results}
        out_dir = args.open_ended_out_dir
    else:
        problems = load_json(args.problems)
        problems_by_id = index_problems(problems)
        cascade_data = load_json(args.cascade_results)
        cascade_by_id = index_cascade_results(cascade_data)
        cascade_total_tokens = cascade_data.get("total_tokens", 0)
        gemini_paths = {
            "one_shot": args.gemini_results or args.gemini_one_shot,
            "blind_iter": args.gemini_blind,
            "context_iter": args.gemini_context,
        }
        out_dir = args.out_dir

    gemini_by_condition = {}
    gemini_totals = {}
    for condition, path in gemini_paths.items():
        if path.exists():
            data = load_json(path)
            gemini_by_condition[condition] = index_gemini_results(data)
            gemini_totals[condition] = data.get("total_tokens", 0) if isinstance(data, dict) else sum(
                gemini_tokens(result) or 0 for result in gemini_by_condition[condition].values()
            )
        else:
            gemini_by_condition[condition] = {}
            gemini_totals[condition] = 0
            print(f"  [warning] Gemini {condition} file not found: {path}", file=sys.stderr)

    rows = []
    for problem_id, problem in problems_by_id.items():
        cascade_result = cascade_by_id.get(problem_id)
        gemini_results = {
            condition: results.get(problem_id)
            for condition, results in gemini_by_condition.items()
        }
        try:
            row = compare_problem(problem, cascade_result, gemini_results, args.judge_model, rng)
        except Exception as e:
            row = {"id": problem_id, "type": "error", "error": str(e)}
            print(f"  [error] {problem_id}: {e}", file=sys.stderr)
        rows.append(row)
        if row["type"] == "code":
            statuses = ", ".join(
                f"{condition}={row.get(f'{condition}_passed', '?')}"
                for condition in gemini_paths
            )
            status = f"cascade={row.get('cascade_passed', '?')}, {statuses}"
        elif row["type"] == "open_ended" and "cascade_score" in row:
            status = f"cascade {row['cascade_score']}/5 vs gemini {row['gemini_score']}/5"
        else:
            status = row.get("error", "skipped")
        print(f"  scored {problem_id} ({row['type']}) -> {status}")

    out_dir.mkdir(parents=True, exist_ok=True)

    cascade_token_ledger = cascade_data.get("token_ledger", {}) if isinstance(cascade_data, dict) else {}
    report_md = build_report(rows, cascade_total_tokens, cascade_token_ledger, gemini_totals)
    (out_dir / "comparison_report.md").write_text(report_md)

    (out_dir / "comparison_results.json").write_text(
        json.dumps({"rows": rows, "judge_model": args.judge_model, "seed": args.seed}, indent=2, default=str)
    )

    judged_rows = [r for r in rows if "cascade_score" in r]
    if judged_rows and args.audit_sample > 0:
        k = max(1, round(len(judged_rows) * args.audit_sample))
        audit_ids = [r["id"] for r in rng.sample(judged_rows, k)]
        (out_dir / "audit_sample.json").write_text(
            json.dumps({"instructions": "Manually review these judged items and record "
                        "whether you agree with the judge's winner. Report the agreement "
                        "rate in your README.", "ids": audit_ids}, indent=2)
        )
        print(f"\nAudit sample written: {k}/{len(judged_rows)} judged items "
              f"flagged in {out_dir / 'audit_sample.json'}")

    print(f"\nReport written to {out_dir / 'comparison_report.md'}")
    print(f"Raw results written to {out_dir / 'comparison_results.json'}")


if __name__ == "__main__":
    main()