from __future__ import annotations

import os
from pathlib import Path

PHASE4_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PHASE4_ROOT.parent

DATA_DIR = PHASE4_ROOT / "data"
BENCHMARK_PATH = DATA_DIR / "benchmark_120.json"
TRACES_DIR = PROJECT_ROOT / "traces" / "phase4"
RESULTS_DIR = PROJECT_ROOT / "results" / "phase4"
ROUTERS_DIR = PHASE4_ROOT / "routers"

# Model Ladder configuration
PHASE4_MODEL_CONFIG = {
    "layer_1": {
        "key": "layer_1",
        "model": "gpt-oss:20b-cloud",
        "name": "gpt-oss-20b",
        "cost_per_1k_tokens": 0.0005,
    },
    "layer_2": {
        "key": "layer_2",
        "model": "gemma4:cloud",
        "name": "gemma4",
        "cost_per_1k_tokens": 0.0010,
    },
    "layer_3": {
        "key": "layer_3",
        "model": "nemotron-3-super:cloud",
        "name": "nemotron-3-super",
        "cost_per_1k_tokens": 0.0020,
    },
    "layer_4": {
        "key": "layer_4",
        "model": "nemotron-3-ultra:cloud",
        "name": "nemotron-3-ultra",
        "cost_per_1k_tokens": 0.0040,
    },
}

DEFAULT_JUDGE_MODEL = "gpt-oss:120b-cloud"
CHEAP_JUDGE_MODEL = "qwen3.5:0.8b"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Cascade parameters
MAX_FALSE_STOP_RATE = 0.05
DEFAULT_TEMPERATURE = 0.1
DEFAULT_SEED = 42

# Database configuration (zero-secret by default)
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
SQLITE_DB_PATH = DATA_DIR / "audit_fixture.db"
