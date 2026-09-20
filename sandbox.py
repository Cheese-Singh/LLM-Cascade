from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:
    resource = None


@dataclass(frozen=True)
class SandboxConfig:
    timeout_s: float = 5.0
    memory_mb: int = 512
    max_output_chars: int = 12000
    max_file_size_mb: int = 32
    max_processes: int = 32
    max_open_files: int = 64


DEFAULT_SANDBOX_CONFIG = SandboxConfig()


def _apply_resource_limits(config: SandboxConfig) -> None:
    if resource is None:
        return

    cpu_seconds = max(1, int(config.timeout_s))

    limits = [
        (
            resource.RLIMIT_CPU,
            cpu_seconds,
            cpu_seconds + 1,
        ),
        (
            resource.RLIMIT_AS,
            config.memory_mb * 1024 * 1024,
            config.memory_mb * 1024 * 1024,
        ),
        (
            resource.RLIMIT_FSIZE,
            config.max_file_size_mb * 1024 * 1024,
            config.max_file_size_mb * 1024 * 1024,
        ),
        (
            resource.RLIMIT_NOFILE,
            config.max_open_files,
            config.max_open_files,
        ),
    ]

    if hasattr(resource, "RLIMIT_NPROC"):
        limits.append(
            (
                resource.RLIMIT_NPROC,
                config.max_processes,
                config.max_processes,
            )
        )

    for resource_type, soft, hard in limits:
        try:
            resource.setrlimit(
                resource_type,
                (soft, hard),
            )
        except (ValueError, OSError):
            pass


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(
            process.pid,
            signal.SIGKILL,
        )
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _truncate_output(
    output: str,
    max_chars: int,
) -> str:
    if len(output) <= max_chars:
        return output

    return (
        output[:max_chars]
        + f"\n... output truncated after {max_chars} characters."
    )


def _build_source(
    problem: dict[str, Any],
    candidate_code: str,
) -> str:
    entry_point = problem["entry_point"]
    check_code = problem["check"]

    return (
        candidate_code.rstrip()
        + "\n\n"
        + check_code.rstrip()
        + "\n\n"
        + f"check({entry_point})\n"
    )


def run_check(
    problem: dict[str, Any],
    candidate_code: str,
    config: SandboxConfig | None = None,
) -> dict[str, Any]:
    config = config or DEFAULT_SANDBOX_CONFIG

    if not candidate_code or not candidate_code.strip():
        return {
            "passed": False,
            "error": "Candidate code is empty.",
            "return_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "execution_time_ms": 0.0,
        }

    if "entry_point" not in problem:
        return {
            "passed": False,
            "error": "Problem is missing 'entry_point'.",
            "return_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "execution_time_ms": 0.0,
        }

    if "check" not in problem:
        return {
            "passed": False,
            "error": "Problem is missing 'check'.",
            "return_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "execution_time_ms": 0.0,
        }

    source = _build_source(
        problem,
        candidate_code,
    )

    with tempfile.TemporaryDirectory(
        prefix="llm_cascade_"
    ) as temp_dir:
        temp_path = Path(temp_dir) / "candidate_test.py"

        try:
            temp_path.write_text(
                source,
                encoding="utf-8",
            )
        except OSError as exc:
            return {
                "passed": False,
                "error": f"Failed to create sandbox file: {exc}",
                "return_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "execution_time_ms": 0.0,
            }

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONHASHSEED": "0",
        }

        command = [
            sys.executable,
            "-I",
            str(temp_path),
        ]

        process = None
        start_time = __import__("time").perf_counter()

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=temp_dir,
                env=environment,
                start_new_session=True,
                preexec_fn=(
                    lambda: _apply_resource_limits(config)
                    if resource is not None
                    else None
                ),
            )

            try:
                stdout, stderr = process.communicate(
                    timeout=config.timeout_s
                )
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_group(process)
                stdout, stderr = process.communicate()

        except Exception as exc:
            elapsed_ms = (
                __import__("time").perf_counter()
                - start_time
            ) * 1000.0

            if process is not None:
                _kill_process_group(process)

            return {
                "passed": False,
                "error": f"Sandbox execution failed: {exc}",
                "return_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "execution_time_ms": elapsed_ms,
            }

        elapsed_ms = (
            __import__("time").perf_counter()
            - start_time
        ) * 1000.0

        stdout = _truncate_output(
            stdout or "",
            config.max_output_chars,
        )

        stderr = _truncate_output(
            stderr or "",
            config.max_output_chars,
        )

        if timed_out:
            return {
                "passed": False,
                "error": (
                    f"Execution timed out after "
                    f"{config.timeout_s:.2f}s."
                ),
                "return_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": True,
                "execution_time_ms": elapsed_ms,
            }

        if process.returncode == 0:
            return {
                "passed": True,
                "error": "",
                "return_code": 0,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "execution_time_ms": elapsed_ms,
            }

        error_parts = []

        if stderr.strip():
            error_parts.append(stderr.strip())

        if stdout.strip():
            error_parts.append(stdout.strip())

        if not error_parts:
            error_parts.append(
                f"Process exited with return code "
                f"{process.returncode}."
            )

        return {
            "passed": False,
            "error": "\n".join(error_parts),
            "return_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "execution_time_ms": elapsed_ms,
        }