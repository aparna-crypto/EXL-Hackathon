"""Card transaction fraud model: gradient boosting on anonymised card transactions.

Model ID: FRD-CARD-07 (no owner, purpose or validation record is kept with this code).
Usage:    python models/fraud_gbm/train_fraud.py [data_dir] [out_dir]
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

SEED = 42
data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/processed")
out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/reference/fraud_gbm")
out_dir.mkdir(parents=True, exist_ok=True)

train = pd.read_parquet(data_dir / "creditcard_train.parquet")
test = pd.read_parquet(data_dir / "creditcard_test.parquet")


def features(df: pd.DataFrame) -> pd.DataFrame:
    X = df[[f"V{i}" for i in range(1, 29)]].copy()
    X["log_amount"] = np.log1p(df["Amount"])
    X["hour"] = (df["Time"] // 3600) % 24
    return X


X_train, y_train = features(train), train["Class"]
X_test, y_test = features(test), test["Class"]

model = HistGradientBoostingClassifier(
    max_iter=200, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
    early_stopping=False, random_state=SEED,
)
model.fit(X_train, y_train)
p_train = model.predict_proba(X_train)[:, 1]
p_test = model.predict_proba(X_test)[:, 1]


def ks(y, p):
    order = np.argsort(p)
    y = np.asarray(y)[order]
    cum_bad = np.cumsum(y) / y.sum()
    cum_good = np.cumsum(1 - y) / (1 - y).sum()
    return float(np.max(np.abs(cum_bad - cum_good)))


metrics = {}
for name, y, p in [("train", y_train, p_train), ("test", y_test, p_test)]:
    auc = roc_auc_score(y, p)
    metrics[name] = {"n": int(len(y)), "fraud_rate": float(y.mean()), "auc": auc,
                     "gini": 2 * auc - 1, "ks": ks(y, p),
                     "average_precision": average_precision_score(y, p)}

pd.DataFrame({"id": test["id"], "Class": y_test, "p_fraud": p_test}).to_csv(
    out_dir / "predictions_test.csv.gz", index=False, float_format="%.10g")
(out_dir / "metrics.json").write_text(json.dumps({
    "model": "fraud_gbm", "language": "Python", "sklearn_version": sklearn.__version__,
    "seed": SEED, "features": list(X_train.columns), "params": model.get_params(),
    "metrics": metrics}, indent=2, default=str))
print(f"fraud_gbm: test AUC {metrics['test']['auc']:.4f}, "
      f"AP {metrics['test']['average_precision']:.4f}")
