from __future__ import annotations

from pathlib import Path


PHASE3_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PHASE3_ROOT.parent

DATA_ROOT = PHASE3_ROOT / "data"
TATQA_DATA_ROOT = DATA_ROOT / "tatqa"
EXTRA_TRAIN_TRACE_PATH = (
    DATA_ROOT
    / "phase3_tatqa_train_extra_counterfactual.jsonl"
)
EXTRA_TRAIN_SELECTION_PATH = (
    DATA_ROOT
    / "phase3_tatqa_train_extra_selection.jsonl"
)

TRACES_ROOT = PROJECT_ROOT / "traces" / "phase3"
ROUTERS_ROOT = PHASE3_ROOT / "routers"

SEED = 42
TEMPERATURE = 0.0

LAYER_KEYS = [
    "layer_1",
    "layer_2",
    "layer_3",
    "layer_4",
]

PHASE3_MODEL_CONFIG = {
    "layer_1": {
        "model": "gpt-oss:20b-cloud",
    },
    "layer_2": {
        "model": "gemma4:cloud",
    },
    "layer_3": {
        "model": "nemotron-3-super:cloud",
    },
    "layer_4": {
        "model": "nemotron-3-ultra:cloud",
    },
}

TATQA_SUBSET_SIZES = {
    "train": 300,
    "validation": 100,
    "test": 100,
}

MAX_FALSE_STOP_RATE = 0.05