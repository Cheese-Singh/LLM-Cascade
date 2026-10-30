from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

from phase4.config import ROUTERS_DIR, MAX_FALSE_STOP_RATE
from phase4.signals import signal_vector


def extract_layer_data(record: Dict[str, Any], layer_key: str) -> Tuple[Optional[List[float]], Optional[int]]:
    """
    Extracts ordered feature vector and binary ground-truth pass label.
    Supports both genuine dataset records (r["tier_evaluations"]) and legacy records (r["layers"]).
    """
    if "tier_evaluations" in record:
        te = record.get("tier_evaluations", {}).get(layer_key, {})
        vec = te.get("runtime_feature_vector")
        if vec is None and "runtime_features" in te:
            vec = signal_vector(te["runtime_features"])
        passed = te.get("offline_overall_pass")
        if passed is not None and vec is not None:
            return vec, 1 if passed else 0

    if "layers" in record:
        l_data = record.get("layers", {}).get(layer_key, {})
        signals = l_data.get("signals")
        passed = l_data.get("passed")
        if signals and passed is not None:
            return signal_vector(signals), 1 if passed else 0

    return None, None


def calibrate_layer_threshold(
    model: Any,
    val_X: List[List[float]],
    val_y: List[int],
    max_fsr: float = MAX_FALSE_STOP_RATE,
    default_threshold: float = 0.85,
) -> float:
    """
    Calibrate stopping probability threshold on validation data subject to FSR <= max_fsr constraint.
    Searches from lenient (0.50) to strict (0.99) to select the lowest threshold satisfying the safety budget.
    """
    if not val_X or not val_y:
        return default_threshold

    try:
        probs = model.predict_proba(val_X)[:, 1]
    except Exception:
        return default_threshold

    best_thresh = 0.95
    for thresh in np.linspace(0.50, 0.99, 50):
        preds = (probs >= thresh).astype(int)
        false_stops = sum(1 for p, y in zip(preds, val_y) if p == 1 and y == 0)
        total_stops = sum(preds)
        if total_stops == 0:
            best_thresh = float(thresh)
            break
        fsr = false_stops / float(total_stops)
        if fsr <= max_fsr:
            best_thresh = float(thresh)
            break

    return best_thresh


class Phase4CascadeRouter:
    """Logistic-regression router with calibrated stopping thresholds."""

    def __init__(self, routers_dir: Path = ROUTERS_DIR):
        self.routers_dir = routers_dir
        self.models: Dict[str, Pipeline] = {}
        self.thresholds: Dict[str, float] = {
            "layer_1": 0.85,
            "layer_2": 0.80,
            "layer_3": 0.75,
            "layer_4": 0.00,  # Layer 4 always stops
        }
        self.load()

    def train_on_data(
        self,
        training_records: List[Dict[str, Any]],
        validation_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Train per-layer logistic regression models and calibrate thresholds."""
        self.routers_dir.mkdir(parents=True, exist_ok=True)
        summary: Dict[str, Any] = {}

        for layer_key in ["layer_1", "layer_2", "layer_3"]:
            X_train, y_train = [], []
            for r in training_records:
                vec, passed = extract_layer_data(r, layer_key)
                if vec is not None and passed is not None:
                    X_train.append(vec)
                    y_train.append(passed)

            if not X_train:
                continue

            clf = Pipeline([
                ("scaler", StandardScaler()),
                ("logreg", LogisticRegression(C=1.0, random_state=42, max_iter=200, class_weight="balanced"))
            ])

            if len(set(y_train)) < 2:
                single_class = y_train[0]
                centroid = np.mean(X_train, axis=0)
                X_train_adj = list(X_train) + [list(centroid - 0.5)]
                y_train_adj = list(y_train) + [0 if single_class == 1 else 1]
                clf.fit(X_train_adj, y_train_adj)
            else:
                clf.fit(X_train, y_train)

            self.models[layer_key] = clf
            joblib.dump(clf, self.routers_dir / f"{layer_key}_router.joblib")

            val_X, val_y = [], []
            for r in validation_records:
                vec, passed = extract_layer_data(r, layer_key)
                if vec is not None and passed is not None:
                    val_X.append(vec)
                    val_y.append(passed)

            best_threshold = calibrate_layer_threshold(
                clf, val_X, val_y, max_fsr=MAX_FALSE_STOP_RATE, default_threshold=self.thresholds.get(layer_key, 0.85)
            )
            self.thresholds[layer_key] = best_threshold
            summary[layer_key] = {
                "threshold": best_threshold,
                "train_samples": len(X_train),
                "val_samples": len(val_X),
                "train_positive_rate": float(np.mean(y_train)) if y_train else 0.0,
            }

        # Save thresholds
        (self.routers_dir / "thresholds.json").write_text(json.dumps(self.thresholds, indent=2))
        return summary

    def should_stop(
        self,
        layer_key: str,
        signals: Dict[str, float],
        runtime_verifier_result: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, float]:
        """Decide whether to stop at this layer or escalate."""
        if layer_key == "layer_4":
            return True, 1.0

        model = self.models.get(layer_key)
        vec = [signal_vector(signals)]

        prob = None
        if model is not None:
            try:
                estimator = model.steps[-1][1] if hasattr(model, "steps") else model
                if not hasattr(estimator, "multi_class"):
                    estimator.multi_class = "auto"
                prob = float(model.predict_proba(vec)[0, 1])
            except Exception:
                prob = None

        if prob is None:
            conf = signals.get("stated_confidence", 0.5)
            hedg = signals.get("hedging_score", 0.5)
            prob = max(0.0, min(1.0, conf - (hedg * 0.3)))

        threshold = self.thresholds.get(layer_key, 0.85)
        stop = (prob >= threshold)
        return stop, prob

    def load(self) -> None:
        """Load trained models and thresholds from disk if available."""
        if not self.routers_dir.exists():
            return

        thresh_path = self.routers_dir / "thresholds.json"
        if thresh_path.exists():
            try:
                self.thresholds = json.loads(thresh_path.read_text())
            except Exception:
                pass

        for layer_key in ["layer_1", "layer_2", "layer_3"]:
            joblib_path = self.routers_dir / f"{layer_key}_router.joblib"
            if joblib_path.exists():
                try:
                    self.models[layer_key] = joblib.load(joblib_path)
                except Exception:
                    pass


class Phase4DecisionTreeRouter:
    """Interpretable shallow decision-tree router with calibrated stopping thresholds."""

    def __init__(self, routers_dir: Path = ROUTERS_DIR, max_depth: int = 3):
        if routers_dir.name != "decision_tree":
            self.routers_dir = routers_dir / "decision_tree"
        else:
            self.routers_dir = routers_dir
        self.max_depth = max_depth
        self.models: Dict[str, DecisionTreeClassifier] = {}
        self.thresholds: Dict[str, float] = {
            "layer_1": 0.85,
            "layer_2": 0.80,
            "layer_3": 0.75,
            "layer_4": 0.00,
        }
        self.rules: Dict[str, str] = {}
        self.load()

    def train_on_data(
        self,
        training_records: List[Dict[str, Any]],
        validation_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Train per-layer decision trees and extract human-readable rules."""
        self.routers_dir.mkdir(parents=True, exist_ok=True)
        summary: Dict[str, Any] = {}

        feature_names = [
            "stated_confidence",
            "hedging_score",
            "audit_kw_density",
            "numeric_density",
            "citation_score",
            "length_log",
            "disagreement_with_prior",
            "abstention_flag",
        ]

        for layer_key in ["layer_1", "layer_2", "layer_3"]:
            X_train, y_train = [], []
            for r in training_records:
                vec, passed = extract_layer_data(r, layer_key)
                if vec is not None and passed is not None:
                    X_train.append(vec)
                    y_train.append(passed)

            if not X_train:
                continue

            dt = DecisionTreeClassifier(
                max_depth=self.max_depth,
                class_weight="balanced",
                random_state=42,
            )

            if len(set(y_train)) < 2:
                single_class = y_train[0]
                centroid = np.mean(X_train, axis=0)
                X_train_adj = list(X_train) + [list(centroid - 0.5)]
                y_train_adj = list(y_train) + [0 if single_class == 1 else 1]
                dt.fit(X_train_adj, y_train_adj)
            else:
                dt.fit(X_train, y_train)

            self.models[layer_key] = dt
            joblib.dump(dt, self.routers_dir / f"{layer_key}_tree.joblib")

            tree_text = export_text(dt, feature_names=feature_names)
            self.rules[layer_key] = tree_text
            (self.routers_dir / f"{layer_key}_rules.txt").write_text(tree_text)

            val_X, val_y = [], []
            for r in validation_records:
                vec, passed = extract_layer_data(r, layer_key)
                if vec is not None and passed is not None:
                    val_X.append(vec)
                    val_y.append(passed)

            best_threshold = calibrate_layer_threshold(
                dt, val_X, val_y, max_fsr=MAX_FALSE_STOP_RATE, default_threshold=self.thresholds.get(layer_key, 0.85)
            )
            self.thresholds[layer_key] = best_threshold
            summary[layer_key] = {
                "threshold": best_threshold,
                "train_samples": len(X_train),
                "val_samples": len(val_X),
                "rules": tree_text,
            }

        (self.routers_dir / "thresholds.json").write_text(json.dumps(self.thresholds, indent=2))
        return summary

    def should_stop(
        self,
        layer_key: str,
        signals: Dict[str, float],
        runtime_verifier_result: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, float]:
        """Decide whether to stop at this layer or escalate based on decision tree."""
        if layer_key == "layer_4":
            return True, 1.0

        model = self.models.get(layer_key)
        vec = [signal_vector(signals)]

        prob = None
        if model is not None:
            try:
                prob = float(model.predict_proba(vec)[0, 1])
            except Exception:
                prob = None

        if prob is None:
            conf = signals.get("stated_confidence", 0.5)
            prob = conf

        threshold = self.thresholds.get(layer_key, 0.85)
        stop = (prob >= threshold)
        return stop, prob

    def load(self) -> None:
        """Load trained models from disk if available."""
        if not self.routers_dir.exists():
            return

        thresh_path = self.routers_dir / "thresholds.json"
        if thresh_path.exists():
            try:
                self.thresholds = json.loads(thresh_path.read_text())
            except Exception:
                pass

        for layer_key in ["layer_1", "layer_2", "layer_3"]:
            joblib_path = self.routers_dir / f"{layer_key}_tree.joblib"
            if joblib_path.exists():
                try:
                    self.models[layer_key] = joblib.load(joblib_path)
                except Exception:
                    pass


class Phase4HybridRouter:
    """
    Hybrid Cascade Router combining deterministic runtime verifier with calibrated ML router.
    Hard gate: If runtime verifier fails (invalid citations, ungrounded numbers, etc.), forces escalation.
    Soft gate: If runtime verifier passes, evaluates ML router probability against calibrated threshold.
    """

    def __init__(self, ml_router: Any):
        self.ml_router = ml_router

    def should_stop(
        self,
        layer_key: str,
        signals: Dict[str, float],
        runtime_verifier_result: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, float]:
        if layer_key == "layer_4":
            return True, 1.0

        if runtime_verifier_result is not None:
            if not runtime_verifier_result.get("runtime_pass", True):
                return False, 0.0

        return self.ml_router.should_stop(layer_key, signals)

