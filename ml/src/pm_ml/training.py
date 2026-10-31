"""Reproducible training + evaluation pipeline.

    load -> validate -> engine-level split -> labels -> features -> baselines + XGBoost
         -> evaluate (validation + NASA test protocol) -> save artifact

Run:  python -m pm_ml.training            (from the repo root, venv active)
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import DEFAULT_RAW_DIR, file_sha256, load_fd001
from .features import FeatureConfig, build_features, feature_names
from .labels import FAILURE_HORIZON, RUL_CAP, add_rul, cap_rul, failure_within
from .metrics import classification_report, regression_report

DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "artifacts"

REG_PARAMS = dict(
    n_estimators=300, learning_rate=0.05, max_depth=4, subsample=0.8,
    colsample_bytree=0.8, min_child_weight=5, tree_method="hist", n_jobs=1,
)
CLF_PARAMS = dict(REG_PARAMS)


def split_engines(units, val_fraction: float, seed: int) -> tuple[list[int], list[int]]:
    """Split by ENGINE, never by row, so no engine contributes to both sides."""
    units = np.array(sorted(units))
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(units)
    n_val = max(1, int(round(len(units) * val_fraction)))
    return sorted(shuffled[n_val:].tolist()), sorted(shuffled[:n_val].tolist())


def last_rows(df: pd.DataFrame) -> pd.Index:
    return df.groupby("unit")["cycle"].idxmax().to_numpy()


def train_and_evaluate(
    raw_dir=DEFAULT_RAW_DIR, seed: int = 42, val_fraction: float = 0.2,
    cap: int = RUL_CAP, horizon: int = FAILURE_HORIZON,
    cfg: FeatureConfig = FeatureConfig(),
) -> dict:
    train_df, test_df, test_rul = load_fd001(raw_dir)
    train_df = add_rul(train_df)
    train_df["rul_target"] = cap_rul(train_df["rul"], cap)
    train_df["fail_label"] = failure_within(train_df["rul"], horizon)

    tr_units, va_units = split_engines(train_df["unit"].unique(), val_fraction, seed)
    assert not set(tr_units) & set(va_units)
    tr = train_df[train_df["unit"].isin(tr_units)]
    va = train_df[train_df["unit"].isin(va_units)]

    X_tr, X_va = build_features(tr, cfg), build_features(va, cfg)
    X_te_all = build_features(test_df, cfg)
    te_idx = last_rows(test_df)
    X_te = X_te_all.loc[te_idx]
    te_units = test_df.loc[te_idx, "unit"].to_numpy()
    y_te = test_rul.loc[te_units].to_numpy()  # true RUL after last observed cycle
    y_te_capped = np.minimum(y_te, cap)
    y_te_fail = failure_within(pd.Series(y_te), horizon).to_numpy()

    # --- models ---------------------------------------------------------------------------
    const_value = float(tr["rul_target"].mean())  # baseline 1: constant
    raw_cols = list(cfg.sensors)
    ridge = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(tr[raw_cols], tr["rul_target"])  # baseline 2

    reg = xgb.XGBRegressor(random_state=seed, **REG_PARAMS).fit(X_tr, tr["rul_target"])
    clf = xgb.XGBClassifier(random_state=seed, **CLF_PARAMS).fit(X_tr, tr["fail_label"])

    def clipped(p):
        return np.clip(p, 0, cap)

    va_last = last_rows(va)
    metrics = {
        "validation_all_cycles": {  # vs capped target, held-out engines
            "baseline_constant": regression_report(va["rul_target"], np.full(len(va), const_value)),
            "baseline_ridge": regression_report(va["rul_target"], clipped(ridge.predict(va[raw_cols]))),
            "xgboost": regression_report(va["rul_target"], clipped(reg.predict(X_va))),
        },
        "test_last_cycle_vs_true_rul": {  # NASA protocol, 100 test engines
            "baseline_constant": regression_report(y_te, np.full(len(y_te), const_value)),
            "baseline_ridge": regression_report(y_te, clipped(ridge.predict(test_df.loc[te_idx, raw_cols]))),
            "xgboost": regression_report(y_te, clipped(reg.predict(X_te))),
        },
        "test_last_cycle_vs_capped_rul": {
            "xgboost": regression_report(y_te_capped, clipped(reg.predict(X_te))),
        },
        "classification_validation_all_cycles": {
            "xgb_classifier": classification_report(va["fail_label"], clf.predict(X_va)),
            "from_regressor_threshold": classification_report(
                va["fail_label"], (clipped(reg.predict(X_va)) <= horizon).astype(int)),
        },
        "classification_test_last_cycle": {
            "xgb_classifier": classification_report(y_te_fail, clf.predict(X_te)),
            "from_regressor_threshold": classification_report(
                y_te_fail, (clipped(reg.predict(X_te)) <= horizon).astype(int)),
        },
    }
    # Validation on the final recorded cycle of each val engine (all RUL=0) is reported for reference only.
    metrics["validation_final_cycle_only"] = {
        "xgboost": regression_report(va.loc[va_last, "rul"], clipped(reg.predict(X_va.loc[va_last])))
    }

    raw = Path(raw_dir)
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "NASA C-MAPSS FD001",
        "dataset_files_sha256": {n: file_sha256(raw / n) for n in
                                 ("train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt")},
        "seed": seed, "val_fraction": val_fraction,
        "rul_cap": cap, "failure_horizon": horizon,
        "train_units": tr_units, "validation_units": va_units,
        "n_train_rows": int(len(tr)), "n_validation_rows": int(len(va)),
        "feature_config": cfg.to_dict(), "feature_names": feature_names(cfg),
        "regressor_params": REG_PARAMS, "classifier_params": CLF_PARAMS,
        "baseline_constant_value": const_value,
        "metrics": metrics,
        "libraries": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                      "scikit-learn": sklearn.__version__, "xgboost": xgb.__version__},
        "note": "failure classifier outputs an UNCALIBRATED score; test set evaluated once, not used for tuning.",
    }
    return {"regressor": reg, "classifier": clf, "metadata": metadata}


def save_artifact(result: dict, root: Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    meta = dict(result["metadata"])
    stamp = datetime.fromisoformat(meta["created_utc"]).strftime("%Y%m%dT%H%M%SZ")
    meta["version"] = f"fd001-xgb-{stamp}"
    d = Path(root) / meta["version"]
    d.mkdir(parents=True, exist_ok=False)  # never overwrite an existing version
    result["regressor"].save_model(d / "regressor.json")
    result["classifier"].save_model(d / "classifier.json")
    (d / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    ap.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    res = train_and_evaluate(a.raw_dir, seed=a.seed)
    d = save_artifact(res, Path(a.artifact_root))
    print(json.dumps(res["metadata"]["metrics"], indent=2))
    print(f"\nSaved artifact: {d}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
