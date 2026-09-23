from __future__ import annotations

from pathlib import Path


DEFAULT_PROBLEMS_FILE = Path(
    "benchmarks/code_subset.json"
)

DEFAULT_OUTPUT_DIR = Path(
    "results"
)

DEFAULT_TRACE_DIR = Path(
    "traces"
)


SUPPORTED_DOMAINS = (
    "code",
    "math",
    "finance",
)


DEFAULT_DOMAIN = "code"


DEFAULT_PROBLEM_FILES = {
    "code": Path(
        "benchmarks/code_subset.json"
    ),
    "math": Path(
        "benchmarks/math.json"
    ),
    "finance": Path(
        "benchmarks/finance.json"
    ),
}


EXPERIMENT_MODES = (
    "all",
    "layer_1_only",
    "layer_4_only",
    "fixed_cascade",
    "execution_gated",
)


MODEL_CONFIG = {
    "layer_1": {
        "key": "layer_1",
        "model": "gpt-oss:20b-cloud",
        "name": "gpt-oss-20b",
    },
    "layer_2": {
        "key": "layer_2",
        "model": "gemma4:cloud",
        "name": "gemma4",
    },
    "layer_3": {
        "key": "layer_3",
        "model": "nemotron-3-super:cloud",
        "name": "nemotron-3-super",
    },
    "layer_4": {
        "key": "layer_4",
        "model": "nemotron-3-ultra:cloud",
        "name": "nemotron-3-ultra",
    },
}


OLLAMA_HOST = (
    "http://localhost:11434"
)


DEFAULT_TEMPERATURE = 0.2
DEFAULT_SEED = 42


DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MEMORY_MB = 512
DEFAULT_MAX_OUTPUT_CHARS = 12000
DEFAULT_MAX_FILE_SIZE_MB = 32
DEFAULT_MAX_PROCESSES = 32
DEFAULT_MAX_OPEN_FILES = 64


DEFAULT_SANDBOX_CONFIG = {
    "timeout_s": DEFAULT_TIMEOUT_SECONDS,
    "memory_mb": DEFAULT_MEMORY_MB,
    "max_output_chars": DEFAULT_MAX_OUTPUT_CHARS,
    "max_file_size_mb": DEFAULT_MAX_FILE_SIZE_MB,
    "max_processes": DEFAULT_MAX_PROCESSES,
    "max_open_files": DEFAULT_MAX_OPEN_FILES,
}


MODEL_GENERATION_OPTIONS = {
    "temperature": DEFAULT_TEMPERATURE,
    "seed": DEFAULT_SEED,
}


RESULT_FILE_PREFIXES = {
    "experiment": "experiment",
    "summary": "summary",
    "records": "records",
}


REQUIRED_PROBLEM_KEYS = {
    "id",
    "prompt",
    "check",
    "entry_point",
}