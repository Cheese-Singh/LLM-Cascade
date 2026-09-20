"""
Gemini baseline conditions — designed to be matched, apples-to-apples,
against the cascade in main.py. This is NOT built to make the cascade win;
every rule here (stopping condition, AGREE-must-pass guard, N-matching) is
applied identically to Gemini and to the cascade, so a Gemini win is just as
valid a result as a cascade win. Whichever way it lands is the finding.

Three conditions get produced by this file, when run after main.py has
already produced spike_results.json for a set of problems:

  1. gemini_one_shot   — single call, no iteration. Existing baseline; not
                          reproduced here, assumed already available via
                          your existing gemini_token_test.py-style script.
                          (If you don't have that yet, see
                          `run_one_shot()` below — it's included so this
                          file can stand alone.)

  2. gemini_blind_iter — Gemini reconsiders its OWN previous answer N times,
                          with NO test-execution feedback. It only sees its
                          prior answer and is asked if it wants to change
                          it. N = the cascade's actual n_hops_used for that
                          specific problem (matched cost per instance, not
                          a flat cap). Tests: does raw self-reconsideration
                          help, with no ground truth to react to.

  3. gemini_context_iter — Same N-matching, but Gemini is told whether its
                          previous answer PASSED or FAILED its test suite,
                          and the exact error if it failed — identical
                          information to what the cascade's review_prompt
                          gives each hop. Gemini must output a VERDICT
                          (AGREE/EDIT/REWRITE), and an AGREE is only
                          honored if the code actually passes (same guard
                          as run_cascade's AGREE-override-to-EDIT check).
                          This is the closest apples-to-apples control for
                          "does model diversity add anything beyond what
                          iteration + ground-truth feedback already gives."

All three write to the same JSON shape so compare_gemini_conditions.py
(or your existing compare script) can consume them uniformly.

Usage:
    pip install google-genai --break-system-packages

    export GEMINI_API_KEY=your_key_here

    # requires spike_results.json from main.py to already exist, so N can
    # be read per-problem
    python gemini_conditions.py \\
        --problems-file all_questions.json \\
        --cascade-results spike_results.json \\
        --condition blind \\
        --out gemini_blind_results.json

    python gemini_conditions.py \\
        --problems-file all_questions.json \\
        --cascade-results spike_results.json \\
        --condition context \\
        --out gemini_context_results.json

    python gemini_conditions.py \\
        --problems-file all_questions.json \\
        --condition one_shot \\
        --out gemini_one_shot_results.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

GEMINI_MODEL = "gemini-3.6-flash"
EXEC_TIMEOUT_S = 5
MAX_ITER_HARD_CAP = 10  # safety ceiling, independent of cascade N — see run_iteration()

VALID_VERDICTS = {"AGREE", "EDIT", "REWRITE"}

# ---------------------------------------------------------------------------
# Token accounting — mirrors main.py's TokenLedger shape so downstream
# comparison tooling can read either file the same way.
# ---------------------------------------------------------------------------

@dataclass
class TokenLedger:
    per_problem: dict = field(default_factory=dict)
    _current_problem_id: str | None = None

    def set_problem(self, problem_id: str):
        self._current_problem_id = problem_id
        self.per_problem.setdefault(problem_id, {"prompt": 0, "completion": 0, "calls": 0})

    def add(self, prompt_tokens: int, completion_tokens: int):
        if self._current_problem_id is None:
            raise RuntimeError("TokenLedger.add() called before set_problem()")
        p = self.per_problem[self._current_problem_id]
        p["prompt"] += prompt_tokens
        p["completion"] += completion_tokens
        p["calls"] += 1

    def tokens_for_problem(self, problem_id: str) -> int:
        p = self.per_problem.get(problem_id)
        return (p["prompt"] + p["completion"]) if p else 0

    def total_tokens(self) -> int:
        return sum(v["prompt"] + v["completion"] for v in self.per_problem.values())


LEDGER = TokenLedger()

# ---------------------------------------------------------------------------
# Gemini call wrapper
# ---------------------------------------------------------------------------

def get_client():
    if genai is None:
        raise RuntimeError(
            "google-genai is not installed. Run: pip install google-genai --break-system-packages"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Get a key from https://aistudio.google.com/apikey and "
            "export GEMINI_API_KEY=your_key_here"
        )
    return genai.Client(api_key=api_key)


def call_gemini(client, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    """
    Calls Gemini and logs token usage to the global LEDGER (caller must have
    already set the current problem via LEDGER.set_problem()).
    """
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
            ),
        )
    except Exception as e:
        raise RuntimeError(f"Gemini call failed: {e}") from e

    content = response.text or ""

    # usage_metadata field names per the google-genai response schema
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    LEDGER.add(prompt_tokens or 0, completion_tokens or 0)

    return content


# ---------------------------------------------------------------------------
# Code + verdict extraction — identical logic to main.py, duplicated here
# so this file has no import dependency on main.py (keeps the two scripts
# independently runnable). If you'd rather share one copy, move these into
# a shared module and import from both.
# ---------------------------------------------------------------------------

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


def extract_verdict(text: str) -> str | None:
    match = re.search(r"VERDICT\s*:?\s*\**\s*(AGREE|EDIT|REWRITE)\**", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


class TimeoutException(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutException()


def run_check(problem: dict, candidate_code: str) -> tuple[bool, str]:
    if not candidate_code:
        return False, "No code extracted from response"
    full_source = candidate_code + "\n\n" + problem["check"] + f"\n\ncheck({problem['entry_point']})\n"
    namespace = {}
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


# ---------------------------------------------------------------------------
# Prompts — deliberately mirrored against main.py's FIRST_HOP_SYSTEM /
# REVIEW_SYSTEM so the "same information, only model-diversity differs"
# comparison actually holds. If you edit main.py's prompts, mirror the
# edit here too, or the comparison stops being matched.
# ---------------------------------------------------------------------------

ONE_SHOT_SYSTEM = (
    "You are an expert Python programmer. Complete the given function. "
    "Return ONLY the complete function implementation in a single Python "
    "code block (```python ... ```). Do not include test code, example "
    "usage, print statements, or explanations — code block only.\n\n"
    "CRITICAL: the function must be named EXACTLY as given in the prompt. "
    "Do not rename it, even if you think a different name is clearer."
)

# Blind iteration: Gemini sees its own prior answer, NO test feedback.
# Deliberately does not mention pass/fail at all.
BLIND_ITER_SYSTEM = (
    "You are an expert Python programmer. You previously answered this "
    "problem. Reconsider your previous answer — if you believe it is "
    "correct, return it unchanged. If you see a way to improve it, return "
    "an improved version.\n\n"
    "Return ONLY the complete function implementation in a single Python "
    "code block (```python ... ```). Do not include test code or "
    "explanations.\n\n"
    "CRITICAL: the function must be named EXACTLY as given in the prompt."
)

# Context iteration: identical shape/rules to main.py's REVIEW_SYSTEM,
# including the same AGREE-must-actually-pass rule stated in the prompt
# (and separately enforced in code as a guard, matching run_cascade).
CONTEXT_ITER_SYSTEM = (
    "You are an expert Python programmer reviewing your OWN previous "
    "answer to this problem. You are told whether your previous answer "
    "currently passes or fails its test suite, and the exact error if it "
    "fails. Use that result as ground truth alongside your own reading of "
    "the code — do not contradict a reported test failure.\n\n"
    "You must respond in exactly this format:\n\n"
    "VERDICT: <AGREE|EDIT|REWRITE>\n\n"
    "```python\n<the function implementation you want to carry forward>\n```\n\n"
    "Rules for choosing the verdict:\n"
    "- AGREE: only valid if your previous answer currently PASSES its test "
    "suite and you see no remaining correctness issues. Return it "
    "UNCHANGED in the code block anyway. Never AGREE with an answer that "
    "is reported as failing.\n"
    "- EDIT: your previous answer is close but has a bug or edge-case "
    "issue, or it is reported as failing. Return a corrected version.\n"
    "- REWRITE: your previous approach is wrong or a substantially better "
    "solution exists. Discard it and write a new implementation from "
    "scratch.\n\n"
    "The function in your returned code block must be named EXACTLY as in "
    "the original problem spec.\n\n"
    "Always include the VERDICT line first, then the code block. Do not "
    "add any other text."
)


def one_shot_prompt(problem: dict) -> str:
    return (
        f"Complete this function. It must be named exactly "
        f"'{problem['entry_point']}':\n\n```python\n{problem['prompt']}```"
    )


def blind_iter_prompt(problem: dict, previous_code: str) -> str:
    return (
        f"Original problem spec (function must be named exactly "
        f"'{problem['entry_point']}'):\n\n```python\n{problem['prompt']}```\n\n"
        f"Your previous answer:\n\n```python\n{previous_code}\n```\n\n"
        f"Reconsider it."
    )


def context_iter_prompt(problem: dict, previous_code: str, passed: bool, error: str) -> str:
    check_status = (
        "Your previous answer CURRENTLY PASSES its test suite."
        if passed
        else f"Your previous answer CURRENTLY FAILS its test suite. Error: {error}"
    )
    return (
        f"Original problem spec (function must be named exactly "
        f"'{problem['entry_point']}'):\n\n```python\n{problem['prompt']}```\n\n"
        f"Your previous answer:\n\n```python\n{previous_code}\n```\n\n"
        f"{check_status}\n\n"
        f"Review it and respond in the required VERDICT + code format."
    )


# ---------------------------------------------------------------------------
# Condition runners
# ---------------------------------------------------------------------------

def run_one_shot(client, problem: dict) -> dict:
    """Single call, no iteration. The baseline all comparisons sit against."""
    raw = call_gemini(client, ONE_SHOT_SYSTEM, one_shot_prompt(problem))
    code = extract_code(raw, problem["entry_point"])
    passed, err = run_check(problem, code)
    return {
        "condition": "one_shot",
        "final_code": code,
        "final_passed": passed,
        "final_error": err,
        "n_iterations_used": 1,
        "iterations": [{"iter": 0, "code": code, "passed": passed, "error": err, "raw_response": raw}],
    }


def run_blind_iteration(client, problem: dict, n_iterations: int) -> dict:
    """
    Gemini reconsiders its own answer n_iterations times total (including
    the first draft), with NO test-execution feedback at any point. Unlike
    the cascade/context-iteration, there's no AGREE verdict to key off of —
    there's no ground truth signal for Gemini to react to, so this
    condition always runs the full n_iterations (matching cascade cost by
    call-count, since there's no principled earlier stopping point without
    feedback to stop on).
    """
    n_iterations = max(1, min(n_iterations, MAX_ITER_HARD_CAP))
    iterations = []
    current_code = None

    for i in range(n_iterations):
        if i == 0:
            raw = call_gemini(client, ONE_SHOT_SYSTEM, one_shot_prompt(problem))
        else:
            raw = call_gemini(client, BLIND_ITER_SYSTEM, blind_iter_prompt(problem, current_code))
        code = extract_code(raw, problem["entry_point"])
        passed, err = run_check(problem, code)
        iterations.append({"iter": i, "code": code, "passed": passed, "error": err, "raw_response": raw})
        current_code = code

    return {
        "condition": "blind_iter",
        "final_code": current_code,
        "final_passed": iterations[-1]["passed"],
        "final_error": iterations[-1]["error"],
        "n_iterations_used": len(iterations),
        "iterations": iterations,
    }


def run_context_iteration(
    client, problem: dict, n_iterations: int, condition_name: str = "context_iter"
) -> dict:
    """
    Same N-matching as blind iteration, but Gemini gets test-pass/fail
    feedback each round and must output a verdict, mirroring
    main.py's run_cascade exactly:
      - AGREE only honored if the code actually passes (same override-to-
        EDIT guard as run_cascade)
      - stops early on a genuine AGREE, same as the cascade's early exit
      - otherwise runs up to n_iterations, same cap as blind iteration
    This is the primary "fair" control your senior's point calls for.
    """
    n_iterations = max(1, min(n_iterations, MAX_ITER_HARD_CAP))
    iterations = []
    current_code = None
    stop_reason = None

    for i in range(n_iterations):
        if i == 0:
            raw = call_gemini(client, ONE_SHOT_SYSTEM, one_shot_prompt(problem))
            code = extract_code(raw, problem["entry_point"])
            verdict = None
            passed, err = run_check(problem, code)
        else:
            prior_passed, prior_err = run_check(problem, current_code)
            raw = call_gemini(
                client, CONTEXT_ITER_SYSTEM,
                context_iter_prompt(problem, current_code, prior_passed, prior_err),
            )
            verdict = extract_verdict(raw)
            extracted = extract_code(raw, problem["entry_point"])

            if verdict is None:
                print(f"     [warning] iter {i} gave no parseable VERDICT line, "
                      f"treating as EDIT and continuing")
                verdict = "EDIT"

            code = extracted if extracted else current_code
            passed, err = run_check(problem, code)

            if verdict == "AGREE" and not passed:
                print(f"     [override] iter {i} said AGREE but code fails its "
                      f"test suite ({err}), forcing EDIT and continuing")
                verdict = "EDIT"

        iterations.append({
            "iter": i, "verdict": verdict, "code": code,
            "passed": passed, "error": err, "raw_response": raw,
        })
        current_code = code

        if verdict == "AGREE":
            stop_reason = f"AGREE at iter {i}"
            break
    else:
        stop_reason = f"reached n_iterations cap ({n_iterations})"

    return {
        "condition": condition_name,
        "final_code": current_code,
        "final_passed": iterations[-1]["passed"],
        "final_error": iterations[-1]["error"],
        "n_iterations_used": len(iterations),
        "stop_reason": stop_reason,
        "iterations": iterations,
    }


# ---------------------------------------------------------------------------
# N-matching: read the cascade's actual per-problem hop count
# ---------------------------------------------------------------------------

def load_cascade_hop_counts(cascade_results_path: Path) -> dict[str, int]:
    """
    Reads spike_results.json (main.py's output) and returns
    {problem_id: n_hops_used}, so Gemini's iteration count can be matched
    per-instance rather than using one flat N for every problem.
    """
    data = json.loads(cascade_results_path.read_text())
    results = data.get("results", data if isinstance(data, list) else [])
    counts = {}
    for r in results:
        if "n_hops_used" in r:
            counts[r["id"]] = r["n_hops_used"]
    return counts


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--problems-file", type=Path, required=True)
    p.add_argument("--cascade-results", type=Path, default=None,
                    help="spike_results.json from main.py — required for "
                         "--condition blind/context to read per-problem N. "
                         "Not needed for --condition one_shot.")
    p.add_argument(
        "--condition",
        choices=["one_shot", "blind", "context"],
        required=True,
        help="context uses matched N",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", type=Path, required=True)
    return p.parse_args()


def main():
    args = parse_args()

    problems = json.loads(args.problems_file.read_text())
    if args.limit:
        problems = problems[: args.limit]

    hop_counts = {}
    if args.condition in ("blind", "context"):
        if args.cascade_results is None:
            print("Error: --cascade-results is required for --condition blind/context "
                  "(needed to read per-problem N to match).", file=sys.stderr)
            sys.exit(1)
        hop_counts = load_cascade_hop_counts(args.cascade_results)

    client = get_client()

    all_results = []
    for problem in problems:
        pid = problem["id"]
        LEDGER.set_problem(pid)
        print(f"{'='*70}\nProblem: {pid}  (condition={args.condition})\n{'='*70}")

        try:
            if args.condition == "one_shot":
                result = run_one_shot(client, problem)
            elif args.condition == "blind":
                n = hop_counts.get(pid)
                if n is None:
                    print(f"  [skip] no cascade hop count found for '{pid}' in "
                          f"{args.cascade_results}, skipping")
                    continue
                print(f"  matching cascade's {n} hop(s) for this problem")
                result = run_blind_iteration(client, problem, n)
            elif args.condition == "context":
                n = hop_counts.get(pid)
                if n is None:
                    print(f"  [skip] no cascade hop count found for '{pid}' in "
                          f"{args.cascade_results}, skipping")
                    continue
                print(f"  matching cascade's {n} hop(s) for this problem")
                result = run_context_iteration(client, problem, n)
            result["tokens"] = LEDGER.tokens_for_problem(pid)
            all_results.append({"id": pid, **result})

            print(f"  n_iterations_used: {result['n_iterations_used']}")
            print(f"  FINAL: {'PASS' if result['final_passed'] else 'FAIL'}")
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            all_results.append({"id": pid, "error": str(e), "final_passed": False})
        print()

    passed = sum(1 for r in all_results if r.get("final_passed"))
    n = len(all_results) or 1
    print(f"{'='*70}\nSUMMARY ({args.condition})\n{'='*70}")
    print(f"  pass@1: {passed}/{n} ({passed/n:.2%})")
    print(f"  total tokens: {LEDGER.total_tokens()}")

    args.out.write_text(json.dumps({
        "condition": args.condition,
        "results": all_results,
        "total_tokens": LEDGER.total_tokens(),
    }, indent=2, default=str))
    print(f"\nResults written to {args.out}")


if __name__ == "__main__":
    main()