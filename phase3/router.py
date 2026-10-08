from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from phase3.signals import signal_names, signal_vector


def load_records(
    path: Path,
) -> list[dict[str, Any]]:
    records = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line in handle:
            line = line.strip()

            if line:
                records.append(
                    json.loads(line)
                )

    return records


def build_training_matrix(
    records: list[dict[str, Any]],
    layer_key: str,
) -> tuple[np.ndarray, np.ndarray]:
    x = []
    y = []

    for record in records:
        layer = record["layers"].get(
            layer_key
        )

        if not layer:
            continue

        signals = layer.get(
            "signals",
            {},
        )

        if "is_arithmetic" not in signals:
            from phase3.signals import extract_signals
            layer_idx = int(layer_key.split("_")[-1]) - 1
            prev_resp = None
            if layer_idx > 0:
                prev_layer = record.get("layers", {}).get(f"layer_{layer_idx}")
                if prev_layer:
                    prev_resp = prev_layer.get("response")
            signals = extract_signals(
                response=layer.get("response", ""),
                problem=record,
                previous_response=prev_resp,
            )
            layer["signals"] = signals

        x.append(
            signal_vector(signals)
        )

        y.append(
            int(
                bool(
                    layer.get(
                        "correct",
                        False,
                    )
                )
            )
        )

    if not x:
        raise ValueError(
            f"No training records found for {layer_key}."
        )

    return (
        np.asarray(
            x,
            dtype=float,
        ),
        np.asarray(
            y,
            dtype=int,
        ),
    )


def create_router() -> Pipeline:
    return Pipeline(
        [
            (
                "scaler",
                StandardScaler(),
            ),
            (
                "classifier",
                LogisticRegression(
                    random_state=42,
                    max_iter=2000,
                    class_weight="balanced",
                ),
            ),
        ]
    )


def fit_router(
    records: list[dict[str, Any]],
    layer_key: str,
) -> tuple[Pipeline, dict[str, Any]]:
    x, y = build_training_matrix(
        records,
        layer_key,
    )

    if len(np.unique(y)) < 2:
        raise ValueError(
            f"{layer_key} training data contains "
            f"only one correctness class."
        )

    router = create_router()
    router.fit(x, y)

    classifier = router.named_steps[
        "classifier"
    ]

    metadata = {
        "layer_key": layer_key,
        "signals": signal_names(),
        "samples": int(len(y)),
        "correct": int(y.sum()),
        "incorrect": int(
            len(y) - y.sum()
        ),
        "coefficients": (
            classifier.coef_[0].tolist()
        ),
        "intercept": float(
            classifier.intercept_[0]
        ),
    }

    return router, metadata


def predict_correctness(
    router: Pipeline,
    signals: dict[str, Any],
) -> float:
    vector = np.asarray(
        [signal_vector(signals)],
        dtype=float,
    )

    probability = router.predict_proba(
        vector
    )[0, 1]

    return float(
        max(
            0.0,
            min(
                1.0,
                probability,
            ),
        )
    )


def choose_threshold(
    router: Pipeline,
    validation_records: list[dict[str, Any]],
    layer_key: str,
    max_false_stop_rate: float,
) -> dict[str, float]:
    probabilities = []
    correctness = []

    for record in validation_records:
        layer = record["layers"].get(
            layer_key
        )

        if not layer:
            continue

        signals = layer.get(
            "signals",
            {},
        )

        if "is_arithmetic" not in signals:
            from phase3.signals import extract_signals
            layer_idx = int(layer_key.split("_")[-1]) - 1
            prev_resp = None
            if layer_idx > 0:
                prev_layer = record.get("layers", {}).get(f"layer_{layer_idx}")
                if prev_layer:
                    prev_resp = prev_layer.get("response")
            signals = extract_signals(
                response=layer.get("response", ""),
                problem=record,
                previous_response=prev_resp,
            )
            layer["signals"] = signals

        probability = predict_correctness(
            router,
            signals,
        )

        probabilities.append(
            probability
        )

        correctness.append(
            bool(
                layer.get(
                    "correct",
                    False,
                )
            )
        )

    if not probabilities:
        raise ValueError(
            f"No validation records found for {layer_key}."
        )

    candidates = sorted(
        set(
            [
                0.0,
                0.5,
                0.55,
                0.60,
                0.65,
                0.70,
                0.75,
                0.80,
                0.85,
                0.90,
                0.95,
                1.0,
            ]
            + probabilities
        )
    )

    valid_candidates = []

    for threshold in candidates:
        stopped = [
            probability >= threshold
            for probability in probabilities
        ]

        if not any(stopped):
            continue

        false_stops = sum(
            1
            for stop, correct in zip(
                stopped,
                correctness,
            )
            if stop and not correct
        )

        total_stops = sum(stopped)

        false_stop_rate = (
            false_stops / total_stops
            if total_stops
            else 0.0
        )

        if false_stop_rate <= max_false_stop_rate:
            valid_candidates.append(
                {
                    "threshold": float(
                        threshold
                    ),
                    "false_stop_rate": float(
                        false_stop_rate
                    ),
                    "stop_rate": float(
                        total_stops
                        / len(probabilities)
                    ),
                    "validation_samples": float(
                        len(probabilities)
                    ),
                }
            )

    if not valid_candidates:
        return {
            "threshold": 1.0,
            "false_stop_rate": 0.0,
            "stop_rate": 0.0,
            "validation_samples": float(
                len(probabilities)
            ),
        }

    # Maximize stop_rate (minimum sufficient inference savings),
    # breaking ties with lowest false_stop_rate and higher threshold
    selected = max(
        valid_candidates,
        key=lambda item: (
            item["stop_rate"],
            -item["false_stop_rate"],
            item["threshold"],
        ),
    )

    return selected


def train_all_routers(
    train_records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
    output_directory: Path,
    max_false_stop_rate: float = 0.05,
) -> dict[str, Any]:
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    metadata = {}
    thresholds = {}

    from phase3.config import LAYER_KEYS

    for layer_key in LAYER_KEYS:
        router, router_metadata = fit_router(
            train_records,
            layer_key,
        )

        threshold = choose_threshold(
            router,
            validation_records,
            layer_key,
            max_false_stop_rate,
        )

        joblib.dump(
            router,
            output_directory
            / f"{layer_key}.joblib",
        )

        metadata[layer_key] = (
            router_metadata
        )

        thresholds[layer_key] = threshold

    with (
        output_directory
        / "thresholds.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            thresholds,
            handle,
            indent=2,
        )

    with (
        output_directory
        / "metadata.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            metadata,
            handle,
            indent=2,
        )

    return {
        "routers": metadata,
        "thresholds": thresholds,
    }


def load_routers(
    directory: Path,
) -> dict[str, Pipeline]:
    from phase3.config import LAYER_KEYS

    routers = {}

    for layer_key in LAYER_KEYS:
        path = (
            directory
            / f"{layer_key}.joblib"
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Missing router: {path}"
            )

        routers[layer_key] = joblib.load(
            path
        )

    return routers


def load_thresholds(
    directory: Path,
) -> dict[str, dict[str, float]]:
    path = (
        directory
        / "thresholds.json"
    )

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(handle)