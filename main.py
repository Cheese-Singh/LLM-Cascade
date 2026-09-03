import argparse
import ast
import json
import re
import signal
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import ollama

CLIENT = ollama.Client()

MODELS = {
    "gemma": "gemma4:cloud",
    "nemotron": "nemotron-3-super:cloud",
    "gptoss": "gpt-oss:20b-cloud",
    "nemotron-ultra": "nemotron-3-ultra:cloud",
}

CHAIN_ORDER = ["gptoss", "gemma", "nemotron", "nemotron-ultra"]

EXEC_TIMEOUT_S = 5
DEFAULT_PROBLEMS_FILE = Path(__file__).parent / "all_questions.json"

VALID_VERDICTS = {"AGREE", "EDIT", "REWRITE"}


@dataclass
class TokenLedger:
    per_model: dict = field(default_factory=dict)
    per_problem: dict = field(default_factory=dict)  
    _current_problem_id: str | None = None            

    def set_problem(self, problem_id: str):            
        self._current_problem_id = problem_id
        self.per_problem.setdefault(problem_id, {
            "prompt": 0, "completion": 0, "calls": 0, "by_model": {}
        })

    def add(self, model_key: str, prompt_tokens: int, completion_tokens: int):
        if model_key not in self.per_model:
            self.per_model[model_key] = {"prompt": 0, "completion": 0, "calls": 0}
        self.per_model[model_key]["prompt"] += prompt_tokens
        self.per_model[model_key]["completion"] += completion_tokens
        self.per_model[model_key]["calls"] += 1

        if self._current_problem_id is not None:        
            p = self.per_problem[self._current_problem_id]
            p["prompt"] += prompt_tokens
            p["completion"] += completion_tokens
            p["calls"] += 1
            model_counts = p["by_model"].setdefault(
                model_key, {"prompt": 0, "completion": 0, "calls": 0}
            )
            model_counts["prompt"] += prompt_tokens
            model_counts["completion"] += completion_tokens
            model_counts["calls"] += 1

    def total_tokens(self) -> int:
        return sum(v["prompt"] + v["completion"] for v in self.per_model.values())

    def tokens_for_problem(self, problem_id: str) -> int:  
        p = self.per_problem.get(problem_id)
        return (p["prompt"] + p["completion"]) if p else 0

    def model_tokens_for_problem(self, problem_id: str) -> dict:
        problem = self.per_problem.get(problem_id, {})
        return {
            model: counts["prompt"] + counts["completion"]
            for model, counts in problem.get("by_model", {}).items()
        }

    def report(self) -> str:
        lines = ["\n=== TOKEN USAGE ==="]
        grand_total = 0
        for model, counts in self.per_model.items():
            subtotal = counts["prompt"] + counts["completion"]
            grand_total += subtotal
            lines.append(
                f"  {model:12s}  calls={counts['calls']:3d}  "
                f"prompt={counts['prompt']:6d}  completion={counts['completion']:6d}  "
                f"total={subtotal:6d}"
            )
        lines.append(f"  {'-'*60}")
        lines.append(f"  {'GRAND TOTAL':12s}  {'':>4s}  {'':>13s}  {'':>17s}  total={grand_total:6d}")
        return "\n".join(lines)


LEDGER = TokenLedger()


def load_problems_from_file(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Problems file not found: {path}\n"
            f"Either create it or pass --custom-prompt/--custom-check for a single inline problem."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Problems file '{path}' is not valid JSON: {e}") from e
    if not isinstance(data, list) or not data:
        raise ValueError(f"Problems file '{path}' must contain a non-empty JSON list.")
    required_keys = {"id", "prompt", "check", "entry_point"}
    for i, problem in enumerate(data):
        missing = required_keys - set(problem.keys())
        if missing:
            raise ValueError(f"Problem at index {i} in '{path}' is missing required keys: {missing}")
    if limit is not None:
        data = data[:limit]
    return data


def build_custom_problem(prompt_text: str, check_text: str, entry_point: str) -> dict:
    if f"def {entry_point}" not in prompt_text:
        print(
            f"Warning: entry_point '{entry_point}' not found as 'def {entry_point}' "
            f"in the supplied prompt text.",
            file=sys.stderr,
        )
    if "def check(" not in check_text:
        raise ValueError(
            "Custom check text must define a function named exactly 'check(candidate)'."
        )
    return {"id": "custom", "prompt": prompt_text, "check": check_text, "entry_point": entry_point}


def call_ollama(model_key, system_prompt, user_prompt, temperature=0.2):
    model_name = MODELS.get(model_key, model_key)
    try:
        response = CLIENT.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": temperature},
        )
    except Exception as e:
        raise RuntimeError(
            f"Ollama call failed for model '{model_name}' (key='{model_key}'): {e}\n"
            f"If cloud: check `ollama signin` and current free-tier access. "
            f"If local: check `ollama pull {model_name}`."
        ) from e

    content = response.message.content or ""
    prompt_tokens = response.prompt_eval_count or 0
    completion_tokens = response.eval_count or 0
    LEDGER.add(model_key, prompt_tokens, completion_tokens)
    return content, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
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


FIRST_HOP_SYSTEM = (
    "You are an expert Python programmer. Complete the given function. "
    "Return ONLY the complete function implementation in a single Python "
    "code block (```python ... ```). Do not include test code, example "
    "usage, print statements, or explanations — code block only.\n\n"
    "CRITICAL: the function must be named EXACTLY as given in the prompt. "
    "Do not rename it, even if you think a different name is clearer."
)

REVIEW_SYSTEM = (
    "You are an expert Python programmer reviewing a draft implementation "
    "written by another, weaker model. You are told whether the draft "
    "currently passes or fails its test suite, and the exact error if it "
    "fails. Use that result as ground truth alongside your own reading of "
    "the code — do not contradict a reported test failure.\n\n"
    "You must respond in exactly this format:\n\n"
    "VERDICT: <AGREE|EDIT|REWRITE>\n\n"
    "```python\n<the function implementation you want to carry forward>\n```\n\n"
    "Rules for choosing the verdict:\n"
    "- AGREE: only valid if the draft currently PASSES its test suite and "
    "you see no remaining correctness issues. Return it UNCHANGED in the "
    "code block anyway (so the code is always present in your response). "
    "Never AGREE with a draft that is reported as failing.\n"
    "- EDIT: the draft is close but has a bug or edge-case issue, or it is "
    "reported as failing. Return a corrected version that keeps the "
    "draft's overall approach.\n"
    "- REWRITE: the draft's approach is wrong or a substantially better "
    "solution exists. Discard it and write a new implementation from scratch.\n\n"
    "The function in your returned code block must be named EXACTLY as in "
    "the original problem spec, regardless of what the draft named it.\n\n"
    "Always include the VERDICT line first, then the code block. Do not "
    "add any other text."
)


def first_hop_prompt(problem: dict) -> str:
    return (
        f"Complete this function. It must be named exactly "
        f"'{problem['entry_point']}':\n\n```python\n{problem['prompt']}```"
    )


def review_prompt(problem: dict, previous_code: str, passed: bool, error: str) -> str:
    check_status = (
        "This draft CURRENTLY PASSES its test suite."
        if passed
        else f"This draft CURRENTLY FAILS its test suite. Error: {error}"
    )
    return (
        f"Original problem spec (function must be named exactly "
        f"'{problem['entry_point']}'):\n\n```python\n{problem['prompt']}```\n\n"
        f"Draft implementation from a previous (weaker) model:\n\n"
        f"```python\n{previous_code}\n```\n\n"
        f"{check_status}\n\n"
        f"Review it and respond in the required VERDICT + code format."
    )


def run_cascade(problem: dict, chain: list[str]) -> dict:
    LEDGER.set_problem(problem["id"])
    hops = []
    current_code = None
    stop_reason = None

    for i, model_key in enumerate(chain):
        if i == 0:
            raw, usage = call_ollama(model_key, FIRST_HOP_SYSTEM, first_hop_prompt(problem))
            code = extract_code(raw, problem["entry_point"])
            verdict = None
            passed, err = run_check(problem, code)
        else:
            prior_passed, prior_err = run_check(problem, current_code)
            raw, usage = call_ollama(
                model_key,
                REVIEW_SYSTEM,
                review_prompt(problem, current_code, prior_passed, prior_err),
            )
            verdict = extract_verdict(raw)
            extracted = extract_code(raw, problem["entry_point"])

            if verdict is None:
                print(f"     [warning] hop {i} ({model_key}) gave no parseable "
                      f"VERDICT line, treating as EDIT and continuing")
                verdict = "EDIT"

            code = extracted if extracted else current_code
            passed, err = run_check(problem, code)

            if verdict == "AGREE" and not passed:
                print(f"     [override] hop {i} ({model_key}) said AGREE but code "
                      f"fails its test suite ({err}), forcing EDIT and continuing")
                verdict = "EDIT"

        hops.append({
            "hop": i,
            "model": model_key,
            "usage": usage,
            "verdict": verdict,
            "code": code,
            "passed": passed,
            "error": err,
            "raw_response": raw,
        })

        current_code = code

        if verdict == "AGREE":
            stop_reason = f"AGREE at hop {i} ({model_key})"
            break
    else:
        stop_reason = f"reached strongest model ({chain[-1]}), chain exhausted"

    return {
        "hops": hops,
        "final_code": current_code,
        "final_passed": hops[-1]["passed"],
        "stop_reason": stop_reason,
        "n_hops_used": len(hops),
        "tokens_by_model": LEDGER.model_tokens_for_problem(problem["id"]),
    }


def parse_args():
    p = argparse.ArgumentParser(
        description="Cascade spike: real AGREE/EDIT/REWRITE, check-gated review, always escalates to consensus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--problems-file", type=Path, default=DEFAULT_PROBLEMS_FILE,
                    help="JSON file of problems to load (default: problems.json).")
    p.add_argument("--limit", type=int, default=None,
                    help="Only run the first N problems from the problems file.")
    p.add_argument("--custom-prompt", type=Path, default=None,
                    help="Path to a .py file with ONE function signature+docstring.")
    p.add_argument("--custom-check", type=Path, default=None,
                    help="Path to a .py file defining check(candidate).")
    p.add_argument("--entry-point", type=str, default=None,
                    help="Function name. Required with --custom-prompt.")
    return p.parse_args()


def load_selected_problems(args) -> list[dict]:
    if args.custom_prompt:
        if not args.custom_check or not args.entry_point:
            raise SystemExit("--custom-prompt requires --custom-check and --entry-point.")
        return [build_custom_problem(
            args.custom_prompt.read_text(), args.custom_check.read_text(), args.entry_point
        )]
    return load_problems_from_file(args.problems_file, limit=args.limit)


def main():
    args = parse_args()

    try:
        problems = load_selected_problems(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading problems: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(problems)} problem(s): {[p['id'] for p in problems]}")
    print(f"No max-hops cap: every problem escalates until AGREE or the chain "
          f"is exhausted at {CHAIN_ORDER[-1]}.")
    print("NOTE: this means every run CAN reach minimax (most expensive model). "
          "It's only actually called if every earlier model returns EDIT/REWRITE.")
    print()

    all_results = []

    for question_number, problem in enumerate(problems, start=1):
        print(f"{'='*70}\nQuestion {question_number}: {problem['id']}\n{'='*70}")
        try:
            result = run_cascade(problem, CHAIN_ORDER)
            result["tokens"] = LEDGER.tokens_for_problem(problem["id"])   
            all_results.append({"id": problem["id"], **result})

            for hop in result["hops"]:
                status = "PASS" if hop["passed"] else "FAIL"
                verdict_str = f"[{hop['verdict']}]" if hop["verdict"] else "[origin draft]"
                usage = hop.get("usage", {})
                token_str = f"tokens={usage['total_tokens']}" if usage else "tokens=?"
                print(f"  hop {hop['hop']} ({hop['model']}) {verdict_str}: {status} ({token_str})")

            model_tokens = ", ".join(
                f"{model}={tokens}"
                for model, tokens in result["tokens_by_model"].items()
            )
            print("  Tokens by model:")
            for model, tokens in result["tokens_by_model"].items():
                print(f"    {model} | {tokens}")

            print(f"  STOPPED: {result['stop_reason']}")
            print(f"  Hops used: {result['n_hops_used']}/{len(CHAIN_ORDER)}")
            print(f"  FINAL: {'PASS' if result['final_passed'] else 'FAIL'}")
            print(f"\n  --- Final answer ---\n{result['final_code']}\n")
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            all_results.append({"id": problem["id"], "final_passed": False, "error": str(e)})
        print()

    passed = sum(1 for r in all_results if r.get("final_passed"))
    print(f"{'='*70}\nSUMMARY\n{'='*70}")
    print(f"  pass@1: {passed}/{len(problems)} ({passed/len(problems):.2%})")
    avg_hops = sum(r.get("n_hops_used", 0) for r in all_results) / len(all_results)
    print(f"  avg hops used per problem: {avg_hops:.2f} (max possible: {len(CHAIN_ORDER)})")
    print(LEDGER.report())

    out_path = "spike_results.json"
    with open(out_path, "w") as f:
        json.dump({
            "results": all_results,
            "token_ledger": LEDGER.per_model,
            "total_tokens": LEDGER.total_tokens(),
        }, f, indent=2, default=str)
    print(f"\nFull results (including every hop's raw response) written to {out_path}")


if __name__ == "__main__":
    main()