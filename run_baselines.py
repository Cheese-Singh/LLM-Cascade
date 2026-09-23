from __future__ import annotations

import re
import time
from typing import Any, Callable

import ollama

from config import MODEL_CONFIG
from sandbox import run_check


CLIENT = ollama.Client()

LAYER_KEYS = (
    "layer_1",
    "layer_2",
    "layer_3",
    "layer_4",
)

MAX_MODEL_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)

TRANSIENT_STATUS_CODES = {
    408,
    409,
    425,
    429,
    500,
    502,
    503,
    504,
}

TRANSIENT_ERROR_MARKERS = (
    "server sent goaway",
    "server_shutting_down",
    "status code: 502",
    "status code: 503",
    "status code: 504",
    "status code: 429",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection closed",
    "temporarily unavailable",
    "temporary failure",
    "timeout",
    "timed out",
    "eof",
)

FIRST_HOP_SYSTEM = (
    "You are an expert Python programmer. Complete the given function. "
    "Return ONLY the complete function implementation in a single Python "
    "code block. Do not include test code, example usage, print statements, "
    "or explanations. The function must be named exactly as specified."
)

ESCALATION_SYSTEM = (
    "You are an expert Python programmer reviewing a solution produced by "
    "another model. You are given the original problem, the candidate "
    "implementation, and the result of executing its test suite. Produce "
    "the corrected complete function implementation. Return ONLY the "
    "complete function implementation in a single Python code block. "
    "Do not include explanations or test code."
)


def model_name(model_key: str) -> str:
    if model_key in MODEL_CONFIG:
        return MODEL_CONFIG[model_key]["model"]
    return model_key


def first_hop_prompt(problem: dict[str, Any]) -> str:
    return (
        f"Complete this function. It must be named exactly "
        f"'{problem['entry_point']}':\n\n"
        f"```python\n{problem['prompt']}\n```"
    )


def escalation_prompt(
    problem: dict[str, Any],
    previous_code: str,
    passed: bool,
    error: str,
) -> str:
    status = (
        "The candidate currently PASSES the test suite."
        if passed
        else f"The candidate currently FAILS the test suite.\nError: {error}"
    )

    return (
        f"Original problem:\n\n"
        f"```python\n{problem['prompt']}\n```\n\n"
        f"Candidate implementation:\n\n"
        f"```python\n{previous_code}\n```\n\n"
        f"{status}\n\n"
        f"Return a corrected implementation of "
        f"'{problem['entry_point']}'."
    )


def _isolate_function(
    block: str,
    entry_point: str,
) -> str:
    import ast

    try:
        tree = ast.parse(block)
    except SyntaxError:
        return block

    function_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    ]

    if not function_nodes:
        return block

    target = next(
        (
            node
            for node in function_nodes
            if node.name == entry_point
        ),
        None,
    )

    if target is None and len(function_nodes) == 1:
        target = function_nodes[0]
        target.name = entry_point

    if target is None:
        return block

    imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    module = ast.Module(
        body=[*imports, target],
        type_ignores=[],
    )

    return ast.unparse(module)


def extract_code(
    text: str,
    entry_point: str,
) -> str:
    if not text:
        return ""

    fenced_blocks = re.findall(
        r"```(?:python)?\s*\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    if fenced_blocks:
        for block in fenced_blocks:
            if f"def {entry_point}" in block:
                return _isolate_function(
                    block,
                    entry_point,
                ).strip()

        for block in fenced_blocks:
            isolated = _isolate_function(
                block,
                entry_point,
            )

            if isolated != block:
                return isolated.strip()

        return fenced_blocks[0].strip()

    function_start = text.find(
        f"def {entry_point}"
    )

    if function_start != -1:
        return _isolate_function(
            text[function_start:],
            entry_point,
        ).strip()

    isolated = _isolate_function(
        text,
        entry_point,
    )

    if isolated != text:
        return isolated.strip()

    return text.strip()


def _is_transient_ollama_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)

    if status_code in TRANSIENT_STATUS_CODES:
        return True

    message = str(exc).lower()

    return any(
        marker in message
        for marker in TRANSIENT_ERROR_MARKERS
    )


def call_model(
    model_key: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    seed: int = 42,
) -> dict[str, Any]:
    model = model_name(model_key)
    start = time.perf_counter()
    last_exception: Exception | None = None
    attempts = 0

    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        attempts = attempt

        try:
            response = CLIENT.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                options={
                    "temperature": temperature,
                    "seed": seed,
                },
            )
            break

        except Exception as exc:
            last_exception = exc

            if not _is_transient_ollama_error(exc):
                raise RuntimeError(
                    f"Ollama call failed for model '{model}' "
                    f"(key='{model_key}'): {exc}"
                ) from exc

            if attempt >= MAX_MODEL_ATTEMPTS:
                raise RuntimeError(
                    f"Ollama call failed after "
                    f"{MAX_MODEL_ATTEMPTS} attempts for model "
                    f"'{model}' (key='{model_key}'): {exc}"
                ) from exc

            delay = RETRY_DELAYS_SECONDS[attempt - 1]

            print(
                f"Ollama transient error for {model} "
                f"(attempt {attempt}/{MAX_MODEL_ATTEMPTS}): {exc}"
            )
            print(f"Retrying in {delay:.0f}s...")
            time.sleep(delay)

    else:
        raise RuntimeError(
            f"Ollama call failed for model '{model}' "
            f"(key='{model_key}'): {last_exception}"
        ) from last_exception

    latency_ms = (
        time.perf_counter() - start
    ) * 1000.0

    content = response.message.content or ""

    prompt_tokens = int(
        response.prompt_eval_count or 0
    )

    completion_tokens = int(
        response.eval_count or 0
    )

    return {
        "model_key": model_key,
        "model": model,
        "response": content,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": (
            prompt_tokens + completion_tokens
        ),
        "latency_ms": latency_ms,
        "attempts": attempts,
        "retries": attempts - 1,
        "seed": seed,
    }


def build_hop(
    hop_index: int,
    result: dict[str, Any],
    code: str,
    check: dict[str, Any],
) -> dict[str, Any]:
    return {
        "hop": hop_index,
        "model": result["model"],
        "model_key": result["model_key"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "total_tokens": result["total_tokens"],
        "latency_ms": result["latency_ms"],
        "attempts": result.get("attempts", 1),
        "retries": result.get("retries", 0),
        "seed": result.get("seed", 42),
        "candidate": code,
        "verification_passed": check["passed"],
        "verification_error": check["error"],
    }


def run_layer(
    problem: dict[str, Any],
    layer_index: int,
    previous_code: str | None,
    previous_check: dict[str, Any] | None,
    temperature: float,
    seed: int,
    status_callback: Callable | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    layer_key = LAYER_KEYS[layer_index]

    if layer_index == 0:
        system_prompt = FIRST_HOP_SYSTEM
        user_prompt = first_hop_prompt(problem)
    else:
        system_prompt = ESCALATION_SYSTEM
        user_prompt = escalation_prompt(
            problem,
            previous_code or "",
            previous_check["passed"]
            if previous_check
            else False,
            previous_check["error"]
            if previous_check
            else "",
        )

    if status_callback is not None:
        status_callback(
            "layer_start",
            layer_index,
            layer_key,
            None,
        )

    result = call_model(
        model_key=layer_key,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        seed=seed,
    )

    code = extract_code(
        result["response"],
        problem["entry_point"],
    )

    check = run_check(
        problem,
        code,
    )

    if status_callback is not None:
        status_callback(
            "layer_complete",
            layer_index,
            layer_key,
            {
                "passed": check["passed"],
                "tokens": result["total_tokens"],
                "latency_ms": result["latency_ms"],
                "attempts": result.get("attempts", 1),
                "retries": result.get("retries", 0),
            },
        )

    return result, code, check


def run_weak_only(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
) -> dict[str, Any]:
    return run_layer_only(
        problem,
        0,
        temperature,
        seed,
        "layer_1_only",
    )


def run_strong_only(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
) -> dict[str, Any]:
    return run_layer_only(
        problem,
        3,
        temperature,
        seed,
        "layer_4_only",
    )


def run_layer_only(
    problem: dict[str, Any],
    layer_index: int,
    temperature: float,
    seed: int,
    system_name: str,
) -> dict[str, Any]:
    problem_start = time.perf_counter()

    result, code, check = run_layer(
        problem,
        layer_index,
        None,
        None,
        temperature,
        seed,
    )

    total_latency_ms = (
        time.perf_counter() - problem_start
    ) * 1000.0

    return {
        "problem_id": problem["id"],
        "system": system_name,
        "final_code": code,
        "final_correct": check["passed"],
        "final_error": check["error"],
        "escalated": False,
        "stop_reason": system_name,
        "total_tokens": result["total_tokens"],
        "total_latency_ms": total_latency_ms,
        "strong_invoked": system_name == "layer_4_only",
        "hops": [
            build_hop(
                layer_index,
                result,
                code,
                check,
            )
        ],
    }


def run_fixed_cascade(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
    status_callback: Callable | None = None,
) -> dict[str, Any]:
    problem_start = time.perf_counter()

    hops = []
    total_tokens = 0
    previous_code = None
    previous_check = None
    final_code = ""
    final_check = None

    for layer_index in range(4):
        result, code, check = run_layer(
            problem,
            layer_index,
            previous_code,
            previous_check,
            temperature,
            seed,
            status_callback=status_callback,
        )

        hops.append(
            build_hop(
                layer_index,
                result,
                code,
                check,
            )
        )

        total_tokens += result["total_tokens"]
        final_code = code
        final_check = check
        previous_code = code
        previous_check = check

    total_latency_ms = (
        time.perf_counter() - problem_start
    ) * 1000.0

    return {
        "problem_id": problem["id"],
        "system": "fixed_cascade",
        "final_code": final_code,
        "final_correct": final_check["passed"],
        "final_error": final_check["error"],
        "escalated": True,
        "stop_reason": "layer_4",
        "total_tokens": total_tokens,
        "total_latency_ms": total_latency_ms,
        "strong_invoked": True,
        "hops": hops,
    }


def run_execution_gated_cascade(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
    verbose: bool = False,
    status_callback: Callable | None = None,
) -> dict[str, Any]:
    problem_start = time.perf_counter()

    hops = []
    total_tokens = 0
    previous_code = None
    previous_check = None

    for layer_index in range(4):
        result, code, check = run_layer(
            problem,
            layer_index,
            previous_code,
            previous_check,
            temperature,
            seed,
            status_callback=status_callback,
        )

        hops.append(
            build_hop(
                layer_index,
                result,
                code,
                check,
            )
        )

        total_tokens += result["total_tokens"]

        if check["passed"]:
            total_latency_ms = (
                time.perf_counter() - problem_start
            ) * 1000.0

            return {
                "problem_id": problem["id"],
                "system": "execution_gated",
                "final_code": code,
                "final_correct": True,
                "final_error": "",
                "escalated": layer_index > 0,
                "stop_reason": (
                    f"layer_{layer_index + 1}_verification_pass"
                ),
                "total_tokens": total_tokens,
                "total_latency_ms": total_latency_ms,
                "strong_invoked": layer_index >= 2,
                "hops": hops,
            }

        previous_code = code
        previous_check = check

    total_latency_ms = (
        time.perf_counter() - problem_start
    ) * 1000.0

    return {
        "problem_id": problem["id"],
        "system": "execution_gated",
        "final_code": previous_code or "",
        "final_correct": (
            previous_check["passed"]
            if previous_check
            else False
        ),
        "final_error": (
            previous_check["error"]
            if previous_check
            else "No candidate produced"
        ),
        "escalated": True,
        "stop_reason": "layer_4_verification_fail",
        "total_tokens": total_tokens,
        "total_latency_ms": total_latency_ms,
        "strong_invoked": True,
        "hops": hops,
    }


RUNNERS = {
    "weak_only": run_weak_only,
    "strong_only": run_strong_only,
    "layer_1_only": run_weak_only,
    "layer_4_only": run_strong_only,
    "fixed_cascade": run_fixed_cascade,
    "execution_gated": run_execution_gated_cascade,
}


def _print_experiment_header(
    mode: str,
    problems: list[dict[str, Any]],
    temperature: float,
    seed: int,
) -> None:
    print()
    print("=" * 72)
    print("LLM-CASCADE — EXPERIMENT")
    print("=" * 72)
    print(f"Mode        : {mode}")
    print(f"Problems    : {len(problems)}")
    print(f"Temperature : {temperature}")
    print(f"Seed        : {seed}")
    print()

    for key in LAYER_KEYS:
        model_config = MODEL_CONFIG[key]
        print(
            f"{model_config['name']:<16}: "
            f"{model_config['model']}"
        )

    print("=" * 72)
    print()


def run_baseline_suite(
    problems: list[dict[str, Any]],
    mode: str = "all",
    temperature: float = 0.0,
    seed: int = 42,
    verbose: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    if mode == "all":
        systems = (
            "layer_1_only",
            "layer_4_only",
            "fixed_cascade",
            "execution_gated",
        )
    elif mode in RUNNERS:
        systems = (mode,)
    else:
        raise ValueError(
            f"Unknown experiment mode: {mode}"
        )

    if verbose:
        _print_experiment_header(
            mode,
            problems,
            temperature,
            seed,
        )

    results: dict[str, list[dict[str, Any]]] = {}

    for system in systems:
        runner = RUNNERS[system]
        records = []
        completed = 0
        correct = 0
        escalated = 0
        start_time = time.perf_counter()

        if verbose:
            print(
                f"STARTING SYSTEM: {system.upper()}"
            )
            print("-" * 72)

        for index, problem in enumerate(
            problems,
            start=1,
        ):
            if verbose:
                print()
                print("=" * 72)
                print(
                    f"[{system}] "
                    f"{index}/{len(problems)} | "
                    f"{problem['id']}"
                )
                print("=" * 72)

            def status_callback(
                event,
                layer_index,
                layer_key,
                data,
            ):
                if not verbose:
                    return

                model_config = MODEL_CONFIG[layer_key]
                layer_name = model_config["name"]
                layer_number = layer_index + 1

                if event == "layer_start":
                    print(
                        f"▶ Layer {layer_number}/4 "
                        f"{layer_name:<18} RUNNING..."
                    )

                elif event == "layer_complete":
                    symbol = (
                        "✓"
                        if data["passed"]
                        else "✗"
                    )

                    retries = data.get(
                        "retries",
                        0,
                    )

                    retry_text = (
                        f" | retries={retries}"
                        if retries
                        else ""
                    )

                    print(
                        f"{symbol} "
                        f"Layer {layer_number}/4 "
                        f"{layer_name:<18} "
                        f"{'PASS' if data['passed'] else 'FAIL':<4} "
                        f"| {data['tokens']} tok "
                        f"| {data['latency_ms'] / 1000:.1f}s"
                        f"{retry_text}"
                    )

            runner_kwargs = {
                "temperature": temperature,
                "seed": seed,
            }

            if system in {
                "fixed_cascade",
                "execution_gated",
            }:
                runner_kwargs[
                    "status_callback"
                ] = status_callback

            record = runner(
                problem,
                **runner_kwargs,
            )

            records.append(record)

            completed += 1
            correct += int(
                record["final_correct"]
            )
            escalated += int(
                record["escalated"]
            )

            accuracy = correct / completed
            escalation_rate = (
                escalated / completed
            )

            if verbose:
                final_status = (
                    "CORRECT"
                    if record["final_correct"]
                    else "INCORRECT"
                )

                print(
                    f"→ {final_status} | "
                    f"stopped: "
                    f"{record['stop_reason']}"
                )

                print(
                    f"Progress: "
                    f"{completed}/{len(problems)} "
                    f"| Accuracy: {accuracy:.1%} "
                    f"| Escalation: "
                    f"{escalation_rate:.1%} "
                    f"| Elapsed: "
                    f"{(time.perf_counter() - start_time) / 60:.1f} min"
                )

        results[system] = records

        if verbose:
            total_elapsed = (
                time.perf_counter()
                - start_time
            )

            print()
            print("-" * 72)
            print(
                f"COMPLETED SYSTEM: "
                f"{system.upper()}"
            )
            print(
                f"Problems: "
                f"{completed}/{len(problems)}"
            )
            print(
                f"Accuracy: "
                f"{correct / completed:.1%}"
            )
            print(
                f"Escalation rate: "
                f"{escalated / completed:.1%}"
            )
            print(
                f"Runtime: "
                f"{total_elapsed / 60:.1f} min"
            )
            print("-" * 72)
            print()

    return results