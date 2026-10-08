from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import ollama


SEMANTIC_MODEL = os.getenv(
    "PHASE3_SEMANTIC_MODEL",
    "qwen3.5:0.8b",
)

SEMANTIC_TIMEOUT = float(
    os.getenv(
        "PHASE3_SEMANTIC_TIMEOUT",
        "300",
    )
)


SEMANTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "equivalent": {
            "type": "boolean",
        },
    },
    "required": [
        "equivalent",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = (
    "You are a strict semantic answer-equivalence classifier.\n\n"
    "Your ONLY task is to compare the GOLD ANSWER and the "
    "CANDIDATE ANSWER for the given QUESTION.\n\n"
    "Do NOT solve the question.\n"
    "Do NOT answer the question yourself.\n"
    "Do NOT use outside knowledge.\n"
    "Do NOT correct, reinterpret, or replace the gold answer.\n"
    "Do NOT invent facts, evidence, explanations, or missing information.\n"
    "Do NOT infer an answer that is not explicitly supported by the "
    "provided text.\n"
    "Do NOT judge whether either answer is independently factually "
    "correct beyond determining whether the two answers express the "
    "same substantive answer.\n\n"
    "Mark equivalent=true ONLY when the candidate conveys the same "
    "substantive meaning as the gold answer for the question.\n"
    "Paraphrasing, grammar changes, wording changes, and sentence "
    "restructuring are allowed when meaning is preserved.\n"
    "Mark equivalent=false when information is changed, missing, "
    "added in a contradictory way, or when the candidate answers "
    "a different aspect of the question.\n"
    "Shared words or topic similarity are NOT sufficient.\n"
    "For multiple gold-answer items, the candidate must preserve "
    "the substantive meaning of every required item.\n\n"
    "Return ONLY the required JSON object."
)


def _clean_model_output(
    text: str,
) -> str:
    text = str(
        text or ""
    ).strip()

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"<thinking>.*?</thinking>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json|text)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        ).strip()

    return text


def _extract_decision(
    text: str,
) -> bool | None:
    cleaned = _clean_model_output(
        text
    )

    if not cleaned:
        return None

    try:
        parsed = json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        return None

    if not isinstance(
        parsed,
        dict,
    ):
        return None

    value = parsed.get(
        "equivalent"
    )

    if isinstance(
        value,
        bool,
    ):
        return value

    return None


def _build_prompt(
    problem: dict[str, Any],
    gold_answer: Any,
    candidate_answer: str,
) -> str:
    question = str(
        problem.get(
            "question",
            "",
        )
    ).strip()

    context = str(
        problem.get(
            "prompt",
            "",
        )
    ).strip()

    if not context:
        context = question

    if isinstance(
        gold_answer,
        list,
    ):
        gold_text = json.dumps(
            gold_answer,
            ensure_ascii=False,
        )
    else:
        gold_text = str(
            gold_answer
        )

    return (
        "QUESTION:\n"
        f"{question}\n\n"
        "CONTEXT:\n"
        f"{context}\n\n"
        "GOLD ANSWER:\n"
        f"{gold_text}\n\n"
        "CANDIDATE ANSWER:\n"
        f"{candidate_answer}\n\n"
        "Determine whether the candidate answer and gold "
        "answer express the same substantive answer to the "
        "question.\n\n"
        "Return only the JSON object required by the system "
        "instructions."
    )


def semantic_verify(
    problem: dict[str, Any],
    gold_answer: Any,
    candidate_answer: str,
) -> dict[str, Any]:
    candidate_text = str(
        candidate_answer
    ).strip()

    if not candidate_text:
        return {
            "passed": False,
            "reason": "empty_candidate_answer",
            "model": SEMANTIC_MODEL,
            "raw_output": "",
        }

    if gold_answer is None:
        return {
            "passed": False,
            "reason": "empty_gold_answer",
            "model": SEMANTIC_MODEL,
            "raw_output": "",
        }

    prompt = _build_prompt(
        problem,
        gold_answer,
        candidate_text,
    )

    start = time.monotonic()

    print(
        "  SEMANTIC VERIFIER",
        flush=True,
    )

    print(
        "  --------------------------------------------------------------------",
        flush=True,
    )

    print(
        f"  Model     : {SEMANTIC_MODEL}",
        flush=True,
    )

    print(
        "  Status    : RUNNING | elapsed=0.0s",
        flush=True,
    )

    print(
        "  --------------------------------------------------------------------",
        flush=True,
    )

    try:
        response_stream = ollama.chat(
            model=SEMANTIC_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            stream=True,
            think=False,
            format=SEMANTIC_SCHEMA,
            options={
                "temperature": 0,
                "num_predict": 20,
            },
        )

        output_parts = []
        last_report = -1.0

        for chunk in response_stream:
            elapsed = (
                time.monotonic()
                - start
            )

            if elapsed >= SEMANTIC_TIMEOUT:
                raise TimeoutError(
                    f"Semantic verifier exceeded "
                    f"{SEMANTIC_TIMEOUT:.1f}s."
                )

            if isinstance(
                chunk,
                dict,
            ):
                message = chunk.get(
                    "message",
                    {},
                )

                content = str(
                    message.get(
                        "content",
                        "",
                    )
                )

                done = bool(
                    chunk.get(
                        "done",
                        False,
                    )
                )
            else:
                message = getattr(
                    chunk,
                    "message",
                    None,
                )

                if message is not None:
                    content = str(
                        getattr(
                            message,
                            "content",
                            "",
                        )
                        or ""
                    )
                else:
                    content = str(
                        getattr(
                            chunk,
                            "content",
                            "",
                        )
                        or ""
                    )

                done = bool(
                    getattr(
                        chunk,
                        "done",
                        False,
                    )
                )

            if content:
                output_parts.append(
                    content
                )

            if (
                elapsed - last_report
                >= 1.0
            ):
                print(
                    f"  Status    : RUNNING | elapsed={elapsed:.1f}s",
                    flush=True,
                )

                last_report = elapsed

            if done:
                break

        raw_output = "".join(
            output_parts
        ).strip()

    except TimeoutError as exc:
        elapsed = (
            time.monotonic()
            - start
        )

        print(
            f"  Status    : TIMEOUT | elapsed={elapsed:.1f}s",
            flush=True,
        )

        print(
            "  --------------------------------------------------------------------",
            flush=True,
        )

        return {
            "passed": False,
            "reason": "semantic_verifier_timeout",
            "model": SEMANTIC_MODEL,
            "error": str(exc),
            "elapsed_seconds": elapsed,
        }

    except Exception as exc:
        elapsed = (
            time.monotonic()
            - start
        )

        print(
            f"  Status    : ERROR | elapsed={elapsed:.1f}s",
            flush=True,
        )

        print(
            "  --------------------------------------------------------------------",
            flush=True,
        )

        return {
            "passed": False,
            "reason": "semantic_verifier_error",
            "model": SEMANTIC_MODEL,
            "error": str(exc),
            "elapsed_seconds": elapsed,
        }

    elapsed = (
        time.monotonic()
        - start
    )

    decision = _extract_decision(
        raw_output
    )

    print(
        f"  Status    : COMPLETE | elapsed={elapsed:.1f}s",
        flush=True,
    )

    if decision is True:
        print(
            "  Decision  : MATCH",
            flush=True,
        )
    elif decision is False:
        print(
            "  Decision  : NO_MATCH",
            flush=True,
        )
    else:
        print(
            "  Decision  : UNPARSEABLE",
            flush=True,
        )

    if raw_output:
        print(
            f"  Raw       : {raw_output}",
            flush=True,
        )

    print(
        "  --------------------------------------------------------------------",
        flush=True,
    )

    if decision is None:
        return {
            "passed": False,
            "reason": "semantic_verifier_unparseable",
            "model": SEMANTIC_MODEL,
            "raw_output": raw_output,
            "elapsed_seconds": elapsed,
        }

    return {
        "passed": decision,
        "reason": (
            "semantic_match"
            if decision
            else "semantic_mismatch"
        ),
        "model": SEMANTIC_MODEL,
        "raw_output": raw_output,
        "elapsed_seconds": elapsed,
    }