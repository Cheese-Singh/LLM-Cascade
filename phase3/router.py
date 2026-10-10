from __future__ import annotations

import json
import itertools
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from phase3.config import LAYER_KEYS
from phase3.dataset import has_nonempty_gold_answer
from phase3.signals import (
    extract_signals,
    signal_names,
    signal_vector,
)


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
    samples_by_id: dict[str, tuple[int, int, list[float], int]] = {}
    samples_without_id: list[tuple[list[float], int]] = []

    for record in records:
        if not has_nonempty_gold_answer(record):
            continue

        layer = record.get("layers", {}).get(
            layer_key
        )

        if not layer:
            continue

        response = str(layer.get("response") or "")
        if not response.strip():
            continue

        layer_number = int(layer_key.split("_")[-1])
        previous_layer = record.get("layers", {}).get(
            f"layer_{layer_number - 1}"
        )
        previous_response = (
            str(previous_layer.get("response") or "")
            if previous_layer
            else None
        )
        signals = extract_signals(
            response=response,
            problem=record,
            previous_response=previous_response,
        )
        layer["signals"] = signals

        vector = signal_vector(signals)
        label = int(bool(layer.get("correct", False)))
        record_id = (
            record.get("problem_id")
            or record.get("id")
        )
        if record_id is None:
            samples_without_id.append((vector, label))
            continue

        completeness = sum(
            bool(
                str(
                    candidate.get("response") or ""
                ).strip()
            )
            for candidate in record.get("layers", {}).values()
        )
        counterfactual = int(
            record.get("collection_mode") == "counterfactual"
        )
        rank = (
            counterfactual,
            completeness,
        )
        previous = samples_by_id.get(str(record_id))
        if previous is None or rank > previous[:2]:
            samples_by_id[str(record_id)] = (
                counterfactual,
                completeness,
                vector,
                label,
            )

    samples = [
        (sample[2], sample[3])
        for sample in samples_by_id.values()
    ] + samples_without_id
    x = [sample[0] for sample in samples]
    y = [sample[1] for sample in samples]

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


def create_router(
    c: float = 10.0,
    class_weight: str | None = None,
) -> Pipeline:
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
                    class_weight=class_weight,
                    C=c,
                ),
            ),
        ]
    )


def fit_router(
    records: list[dict[str, Any]],
    layer_key: str,
    c: float = 10.0,
    class_weight: str | None = None,
    feature_indices: list[int] | None = None,
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

    if feature_indices is None:
        feature_indices = list(range(x.shape[1]))
    router = create_router(
        c=c,
        class_weight=class_weight,
    )
    router.fit(x[:, feature_indices], y)
    router.feature_indices_ = feature_indices

    classifier = router.named_steps[
        "classifier"
    ]

    metadata = {
        "layer_key": layer_key,
        "signals": [
            signal_names()[index]
            for index in feature_indices
        ],
        "feature_indices": feature_indices,
        "regularization_c": c,
        "class_weight": class_weight,
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


def select_router_hyperparameters(
    records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
    layer_key: str,
    feature_indices: list[int] | None = None,
) -> dict[str, Any]:
    x_train, y_train = build_training_matrix(
        records,
        layer_key,
    )
    if len(np.unique(y_train)) < 2:
        raise ValueError(
            f"{layer_key} training data contains "
            "only one correctness class."
        )
    if feature_indices is None:
        feature_indices = list(range(x_train.shape[1]))

    validation_rows = []
    validation_labels = []
    layer_number = int(layer_key.split("_")[-1])
    for record in validation_records:
        layer = record.get("layers", {}).get(layer_key)
        if not layer or not str(layer.get("response") or "").strip():
            continue
        previous_layer = record.get("layers", {}).get(
            f"layer_{layer_number - 1}"
        )
        signals = extract_signals(
            response=str(layer.get("response") or ""),
            problem=record,
            previous_response=(
                str(previous_layer.get("response") or "")
                if previous_layer
                else None
            ),
        )
        validation_rows.append(signal_vector(signals))
        validation_labels.append(
            int(bool(layer.get("correct", False)))
        )

    if not validation_rows:
        raise ValueError(
            f"No usable validation records found for {layer_key}."
        )

    x_validation = np.asarray(validation_rows, dtype=float)
    y_validation = np.asarray(validation_labels, dtype=int)
    candidates = []
    for class_weight in (None, "balanced"):
        for c in (0.01, 0.1, 1.0, 10.0):
            router = create_router(
                c=c,
                class_weight=class_weight,
            )
            router.fit(
                x_train[:, feature_indices],
                y_train,
            )
            scores = router.predict_proba(
                x_validation[:, feature_indices]
            )[:, 1]
            brier_score = float(
                np.mean((scores - y_validation) ** 2)
            )
            candidates.append(
                (
                    brier_score,
                    c,
                    class_weight or "",
                )
            )

    best_score, best_c, best_weight = min(candidates)
    return {
        "feature_indices": feature_indices,
        "regularization_c": best_c,
        "class_weight": best_weight or None,
        "validation_brier_score": best_score,
        "validation_samples": int(len(y_validation)),
    }


def select_router_feature_subset(
    records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
    layer_key: str,
) -> dict[str, Any]:
    names = signal_names()
    x_train, y_train = build_training_matrix(
        records,
        layer_key,
    )
    if len(np.unique(y_train)) < 2:
        raise ValueError(
            f"{layer_key} training data contains only one correctness class."
        )

    validation_rows = []
    validation_labels = []
    layer_number = int(layer_key.split("_")[-1])
    for record in validation_records:
        layer = record.get("layers", {}).get(layer_key)
        if not layer or not str(layer.get("response") or "").strip():
            continue
        previous_layer = record.get("layers", {}).get(
            f"layer_{layer_number - 1}"
        )
        signals = extract_signals(
            response=str(layer.get("response") or ""),
            problem=record,
            previous_response=(
                str(previous_layer.get("response") or "")
                if previous_layer
                else None
            ),
        )
        validation_rows.append(signal_vector(signals))
        validation_labels.append(
            int(bool(layer.get("correct", False)))
        )

    if not validation_rows:
        raise ValueError(
            f"No usable validation records found for {layer_key}."
        )

    x_validation = np.asarray(validation_rows, dtype=float)
    y_validation = np.asarray(validation_labels, dtype=int)
    feature_variants = [
        ("all_features", list(range(len(names))))
    ]
    feature_variants.extend(
        (
            f"without_{name}",
            [
                index
                for index in range(len(names))
                if index != omitted
            ],
        )
        for omitted, name in enumerate(names)
    )

    results = {}
    for variant, indices in feature_variants:
        model = create_router()
        model.fit(
            x_train[:, indices],
            y_train,
        )
        scores = model.predict_proba(
            x_validation[:, indices]
        )[:, 1]
        results[variant] = {
            "feature_indices": indices,
            "features": [names[index] for index in indices],
            "samples": int(len(y_validation)),
            "accuracy_at_half": float(
                np.mean((scores >= 0.5) == y_validation)
            ),
            "brier_score": float(
                np.mean((scores - y_validation) ** 2)
            ),
        }

    selected_variant = min(
        results,
        key=lambda name: (
            results[name]["brier_score"],
            name != "all_features",
            len(results[name]["feature_indices"]),
        ),
    )
    return {
        "selected_variant": selected_variant,
        "feature_indices": results[selected_variant]["feature_indices"],
        "features": results[selected_variant]["features"],
        "validation_brier_score": results[selected_variant]["brier_score"],
        "validation_samples": results[selected_variant]["samples"],
        "ablations": {
            name: {
                "features": result["features"],
                "samples": result["samples"],
                "accuracy_at_half": result["accuracy_at_half"],
                "brier_score": result["brier_score"],
            }
            for name, result in results.items()
        },
    }


def predict_correctness(
    router: Pipeline,
    signals: dict[str, Any],
) -> float:
    features = signal_vector(signals)
    feature_indices = getattr(
        router,
        "feature_indices_",
        list(range(len(features))),
    )
    vector = np.asarray(
        [[features[index] for index in feature_indices]],
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

        layer_number = int(layer_key.split("_")[-1])
        previous_layer = record.get("layers", {}).get(
            f"layer_{layer_number - 1}"
        )
        signals = extract_signals(
            response=str(layer.get("response") or ""),
            problem=record,
            previous_response=(
                str(previous_layer.get("response") or "")
                if previous_layer
                else None
            ),
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


def choose_thresholds_for_cascade(
    routers: dict[str, Pipeline],
    validation_records: list[dict[str, Any]],
    max_false_stop_rate: float,
    min_accuracy: float = 0.0,
) -> dict[str, dict[str, float]]:
    if not validation_records:
        raise ValueError(
            "Cannot tune router thresholds without validation records."
        )

    probabilities = np.full(
        (len(validation_records), len(LAYER_KEYS)),
        np.nan,
        dtype=float,
    )
    correctness = np.zeros(
        (len(validation_records), len(LAYER_KEYS)),
        dtype=bool,
    )
    cumulative_tokens = np.zeros(
        (len(validation_records), len(LAYER_KEYS)),
        dtype=float,
    )

    for record_index, record in enumerate(validation_records):
        running_tokens = 0.0
        for layer_index, layer_key in enumerate(LAYER_KEYS):
            layer = record.get("layers", {}).get(layer_key)
            if layer:
                response = str(layer.get("response") or "")
                if response.strip():
                    previous_layer = record.get("layers", {}).get(
                        f"layer_{layer_index}"
                    )
                    signals = extract_signals(
                        response=response,
                        problem=record,
                        previous_response=(
                            str(previous_layer.get("response") or "")
                            if previous_layer
                            else None
                        ),
                    )
                    layer["signals"] = signals
                    probabilities[record_index, layer_index] = (
                        predict_correctness(
                            routers[layer_key],
                            signals,
                        )
                    )

                correctness[record_index, layer_index] = bool(
                    layer.get("correct", False)
                )
                running_tokens += float(
                    layer.get("total_tokens", 0) or 0
                )
            cumulative_tokens[record_index, layer_index] = running_tokens

    oracle_layers = np.asarray(
        [
            int(record["minimum_sufficient_layer"])
            if record.get("minimum_sufficient_layer") is not None
            else 0
            for record in validation_records
        ],
        dtype=int,
    )
    resolved = oracle_layers > 0
    if not resolved.any():
        raise ValueError(
            "Validation records contain no resolved minimum-sufficient layers."
        )

    threshold_candidates = []
    for layer_index in range(len(LAYER_KEYS)):
        layer_probabilities = probabilities[:, layer_index]
        observed = np.unique(
            layer_probabilities[np.isfinite(layer_probabilities)]
        )
        if not len(observed):
            raise ValueError(
                f"No usable validation predictions for {LAYER_KEYS[layer_index]}."
            )
        threshold_candidates.append(
            [
                0.0,
                *(
                    float(np.nextafter(value, math.inf))
                    for value in observed
                ),
            ]
        )

    l4_accuracy = float(
        correctness[:, -1].mean()
    )
    accuracy_floor = max(
        l4_accuracy,
        min_accuracy,
    )
    best: tuple[tuple[float, float, float, float], tuple[float, ...], dict[str, float]] | None = None

    for threshold_tuple in itertools.product(*threshold_candidates):
        stops = np.full(
            len(validation_records),
            len(LAYER_KEYS),
            dtype=int,
        )
        unresolved = np.ones(
            len(validation_records),
            dtype=bool,
        )

        for layer_index, threshold in enumerate(threshold_tuple):
            reached = (
                unresolved
                & np.isfinite(probabilities[:, layer_index])
                & (probabilities[:, layer_index] >= threshold)
            )
            stops[reached] = layer_index + 1
            unresolved &= ~reached

        selected_correctness = correctness[
            np.arange(len(validation_records)),
            stops - 1,
        ]
        accuracy = float(selected_correctness.mean())
        false_stop_rate = float(
            np.mean(stops[resolved] < oracle_layers[resolved])
        )
        unnecessary_rate = float(
            np.mean(stops[resolved] > oracle_layers[resolved])
        )

        if (
            false_stop_rate > max_false_stop_rate
            or accuracy + 1e-12 < accuracy_floor
        ):
            continue

        token_cost = float(
            cumulative_tokens[
                np.arange(len(validation_records)),
                stops - 1,
            ].mean()
        )
        objective = (
            token_cost,
            -accuracy,
            false_stop_rate,
            unnecessary_rate,
        )
        if best is None or objective < best[0]:
            best = (
                objective,
                threshold_tuple,
                {
                    "validation_accuracy": accuracy,
                    "false_stop_rate": false_stop_rate,
                    "unnecessary_escalation_rate": unnecessary_rate,
                    "mean_tokens": token_cost,
                    "validation_samples": float(len(validation_records)),
                    "accuracy_floor": accuracy_floor,
                },
            )

    if best is None:
        raise ValueError(
            "No validation threshold combination satisfies the false-stop "
            "limit and L4-only accuracy floor."
        )

    return {
        layer_key: {
            "threshold": float(best[1][layer_index]),
            "false_stop_rate": best[2]["false_stop_rate"],
            "unnecessary_escalation_rate": best[2][
                "unnecessary_escalation_rate"
            ],
            "validation_accuracy": best[2]["validation_accuracy"],
            "mean_tokens": best[2]["mean_tokens"],
            "validation_samples": best[2]["validation_samples"],
            "accuracy_floor": best[2]["accuracy_floor"],
        }
        for layer_index, layer_key in enumerate(LAYER_KEYS)
    }


def audit_features(
    train_records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
) -> dict[str, Any]:
    names = signal_names()
    results = {}

    for layer_key in LAYER_KEYS:
        x_train, y_train = build_training_matrix(
            train_records,
            layer_key,
        )

        validation_rows = []
        validation_labels = []
        for record in validation_records:
            layer = record.get("layers", {}).get(layer_key)
            if not layer or not str(layer.get("response") or "").strip():
                continue

            layer_number = int(layer_key.split("_")[-1])
            previous_layer = record.get("layers", {}).get(
                f"layer_{layer_number - 1}"
            )
            signals = extract_signals(
                response=str(layer.get("response") or ""),
                problem=record,
                previous_response=(
                    str(previous_layer.get("response") or "")
                    if previous_layer
                    else None
                ),
            )
            validation_rows.append(signal_vector(signals))
            validation_labels.append(
                int(bool(layer.get("correct", False)))
            )

        if not validation_rows:
            raise ValueError(
                f"No usable validation records found for {layer_key}."
            )

        x_validation = np.asarray(validation_rows, dtype=float)
        y_validation = np.asarray(validation_labels, dtype=int)
        if len(np.unique(y_train)) < 2:
            raise ValueError(
                f"{layer_key} training data contains only one correctness class."
            )

        feature_selection = select_router_feature_subset(
            train_records,
            validation_records,
            layer_key,
        )
        feature_indices = feature_selection["feature_indices"]
        hyperparameters = select_router_hyperparameters(
            train_records,
            validation_records,
            layer_key,
            feature_indices,
        )
        baseline = create_router(
            c=float(hyperparameters["regularization_c"]),
            class_weight=hyperparameters["class_weight"],
        )
        baseline.fit(x_train[:, feature_indices], y_train)
        baseline_scores = baseline.predict_proba(
            x_validation[:, feature_indices]
        )[:, 1]
        layer_results = {
            "all_features": {
                "features": names,
                "selected_features": feature_selection["features"],
                "selected_variant": feature_selection[
                    "selected_variant"
                ],
                "selected_feature_validation_brier_score": (
                    feature_selection["validation_brier_score"]
                ),
                "samples": int(len(y_validation)),
                "accuracy_at_half": float(
                    np.mean((baseline_scores >= 0.5) == y_validation)
                ),
                "brier_score": float(
                    np.mean((baseline_scores - y_validation) ** 2)
                ),
            },
            "hyperparameter_search": hyperparameters,
        }
        for variant, ablation in feature_selection["ablations"].items():
            if variant == "all_features":
                continue
            kept_features = [
                index
                for index, name in enumerate(names)
                if name in ablation["features"]
            ]
            model = create_router(
                c=float(hyperparameters["regularization_c"]),
                class_weight=hyperparameters["class_weight"],
            )
            model.fit(x_train[:, kept_features], y_train)
            scores = model.predict_proba(
                x_validation[:, kept_features]
            )[:, 1]
            layer_results[variant] = {
                "features": [names[index] for index in kept_features],
                "samples": int(len(y_validation)),
                "accuracy_at_half": float(
                    np.mean((scores >= 0.5) == y_validation)
                ),
                "brier_score": float(
                    np.mean((scores - y_validation) ** 2)
                ),
            }
        results[layer_key] = layer_results

    return results


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
    routers = {}

    for layer_key in LAYER_KEYS:
        feature_selection = select_router_feature_subset(
            train_records,
            validation_records,
            layer_key,
        )
        hyperparameters = select_router_hyperparameters(
            train_records,
            validation_records,
            layer_key,
            feature_selection["feature_indices"],
        )
        router, router_metadata = fit_router(
            train_records,
            layer_key,
            c=float(hyperparameters["regularization_c"]),
            class_weight=hyperparameters["class_weight"],
            feature_indices=feature_selection["feature_indices"],
        )
        router_metadata["selected_feature_variant"] = (
            feature_selection["selected_variant"]
        )
        router_metadata["selected_feature_validation_brier_score"] = (
            feature_selection["validation_brier_score"]
        )
        router_metadata[
            "validation_brier_score"
        ] = hyperparameters["validation_brier_score"]
        router_metadata[
            "hyperparameter_validation_samples"
        ] = hyperparameters["validation_samples"]

        joblib.dump(
            router,
            output_directory
            / f"{layer_key}.joblib",
        )

        metadata[layer_key] = (
            router_metadata
        )
        routers[layer_key] = router

    thresholds = choose_thresholds_for_cascade(
        routers,
        validation_records,
        max_false_stop_rate,
    )

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