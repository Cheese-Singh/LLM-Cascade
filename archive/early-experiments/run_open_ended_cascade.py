import argparse
import json
import re
import sys
from pathlib import Path

from main import CHAIN_ORDER, LEDGER, call_ollama

OPEN_ENDED_SYSTEM = (
    "You are a helpful assistant. Answer the user's question clearly, "
    "directly, and completely. Do not output code blocks unless asked."
)

OPEN_ENDED_REVIEW_SYSTEM = (
    "You are reviewing a draft answer written by a weaker model. "
    "Decide whether it is already good enough (AGREE), needs minor edits (EDIT), "
    "or needs a full rewrite (REWRITE).\n\n"
    "Return EXACTLY this format, with no extra text before or after it:\n\n"
    "VERDICT: <AGREE|EDIT|REWRITE>\n\n"
    "ANSWER:\n<the final answer you want to keep or improve>"
)


def open_ended_prompt(question_text: str) -> str:
    return (
        f"Question:\n{question_text}\n\n"
        "Provide the final answer to this question. "
        "Keep it polished, concise, and complete."
    )


def review_open_ended_prompt(original_question: str, draft_answer: str) -> str:
    return (
        f"Original question:\n{original_question}\n\n"
        f"Draft answer to review:\n{draft_answer}\n\n"
        "Evaluate whether this answer is complete, accurate, and well-formed. "
        "If it is already strong enough, return AGREE. If it is close but weak in spots, "
        "return EDIT and provide a corrected answer. If the draft is poor or off-track, "
        "return REWRITE and write a better answer from scratch."
    )


def parse_verdict_and_answer(text: str) -> tuple[str | None, str | None]:
    verdict_match = re.search(r"VERDICT\s*:\s*(AGREE|EDIT|REWRITE)", text, flags=re.IGNORECASE)
    if verdict_match is None:
        return None, None

    verdict = verdict_match.group(1).upper()
    answer_match = re.search(r"ANSWER\s*:\s*(.*)", text, flags=re.DOTALL | re.IGNORECASE)
    if answer_match:
        answer = answer_match.group(1).strip()
    else:
        answer = text.split("VERDICT:", 1)[1].strip()
        answer = re.sub(r"(?i)^\s*(AGREE|EDIT|REWRITE)\s*", "", answer, count=1)
        answer = answer.strip()

    return verdict, answer or None


def run_open_ended_question(question: dict, chain: list[str]) -> dict:
    LEDGER.set_problem(question["id"])
    hops = []
    current_answer = question["prompt"]
    stop_reason = None

    print(f"{'='*70}\nQuestion: {question['id']}\n{'='*70}")
    for i, model_key in enumerate(chain):
        if i == 0:
            raw, usage = call_ollama(
                model_key,
                OPEN_ENDED_SYSTEM,
                open_ended_prompt(current_answer),
                temperature=0.2,
            )
            current_answer = raw.strip()
            hops.append({
                "hop": i,
                "model": model_key,
                "usage": usage,
                "verdict": None,
                "answer": current_answer,
            })
            print(f"  hop {i} ({model_key}): draft generated | tokens={usage['total_tokens']}")
        else:
            raw, usage = call_ollama(
                model_key,
                OPEN_ENDED_REVIEW_SYSTEM,
                review_open_ended_prompt(question["prompt"], current_answer),
                temperature=0.2,
            )
            verdict, revised_answer = parse_verdict_and_answer(raw)
            if verdict is None:
                print(f"     [warning] hop {i} ({model_key}) gave no parseable VERDICT, treating as EDIT")
                verdict = "EDIT"
                revised_answer = current_answer

            current_answer = revised_answer.strip() if revised_answer else current_answer
            hops.append({
                "hop": i,
                "model": model_key,
                "usage": usage,
                "verdict": verdict,
                "answer": current_answer,
            })
            print(f"  hop {i} ({model_key}) verdict={verdict} | tokens={usage['total_tokens']}")

            if verdict == "AGREE":
                stop_reason = f"AGREE at hop {i} ({model_key})"
                break

        if i == len(chain) - 1:
            stop_reason = f"reached strongest model ({chain[-1]}), chain exhausted"

    result = {
        "id": question["id"],
        "hops": hops,
        "final_answer": current_answer,
        "stop_reason": stop_reason,
        "n_hops_used": len(hops),
        "tokens_by_model": LEDGER.model_tokens_for_problem(question["id"]),
        "tokens": LEDGER.tokens_for_problem(question["id"]),
    }

    print("  Tokens by model:")
    for model, tokens in result["tokens_by_model"].items():
        print(f"    {model} | {tokens}")
    print(f"  Hops used: {result['n_hops_used']}/{len(chain)}")
    print(f"  STOPPED: {stop_reason}")
    print(f"  TOTAL TOKENS: {result['tokens']}")
    print(f"\n  --- Final answer ---\n{result['final_answer']}\n")
    return result


def parse_args():
    p = argparse.ArgumentParser(
        description="Open-ended cascade answer generation with AGREE/EDIT/REWRITE stopping logic similar to main.py.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--questions-file", type=Path, default=Path("open_ended/questions.json"),
                   help="JSON file of open-ended questions to load.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only run the first N questions from the file.")
    p.add_argument("--single-model", action="store_true",
                   help="Run only the first model in the cascade chain for a quick smoke test.")
    p.add_argument("--output", type=Path, default=Path("open_ended/cascade_answers.json"),
                   help="Output path for the cascade answer file.")
    return p.parse_args()


def load_questions(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"Questions file '{path}' must contain a non-empty JSON list.")
    if limit is not None:
        data = data[:limit]
    return data


def main():
    args = parse_args()
    try:
        questions = load_questions(args.questions_file, limit=args.limit)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading questions: {e}", file=sys.stderr)
        sys.exit(1)

    chain = [CHAIN_ORDER[0]] if args.single_model else CHAIN_ORDER
    print(f"Loaded {len(questions)} question(s): {[q['id'] for q in questions]}")
    print(f"Using chain: {chain}")
    print()

    all_results = []
    for question_number, question in enumerate(questions, start=1):
        print(f"Processing question {question_number}/{len(questions)}: {question['id']}")
        result = run_open_ended_question(question, chain)
        all_results.append({"id": question["id"], **result})

    print(f"{'='*70}\nSUMMARY\n{'='*70}")
    print(LEDGER.report())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "results": all_results,
            "token_ledger": LEDGER.per_model,
            "total_tokens": LEDGER.total_tokens(),
        }, f, indent=2, default=str)

    print(f"\nFull results written to {args.output}")


if __name__ == "__main__":
    main()