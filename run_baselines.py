from __future__ import annotations

import ast
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import ollama

from config import MODEL_CONFIG
from sandbox import run_check


LAYER_KEYS = (
    "layer_1",
    "layer_2",
    "layer_3",
    "layer_4",
)

LAYER_TIMEOUTS = {
    "layer_1": 180.0,
    "layer_2": 300.0,
    "layer_3": 600.0,
    "layer_4": 600.0,
}

LAYER_MAX_ATTEMPTS = {
    "layer_1": 3,
    "layer_2": 2,
    "layer_3": 2,
    "layer_4": 2,
}

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_ATTEMPTS = 2
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

CLIENTS: dict[float, ollama.Client] = {}


def get_client(timeout: float) -> ollama.Client:
    if timeout not in CLIENTS:
        CLIENTS[timeout] = ollama.Client(timeout=timeout)

    return CLIENTS[timeout]


class ModelCallError(RuntimeError):
    def __init__(
        self,
        message: str,
        model_key: str,
        model: str,
        attempts: int,
        latency_ms: float,
    ) -> None:
        super().__init__(message)
        self.model_key = model_key
        self.model = model
        self.attempts = attempts
        self.latency_ms = latency_ms


class LiveReporter:
    def __init__(self, total: int, enabled: bool = True) -> None:
        self.total = total
        self.enabled = enabled
        self.completed = 0
        self.correct = 0
        self.escalated = 0
        self.run_start = time.perf_counter()
        self.stop_event = threading.Event()
        self.ticker: threading.Thread | None = None
        self.layer_start_time = 0.0
        self.label = ""
        self.is_tty = sys.stdout.isatty()

    def problem_loaded(self, index: int, problem_id: str) -> None:
        if not self.enabled:
            return

        print()
        print(f"* [{index}/{self.total}] {problem_id}")
        print("  Status: LOADED", flush=True)

    def layer_start(self, layer_index: int, layer_name: str) -> None:
        if not self.enabled:
            return

        self.label = f"SOLVING — L{layer_index + 1} {layer_name}"
        self.restart_ticker()

    def layer_complete(
        self,
        layer_index: int,
        layer_name: str,
        passed: bool,
        tokens: int,
        latency_ms: float,
        retries: int = 0,
    ) -> None:
        if not self.enabled:
            return

        self.halt_ticker()

        mark = "✓ PASS" if passed else "✗ FAIL"
        retry_text = f" | retries={retries}" if retries else ""

        print(
            f"  {mark}  L{layer_index + 1} {layer_name} "
            f"| {tokens:,} tok "
            f"| {latency_ms / 1000:.1f}s"
            f"{retry_text}",
            flush=True,
        )

    def layer_error(
        self,
        layer_index: int,
        layer_name: str,
        message: str,
        latency_ms: float,
        attempts: int,
    ) -> None:
        if not self.enabled:
            return

        self.halt_ticker()

        print(
            f"  ✗ ERROR L{layer_index + 1} {layer_name} "
            f"| {attempts} attempts "
            f"| {latency_ms / 1000:.1f}s",
            flush=True,
        )
        print(f"    {message}", flush=True)

    def problem_done(
        self,
        correct: bool,
        escalated: bool,
        tokens: int,
        seconds: float,
        stop_reason: str,
    ) -> None:
        self.completed += 1
        self.correct += int(correct)
        self.escalated += int(escalated)

        if not self.enabled:
            return

        verdict = "SOLVED — correct" if correct else "FAILED — incorrect"
        symbol = "✓" if correct else "✗"
        elapsed_min = (time.perf_counter() - self.run_start) / 60.0

        print(f"  {symbol} {verdict}")
        print(f"  Tokens: {tokens:,}")
        print(f"  Time: {seconds:.1f}s")
        print(f"  Stopped: {stop_reason}")
        print(
            f"  Progress: {self.completed}/{self.total} "
            f"| Accuracy: {self.correct / self.completed:.1%} "
            f"| Escalation: {self.escalated / self.completed:.1%} "
            f"| Elapsed: {elapsed_min:.1f} min",
            flush=True,
        )

    def tick(self) -> None:
        while not self.stop_event.wait(0.5):
            if self.is_tty:
                elapsed = time.perf_counter() - self.layer_start_time
                print(
                    f"\r  Status: {self.label} | Elapsed: {elapsed:.1f}s",
                    end="",
                    flush=True,
                )

        if self.is_tty:
            print("\r" + " " * 100 + "\r", end="", flush=True)

    def restart_ticker(self) -> None:
        self.halt_ticker()
        self.stop_event.clear()
        self.layer_start_time = time.perf_counter()
        self.ticker = threading.Thread(
            target=self.tick,
            daemon=True,
        )
        self.ticker.start()

    def halt_ticker(self) -> None:
        self.stop_event.set()

        if self.ticker is not None:
            self.ticker.join()
            self.ticker = None

    def system_start(self, system: str) -> None:
        if not self.enabled:
            return

        print()
        print("=" * 72)
        print(f"STARTING SYSTEM: {system.upper()}")
        print("=" * 72, flush=True)

    def system_done(self, system: str) -> None:
        if not self.enabled or self.completed == 0:
            return

        elapsed_min = (time.perf_counter() - self.run_start) / 60.0

        print()
        print("-" * 72)
        print(f"COMPLETED SYSTEM: {system.upper()}")
        print(f"Problems: {self.completed}/{self.total}")
        print(f"Accuracy: {self.correct / self.completed:.1%}")
        print(f"Escalation rate: {self.escalated / self.completed:.1%}")
        print(f"Runtime: {elapsed_min:.1f} min")
        print("-" * 72, flush=True)


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


def isolate_function(
    block: str,
    entry_point: str,
) -> str:
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
                return isolate_function(
                    block,
                    entry_point,
                ).strip()

        for block in fenced_blocks:
            isolated = isolate_function(
                block,
                entry_point,
            )

            if isolated != block:
                return isolated.strip()

        return fenced_blocks[0].strip()

    function_start = text.find(f"def {entry_point}")

    if function_start != -1:
        return isolate_function(
            text[function_start:],
            entry_point,
        ).strip()

    isolated = isolate_function(
        text,
        entry_point,
    )

    if isolated != text:
        return isolated.strip()

    return text.strip()


def is_transient_ollama_error(exc: Exception) -> bool:
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
    reporter: LiveReporter | None = None,
) -> dict[str, Any]:
    model = model_name(model_key)
    timeout = LAYER_TIMEOUTS.get(model_key, DEFAULT_TIMEOUT_SECONDS)
    max_attempts = LAYER_MAX_ATTEMPTS.get(model_key, DEFAULT_MAX_ATTEMPTS)
    client = get_client(timeout)

    start = time.perf_counter()
    last_exception: Exception | None = None
    response = None
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt

        try:
            response = client.chat(
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
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            if not is_transient_ollama_error(exc):
                raise ModelCallError(
                    f"Non-transient error for '{model}' "
                    f"(key='{model_key}'): {exc}",
                    model_key,
                    model,
                    attempts,
                    elapsed_ms,
                ) from exc

            if attempt >= max_attempts:
                raise ModelCallError(
                    f"Failed after {max_attempts} attempts for '{model}' "
                    f"(key='{model_key}'): {exc}",
                    model_key,
                    model,
                    attempts,
                    elapsed_ms,
                ) from exc

            delay = RETRY_DELAYS_SECONDS[
                min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)
            ]

            if reporter is not None and reporter.enabled:
                reporter.halt_ticker()
                print(
                    f"  ! Attempt {attempt}/{max_attempts} failed "
                    f"({timeout:.0f}s timeout): {exc}"
                )
                print(f"  ! Retrying in {delay:.0f}s...", flush=True)

            time.sleep(delay)

            if reporter is not None and reporter.enabled:
                reporter.restart_ticker()

    if response is None:
        raise ModelCallError(
            f"No response for '{model}' (key='{model_key}'): "
            f"{last_exception}",
            model_key,
            model,
            attempts,
            (time.perf_counter() - start) * 1000.0,
        )

    latency_ms = (time.perf_counter() - start) * 1000.0

    content = response.message.content or ""
    prompt_tokens = int(response.prompt_eval_count or 0)
    completion_tokens = int(response.eval_count or 0)

    return {
        "model_key": model_key,
        "model": model,
        "response": content,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "retries": attempts - 1,
        "seed": seed,
        "error": "",
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
        "model_error": result.get("error", ""),
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
    reporter: LiveReporter | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    layer_key = LAYER_KEYS[layer_index]
    layer_name = MODEL_CONFIG[layer_key]["name"]

    if layer_index == 0:
        system_prompt = FIRST_HOP_SYSTEM
        user_prompt = first_hop_prompt(problem)
    else:
        system_prompt = ESCALATION_SYSTEM
        user_prompt = escalation_prompt(
            problem,
            previous_code or "",
            previous_check["passed"] if previous_check else False,
            previous_check["error"] if previous_check else "",
        )

    if reporter is not None:
        reporter.layer_start(layer_index, layer_name)

    try:
        result = call_model(
            model_key=layer_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            seed=seed,
            reporter=reporter,
        )
    except ModelCallError as exc:
        if reporter is not None:
            reporter.layer_error(
                layer_index,
                layer_name,
                str(exc),
                exc.latency_ms,
                exc.attempts,
            )

        result = {
            "model_key": exc.model_key,
            "model": exc.model,
            "response": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": exc.latency_ms,
            "attempts": exc.attempts,
            "retries": max(exc.attempts - 1, 0),
            "seed": seed,
            "error": str(exc),
        }

        check = {
            "passed": False,
            "error": f"model_call_failed: {exc}",
        }

        return result, previous_code or "", check
    except Exception:
        if reporter is not None:
            reporter.halt_ticker()
        raise

    code = extract_code(
        result["response"],
        problem["entry_point"],
    )

    check = run_check(
        problem,
        code,
    )

    if reporter is not None:
        reporter.layer_complete(
            layer_index,
            layer_name,
            check["passed"],
            result["total_tokens"],
            result["latency_ms"],
            result.get("retries", 0),
        )

    return result, code, check


def run_layer_only(
    problem: dict[str, Any],
    layer_index: int,
    temperature: float,
    seed: int,
    system_name: str,
    reporter: LiveReporter | None = None,
) -> dict[str, Any]:
    problem_start = time.perf_counter()

    result, code, check = run_layer(
        problem,
        layer_index,
        None,
        None,
        temperature,
        seed,
        reporter,
    )

    total_latency_ms = (time.perf_counter() - problem_start) * 1000.0

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
        "strong_invoked": layer_index == 3,
        "hops": [
            build_hop(
                layer_index,
                result,
                code,
                check,
            )
        ],
    }


def run_weak_only(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
    reporter: LiveReporter | None = None,
) -> dict[str, Any]:
    return run_layer_only(
        problem,
        0,
        temperature,
        seed,
        "layer_1_only",
        reporter,
    )


def run_strong_only(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
    reporter: LiveReporter | None = None,
) -> dict[str, Any]:
    return run_layer_only(
        problem,
        3,
        temperature,
        seed,
        "layer_4_only",
        reporter,
    )


def run_fixed_cascade(
    problem: dict[str, Any],
    temperature: float = 0.0,
    seed: int = 42,
    reporter: LiveReporter | None = None,
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
            reporter,
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

    total_latency_ms = (time.perf_counter() - problem_start) * 1000.0

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
    reporter: LiveReporter | None = None,
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
            reporter,
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
            total_latency_ms = (time.perf_counter() - problem_start) * 1000.0

            return {
                "problem_id": problem["id"],
                "system": "execution_gated",
                "final_code": code,
                "final_correct": True,
                "final_error": "",
                "escalated": layer_index > 0,
                "stop_reason": f"layer_{layer_index + 1}_verification_pass",
                "total_tokens": total_tokens,
                "total_latency_ms": total_latency_ms,
                "strong_invoked": layer_index >= 2,
                "hops": hops,
            }

        previous_code = code
        previous_check = check

    total_latency_ms = (time.perf_counter() - problem_start) * 1000.0

    return {
        "problem_id": problem["id"],
        "system": "execution_gated",
        "final_code": previous_code or "",
        "final_correct": previous_check["passed"],
        "final_error": previous_check["error"],
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


def append_record(
    stream_dir: Path | None,
    system: str,
    record: dict[str, Any],
) -> None:
    if stream_dir is None:
        return

    stream_dir.mkdir(parents=True, exist_ok=True)

    path = stream_dir / f"stream_{system}.jsonl"

    with path.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


def load_completed_records(
    stream_dir: Path | None,
    system: str,
) -> list[dict[str, Any]]:
    if stream_dir is None:
        return []

    path = stream_dir / f"stream_{system}.jsonl"

    if not path.exists():
        return []

    records = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return records


def run_baseline_suite(
    problems: list[dict[str, Any]],
    mode: str = "all",
    temperature: float = 0.0,
    seed: int = 42,
    verbose: bool = True,
    stream_dir: Path | None = None,
    resume: bool = False,
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
        raise ValueError(f"Unknown experiment mode: {mode}")

    results: dict[str, list[dict[str, Any]]] = {}

    for system in systems:
        runner = RUNNERS[system]
        reporter = LiveReporter(
            total=len(problems),
            enabled=verbose,
        )

        records: list[dict[str, Any]] = []
        done_ids: set[str] = set()

        if resume:
            wanted = {problem["id"] for problem in problems}
            records = [
                record
                for record in load_completed_records(stream_dir, system)
                if record.get("problem_id") in wanted
            ]
            done_ids = {record["problem_id"] for record in records}

            for record in records:
                reporter.completed += 1
                reporter.correct += int(record["final_correct"])
                reporter.escalated += int(record["escalated"])

            if verbose:
                print(
                    f"Resuming {system}: {len(done_ids)} problems already "
                    f"complete, {len(problems) - len(done_ids)} remaining."
                )

        reporter.system_start(system)

        try:
            for index, problem in enumerate(problems, start=1):
                if problem["id"] in done_ids:
                    continue

                reporter.problem_loaded(index, problem["id"])

                record = runner(
                    problem,
                    temperature=temperature,
                    seed=seed,
                    reporter=reporter,
                )

                records.append(record)
                append_record(stream_dir, system, record)

                reporter.problem_done(
                    record["final_correct"],
                    record["escalated"],
                    record["total_tokens"],
                    record["total_latency_ms"] / 1000.0,
                    record["stop_reason"],
                )
        finally:
            reporter.halt_ticker()

        results[system] = records
        reporter.system_done(system)

    return results