from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import rankdata

TARGET = "bad"
EXCLUDED = ["id", TARGET, "personal_status_sex", "sex", "age_group", "foreign_worker"]
N_BINS = 5
IV_MIN = 0.02
PDO = 20
BASE_SCORE = 600
BASE_ODDS = 50


def is_binned_numeric(x: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(x)
                and not pd.api.types.is_bool_dtype(x)
                and x.nunique(dropna=True) > 10)


def numeric_breaks(x: pd.Series) -> list[float]:
    vals = np.asarray(x, dtype=float)
    q = np.quantile(vals, np.linspace(0, 1, N_BINS + 1), method="linear")
    br = []
    for v in q:
        if not br or v != br[-1]:
            br.append(float(v))
    br[0] = -math.inf
    br[-1] = math.inf
    return br


def _fmt_num(v: float, digits: int = 10) -> str:
    """Format like R's formatC(x, digits=, width=1): %g style; Inf gets a leading space."""
    if v == math.inf:
        return " Inf"
    if v == -math.inf:
        return "-Inf"
    return "%.*g" % (digits, v)


def _cut_labels(breaks: list[float], dig_lab: int = 10) -> list[str]:
    nb = len(breaks)
    ch = [_fmt_num(b, dig_lab) for b in breaks]
    for dig in range(dig_lab, max(12, dig_lab) + 1):
        ch = [_fmt_num(b, dig) for b in breaks]
        if all(ch[i] != ch[i + 1] for i in range(nb - 1)):
            break
    labels = []
    for i in range(nb - 1):
        labels.append("[" + ch[i] + "," + ch[i + 1] + "]" if i == 0
                      else "(" + ch[i] + "," + ch[i + 1] + "]")
    return labels


def assign_bin(x: pd.Series, breaks):
    if breaks is None:
        if pd.api.types.is_bool_dtype(x):
            return x.map(lambda v: "TRUE" if v else "FALSE").astype(object)
        if pd.api.types.is_float_dtype(x):
            return x.map(lambda v: "%.15g" % v if pd.notna(v) else None).astype(object)
        return x.astype(str).astype(object)
    br = np.asarray(breaks, dtype=float)
    labels = _cut_labels(list(br))
    vals = np.asarray(x, dtype=float)
    out = np.empty(len(vals), dtype=object)
    nb = len(br) - 1
    idx = np.searchsorted(br, vals, side="left") - 1
    for i, v in enumerate(vals):
        if np.isnan(v):
            out[i] = None
            continue
        k = idx[i]
        if v == br[0]:
            k = 0
        if k < 0 or k >= nb:
            out[i] = None
        else:
            out[i] = labels[k]
    return pd.Series(out, index=x.index, dtype=object)


def woe_table(bin: pd.Series, y: pd.Series) -> pd.DataFrame:
    b = np.asarray(bin, dtype=object)
    yy = np.asarray(y)
    total_good = float(np.sum(yy == 0))
    total_bad = float(np.sum(yy == 1))
    levels = sorted(set(v for v in b if v is not None))
    n_good = np.array([int(np.sum((b == lv) & (yy == 0))) for lv in levels], dtype=float)
    n_bad = np.array([int(np.sum((b == lv) & (yy == 1))) for lv in levels], dtype=float)
    dist_good = (n_good + 0.5) / total_good
    dist_bad = (n_bad + 0.5) / total_bad
    woe = np.log(dist_good / dist_bad)
    return pd.DataFrame({
        "bin": levels,
        "n_good": n_good.astype(int),
        "n_bad": n_bad.astype(int),
        "woe": woe,
        "iv_part": (dist_good - dist_bad) * woe,
    })


def apply_woe(bin: pd.Series, table: pd.DataFrame) -> pd.Series:
    mapping = dict(zip(table["bin"], table["woe"]))
    vals = [mapping.get(v, 0.0) if v is not None else 0.0 for v in bin]
    return pd.Series(np.asarray(vals, dtype=float), index=bin.index)


_FACTOR = PDO / math.log(2)
_OFFSET = BASE_SCORE - _FACTOR * math.log(BASE_ODDS)


def to_score(pd) -> np.ndarray:  # noqa: A002
    p = np.asarray(pd, dtype=float)
    return _OFFSET + _FACTOR * np.log((1 - p) / p)


def auc(y, p) -> float:
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    r = rankdata(p, method="average")
    n1 = float(np.sum(y == 1))
    n0 = float(np.sum(y == 0))
    return float((np.sum(r[y == 1]) - n1 * (n1 + 1) / 2) / (n1 * n0))


def ks(y, p) -> float:
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    p1 = np.sort(p[y == 1])
    p0 = np.sort(p[y == 0])
    thr = np.unique(p)
    c1 = np.searchsorted(p1, thr, side="right") / len(p1)
    c0 = np.searchsorted(p0, thr, side="right") / len(p0)
    return float(np.max(np.abs(c1 - c0)))


def _num(v):
    if isinstance(v, (float, np.floating)):
        if v == math.inf:
            return "Inf"
        if v == -math.inf:
            return "-Inf"
        f = float(v)
        if f == int(f) and abs(f) < 1e15:
            return int(f)
        return f
    return v


def main(data_dir: str = "data/processed",
         out_dir: str = "artifacts/redevelop/credit_scorecard/outputs") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train = pd.read_csv(Path(data_dir) / "german_credit_train.csv")
    test = pd.read_csv(Path(data_dir) / "german_credit_test.csv")

    candidates = [c for c in train.columns if c not in EXCLUDED]
    breaks: dict = {}
    tables: dict = {}
    iv: dict = {}
    for v in candidates:
        breaks[v] = numeric_breaks(train[v]) if is_binned_numeric(train[v]) else None
        tables[v] = woe_table(assign_bin(train[v], breaks[v]), train[TARGET])
        iv[v] = float(tables[v]["iv_part"].sum())
    selected = [v for v in candidates if iv[v] >= IV_MIN]

    def woe_frame(df: pd.DataFrame) -> pd.DataFrame:
        o = pd.DataFrame(index=range(len(df)))
        for v in selected:
            w = apply_woe(assign_bin(df[v], breaks[v]), tables[v])
            o["woe_" + v] = w.to_numpy()
        return o

    X_train = woe_frame(train)
    X_test = woe_frame(test)
    y_train = train[TARGET].to_numpy()
    y_test = test[TARGET].to_numpy()

    Xd = sm.add_constant(X_train, has_constant="add")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = sm.GLM(y_train, Xd, family=sm.families.Binomial()).fit()

    pd_train = np.asarray(fit.predict(Xd), dtype=float)
    pd_test = np.asarray(fit.predict(sm.add_constant(X_test, has_constant="add")[Xd.columns]),
                         dtype=float)

    metrics = {}
    for s, y, p in (("train", y_train, pd_train), ("test", y_test, pd_test)):
        a = auc(y, p)
        metrics[s] = {"n": int(len(y)), "bad_rate": float(np.mean(y)), "auc": a,
                      "gini": 2 * a - 1, "ks": ks(y, p)}

    rows = []
    for v in candidates:
        t = tables[v]
        d = pd.DataFrame({"variable": v, "bin": t["bin"], "n_good": t["n_good"],
                          "n_bad": t["n_bad"], "woe": t["woe"],
                          "iv_variable": iv[v], "selected": v in selected})
        rows.append(d)
    bins_out = pd.concat(rows, ignore_index=True)
    bins_out.to_csv(out / "bins.csv", index=False)

    terms = ["(Intercept)"] + ["woe_" + v for v in selected]
    params = np.asarray(fit.params)
    bse = np.asarray(fit.bse)
    pd.DataFrame({"term": terms, "estimate": params, "std_error": bse}).to_csv(
        out / "coefficients.csv", index=False)

    pd.DataFrame({"id": test["id"], "bad": y_test, "pd": pd_test,
                  "score": to_score(pd_test)}).to_csv(out / "predictions_test.csv", index=False)

    meta = {
        "model": "credit_scorecard", "language": "python",
        "seed": 42, "n_bins": N_BINS, "iv_min": IV_MIN, "pdo": PDO,
        "base_score": BASE_SCORE, "base_odds": BASE_ODDS,
        "selected_variables": selected,
        "breaks": {v: [_num(b) for b in breaks[v]] for v in candidates
                   if breaks[v] is not None},
        "metrics": metrics,
    }
    with open(out / "metrics.json", "w") as f:
        json.dump(meta, f, indent=1)

    print("credit_scorecard: %d variables selected; test AUC %.4f, Gini %.4f, KS %.4f" % (
        len(selected), metrics["test"]["auc"], metrics["test"]["gini"], metrics["test"]["ks"]))


if __name__ == "__main__":
    main(*sys.argv[1:3])
