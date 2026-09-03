import argparse
import json
import os
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

GEMINI_MODEL = "gemini-3.6-flash"


def get_client():
    if genai is None:
        raise RuntimeError(
            "google-genai is not installed. Run: pip install google-genai --break-system-packages"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Export it before running this script."
        )
    return genai.Client(api_key=api_key)


def build_prompt(question_text: str) -> str:
    return (
        f"Question:\n{question_text}\n\n"
        "Answer clearly, directly, and completely. "
        "Do not use code blocks unless the user explicitly asks for code."
    )


def get_answer(client, question_text: str) -> tuple[str, dict]:
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_prompt(question_text),
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are a helpful assistant. Answer the user's question clearly, "
                "directly, and completely."
            ),
            temperature=0.2,
        ),
    )

    content = response.text or ""
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    return content.strip(), {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"Questions file '{path}' must contain a non-empty JSON list.")
    return data


def main():
    parser = argparse.ArgumentParser(description="Generate Gemini baseline answers for open-ended questions.")
    parser.add_argument("--questions-file", type=Path, default=Path("open_ended/questions.json"))
    parser.add_argument("--output", type=Path, default=Path("open_ended/gemini_answers.json"))
    args = parser.parse_args()

    questions = load_questions(args.questions_file)
    client = get_client()

    results = []
    for i, question in enumerate(questions, start=1):
        qid = question["id"]
        print(f"[{i}/{len(questions)}] Gemini answering: {qid}")
        answer, usage = get_answer(client, question["prompt"])
        results.append({
            "id": qid,
            "final_answer": answer,
            "usage": usage,
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(results)} Gemini answers to {args.output}")


if __name__ == "__main__":
    main()
