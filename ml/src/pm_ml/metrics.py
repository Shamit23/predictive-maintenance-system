"""Evaluation metrics.

NASA score (Saxena et al., PHM08): with d = predicted - true,
    s = sum( exp(-d/13) - 1 )  if d < 0   (early prediction, milder)
        sum( exp( d/10) - 1 )  if d >= 0  (late prediction, harsher)
Lower is better. Late predictions are penalised more: a missed failure is worse than early maintenance.
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support


def mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.asarray(y_pred, float) - np.asarray(y_true, float))))


def rmse(y_true, y_pred) -> float:
    d = np.asarray(y_pred, float) - np.asarray(y_true, float)
    return float(np.sqrt(np.mean(d**2)))


def nasa_score(y_true, y_pred) -> float:
    d = np.asarray(y_pred, float) - np.asarray(y_true, float)
    return float(np.sum(np.where(d < 0, np.exp(-d / 13.0) - 1.0, np.exp(d / 10.0) - 1.0)))


def regression_report(y_true, y_pred) -> dict:
    return {
        "mae": mae(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "nasa_score": nasa_score(y_true, y_pred),
        "n": int(len(y_true)),
    }


def classification_report(y_true, y_pred) -> dict:
    """Positive class = 1 (failure within horizon). Confusion matrix layout: [[TN, FP], [FN, TP]]."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", pos_label=1, zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    return {
        "precision": float(p),
        "recall": float(r),
        "f1": float(f),
        "confusion_matrix": cm.tolist(),
        "positives": int(y_true.sum()),
        "n": int(len(y_true)),
    }
