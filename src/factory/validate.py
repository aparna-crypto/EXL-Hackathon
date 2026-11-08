"""Stage 5 - Validate: independent validation evidence, RAG ratings and an HTML/PDF report.

Every number is computed here, deterministically, from the model's outputs and the data. The
report agent only writes the narrative: it refers to numbers through [[fact]] placeholders and
cannot change a rating. Thresholds live in config (validate.thresholds).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fairlearn.metrics import (  # noqa: E402
    MetricFrame,
    equalized_odds_difference,
    false_positive_rate,
    selection_rate,
    true_positive_rate,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from scipy import stats  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
)

from factory import discover, model_card  # noqa: E402
from factory.agents import AgentError  # noqa: E402
from factory.agents import report as report_agent  # noqa: E402

STAGE = "validate"
SECTIONS = ["performance", "calibration", "stability", "fairness"]
RAG_ORDER = {"green": 0, "amber": 1, "red": 2}
OUTCOME = {"green": "Fit for use.",
           "amber": "Fit for use with conditions: remediate the amber findings.",
           "red": "Not fit for use until the red findings are remediated."}


# ---- statistics ----------------------------------------------------------------------------

def auc(y, p) -> float:
    return float(roc_auc_score(y, p))


def ks(y, p) -> float:
    y, p = np.asarray(y), np.asarray(p, float)
    return float(stats.ks_2samp(p[y == 1], p[y == 0]).statistic)


def auc_ci(y, p, n: int, seed: int) -> tuple[float, float]:
    """Percentile bootstrap 95% CI for the AUC."""
    y, p = np.asarray(y), np.asarray(p, float)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            vals.append(roc_auc_score(y[i], p[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def quantile_groups(p, bins: int) -> np.ndarray:
    """Equal-count groups 1..bins by predicted value (ties broken by row order)."""
    return pd.qcut(pd.Series(np.asarray(p, float)).rank(method="first"), bins,
                   labels=False).to_numpy() + 1


def calibration_table(y, p, bins: int) -> pd.DataFrame:
    d = pd.DataFrame({"group": quantile_groups(p, bins), "y": np.asarray(y), "p": np.asarray(p)})
    return d.groupby("group").agg(n=("y", "size"), observed=("y", "sum"), expected=("p", "sum"),
                                  mean_predicted=("p", "mean"),
                                  observed_rate=("y", "mean")).reset_index()


def hosmer_lemeshow(y, p, bins: int) -> tuple[float, float, pd.DataFrame]:
    t = calibration_table(y, p, bins)
    chi = float((((t.observed - t.expected) ** 2) / (t.expected * (1 - t.expected / t.n))).sum())
    return chi, float(stats.chi2.sf(chi, len(t) - 2)), t


def psi(expected, actual, bins: int = 10, eps: float = 1e-4) -> tuple[float, pd.DataFrame]:
    """Population stability index of `actual` against `expected` (quantile bins of expected)."""
    e, a = pd.Series(expected).reset_index(drop=True), pd.Series(actual).reset_index(drop=True)
    if pd.api.types.is_numeric_dtype(e) and e.nunique() > bins:
        edges = np.unique(np.quantile(e.astype(float), np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        ec = pd.cut(e.astype(float), edges, include_lowest=True)
        cats = [str(c) for c in ec.cat.categories]
        eb = ec.astype(str)
        ab = pd.cut(a.astype(float), edges, include_lowest=True).astype(str)
    else:
        eb, ab = e.astype(str), a.astype(str)
        cats = sorted(set(eb) | set(ab))
    pe = eb.value_counts(normalize=True).reindex(cats, fill_value=0).clip(lower=eps)
    pa = ab.value_counts(normalize=True).reindex(cats, fill_value=0).clip(lower=eps)
    contrib = (pa - pe) * np.log(pa / pe)
    table = pd.DataFrame({"bin": cats, "expected_pct": pe.to_numpy(), "actual_pct": pa.to_numpy(),
                          "psi": contrib.to_numpy()})
    return float(contrib.sum()), table


def poisson_deviance(y, mu) -> float:
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * np.sum(term - (y - mu)) / len(y))


def lorenz_gini(claims, expected, exposure) -> tuple[float, np.ndarray, np.ndarray]:
    """Gini of the ordered Lorenz curve: policies sorted by predicted frequency, riskiest first."""
    claims, expected, exposure = (np.asarray(v, float) for v in (claims, expected, exposure))
    order = np.argsort(-(expected / exposure), kind="stable")
    x = np.concatenate([[0], np.cumsum(exposure[order]) / exposure.sum()])
    y = np.concatenate([[0], np.cumsum(claims[order]) / claims.sum()])
    return float(2 * np.trapz(y, x) - 1), x, y


def segment_ae(df: pd.DataFrame, by: str) -> pd.DataFrame:
    t = df.groupby(by, observed=True).agg(policies=("ClaimNb", "size"),
                                          exposure=("Exposure", "sum"),
                                          actual=("ClaimNb", "sum"),
                                          expected=("expected_claims", "sum")).reset_index()
    t["ae"] = t.actual / t.expected
    return t.rename(columns={by: "segment"})


# ---- ratings -------------------------------------------------------------------------------

def rag(value, limits, higher_is_better: bool) -> str:
    g, a = limits
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "amber"
    if higher_is_better:
        return "green" if value >= g else "amber" if value >= a else "red"
    return "green" if value <= g else "amber" if value <= a else "red"


def worst(rags) -> str:
    rags = [r for r in rags if r in RAG_ORDER]
    return max(rags, key=RAG_ORDER.get) if rags else "n/a"


class Evidence:
    """Collects metrics, checks, tables, notes and charts per section."""

    def __init__(self, th: dict, out_dir: Path) -> None:
        self.th, self.out_dir = th, out_dir
        self.metrics = {s: {} for s in SECTIONS}
        self.tables = {s: [] for s in SECTIONS}
        self.notes = {s: [] for s in SECTIONS}
        self.charts = {s: [] for s in SECTIONS}
        self.checks: list[dict] = []
        self.na: dict[str, str] = {}
        self.model_under_test = ""
        self.data = ""

    def metric(self, section: str, key: str, value) -> None:
        self.metrics[section][key] = value.item() if hasattr(value, "item") else value

    def check(self, section: str, key: str, label: str, limits_key: str, higher: bool,
              note: str = "") -> None:
        lim = self.th[limits_key]
        rule = (f">= {lim[0]} green, >= {lim[1]} amber" if higher
                else f"<= {lim[0]} green, <= {lim[1]} amber")
        value = self.metrics[section][key]
        self.checks.append({"section": section, "check": label, "metric": f"{section}.{key}",
                            "value": value, "rule": rule, "rag": rag(value, lim, higher),
                            "note": note})

    def table(self, section: str, title: str, df: pd.DataFrame) -> None:
        self.tables[section].append((title, df))

    def chart(self, section: str, name: str, fig) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{name}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
        self.charts[section].append((name.replace("_", " "), path))


def fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    v = float(v)
    if math.isnan(v):
        return "n/a"
    if v == 0:
        return "0"
    if abs(v) < 0.001:
        return f"{v:.2e}"
    return f"{v:.3f}" if abs(v) < 10 else f"{v:,.1f}"


# ---- charts --------------------------------------------------------------------------------

def _roc_chart(ev: Evidence, curves: dict) -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    for label, (y, p) in curves.items():
        fpr, tpr, _ = roc_curve(y, p)
        ax.plot(fpr, tpr, label=f"{label} (AUC {auc(y, p):.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    ax.set(xlabel="False positive rate", ylabel="True positive rate", title="ROC curve")
    ax.legend()
    ev.chart("performance", "roc_curve", fig)


def _calibration_chart(ev: Evidence, t: pd.DataFrame, x="mean_predicted", y="observed_rate",
                       title="Calibration by decile of predicted value") -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(t[x], t[y], "o-", label="observed")
    lim = [0, max(t[x].max(), t[y].max()) * 1.05]
    ax.plot(lim, lim, "k--", lw=0.8, label="perfect")
    ax.set(xlabel="mean predicted", ylabel="observed", title=title)
    ax.legend()
    ev.chart("calibration", "calibration", fig)


def _psi_chart(ev: Evidence, psis: dict[str, float], title: str) -> None:
    fig, ax = plt.subplots(figsize=(5, max(3, 0.22 * len(psis) + 1)))
    names = list(psis)
    vals = [psis[n] for n in names]
    g, a = ev.th["psi_max"]
    ax.barh(names, vals, color=["#2f8f46" if v <= g else "#d98b00" if v <= a else "#c62828"
                                for v in vals])
    ax.axvline(g, color="k", ls="--", lw=0.8)
    ax.axvline(a, color="k", ls=":", lw=0.8)
    ax.invert_yaxis()
    ax.set(xlabel="PSI", title=title)
    ev.chart("stability", "psi_by_variable", fig)


# ---- evidence builders ---------------------------------------------------------------------

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("validated_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _score_scorecard(mod, df: pd.DataFrame, out: Path):
    """Score rows with the rebuild's own bins, WoE table and coefficients."""
    meta = json.loads((out / "metrics.json").read_text())
    bins = pd.read_csv(out / "bins.csv", dtype={"bin": str})
    co = pd.read_csv(out / "coefficients.csv")
    lin = np.full(len(df), float(co.estimate.iloc[0]))
    binned = {}
    for term, est in zip(co.term.iloc[1:], co.estimate.iloc[1:]):
        v = term.removeprefix("woe_")
        br = meta["breaks"].get(v)
        b = pd.Series(mod.assign_bin(df[v].reset_index(drop=True),
                                     [float(x) for x in br] if br else None)).astype(str)
        binned[v] = b.to_numpy()
        table = bins[bins.variable == v][["bin", "woe"]].reset_index(drop=True)
        lin += float(est) * np.asarray(mod.apply_woe(b, table), float)
    p = 1 / (1 + np.exp(-lin))
    return p, np.asarray(mod.to_score(p), float), pd.DataFrame(binned)


def _binary_performance(ev: Evidence, y_te, p_te, train_auc: float, vcfg: dict, seed: int) -> None:
    lo, hi = auc_ci(y_te, p_te, vcfg["bootstrap_samples"], seed)
    m = {"test_auc": auc(y_te, p_te), "train_auc": train_auc}
    m.update(auc_drop=m["train_auc"] - m["test_auc"], test_auc_ci_low=lo, test_auc_ci_high=hi,
             test_gini=2 * m["test_auc"] - 1, test_ks=ks(y_te, p_te), test_n=int(len(y_te)),
             test_event_rate=float(np.mean(y_te)))
    for k, v in m.items():
        ev.metric("performance", k, v)
    ev.check("performance", "test_auc", "Discrimination: test AUC", "auc_min", True,
             f"95% bootstrap CI {lo:.3f}-{hi:.3f}")
    ev.check("performance", "auc_drop", "Overfitting: train AUC - test AUC", "auc_drop_max", False)


def _binary_calibration(ev: Evidence, y, p, bins: int, hl_rated: bool) -> pd.DataFrame:
    chi, pval, t = hosmer_lemeshow(y, p, bins)
    for k, v in {"mean_predicted": float(np.mean(p)), "observed_rate": float(np.mean(y)),
                 "brier_score": float(brier_score_loss(y, p)), "hl_statistic": chi,
                 "hl_pvalue": pval}.items():
        ev.metric("calibration", k, v)
    ev.metric("calibration", "calib_ratio_dev",
              abs(ev.metrics["calibration"]["mean_predicted"]
                  / ev.metrics["calibration"]["observed_rate"] - 1))
    ev.check("calibration", "calib_ratio_dev", "Calibration in the large: |mean predicted / "
             "observed - 1|", "calib_ratio_dev_max", False)
    if hl_rated:
        ev.check("calibration", "hl_pvalue", "Hosmer-Lemeshow goodness of fit (p-value)",
                 "hl_pvalue_min", True, f"{bins} equal-count groups, {bins - 2} d.f.")
    else:
        ev.notes["calibration"].append(
            f"Hosmer-Lemeshow is reported but not rated: with n={len(y):,} it rejects even "
            "negligible miscalibration.")
    ev.table("calibration", "Calibration by decile of predicted value", t)
    _calibration_chart(ev, t)
    return t


def scorecard_rebuild(card: dict, root: Path, ev: Evidence, vcfg: dict, seed: int,
                      test_path: Path | None = None) -> None:
    red = card.get("redevelopment") or {}
    if not red.get("passed"):
        raise AgentError("validation needs an approved Stage 4 rebuild with proven parity")
    module_path = root / red["target"]
    mod = _load_module(module_path)
    data = root / "data" / "processed"
    out = ev.out_dir / "model_outputs"
    mod.main(str(data), str(out))
    train = pd.read_csv(data / "german_credit_train.csv")
    test = pd.read_csv(data / "german_credit_test.csv")
    p_tr, s_tr, b_tr = _score_scorecard(mod, train, out)
    p_te, s_te, b_te = _score_scorecard(mod, test, out)
    ref = pd.read_csv(out / "predictions_test.csv")
    repro = float(np.max(np.abs(ref["pd"].to_numpy() - p_te)))
    if repro > 1e-9:
        raise AgentError(f"re-scoring does not reproduce the rebuild's predictions ({repro:.2e})")
    digest = hashlib.sha256(module_path.read_bytes()).hexdigest()[:12]
    ev.model_under_test = f"{red['target']} (Python rebuild, sha256 {digest}, approved at Gate 3)"
    ev.data = (f"German Credit (UCI), fixed split seed 42: {len(train)} train / {len(test)} test "
               "rows. Re-scoring with the rebuild's bins, WoE and coefficients reproduces its "
               f"test predictions (max abs diff {repro:.1e}).")
    if test_path is not None:
        test = pd.read_csv(test_path)
        p_te, s_te, b_te = _score_scorecard(mod, test, out)
        ev.data += (f" RE-VALIDATION: the evaluation sample is the monitoring batch "
                    f"{Path(test_path).name} ({len(test)} rows, synthetic drift injected by "
                    "Stage 6), not the original test set.")
    y_tr, y_te = train["bad"].to_numpy(), test["bad"].to_numpy()

    _binary_performance(ev, y_te, p_te, auc(y_tr, p_tr), vcfg, seed)
    ev.metric("performance", "train_ks", ks(y_tr, p_tr))
    _roc_chart(ev, {"train": (y_tr, p_tr), "test": (y_te, p_te)})

    _binary_calibration(ev, y_te, p_te, vcfg["calibration_bins"], hl_rated=True)

    bins = vcfg["psi_bins"]
    score_psi, psi_t = psi(s_tr, s_te, bins)
    ev.metric("stability", "score_psi", score_psi)
    ev.check("stability", "score_psi", "Score PSI (train -> test)", "psi_max", False)
    csi = {v: psi(b_tr[v], b_te[v], bins)[0] for v in b_tr.columns}
    top = max(csi, key=csi.get)
    ev.metric("stability", "max_csi", csi[top])
    ev.check("stability", "max_csi", "Characteristic stability: largest CSI", "psi_max", False,
             f"variable {top}")
    k = vcfg["synthetic_time_tranches"]
    order = np.argsort(test["id"].to_numpy(), kind="stable")
    rows = []
    for i, idx in enumerate(np.array_split(order, k), 1):
        rows.append({"tranche": i, "n": len(idx), "bad_rate": float(y_te[idx].mean()),
                     "auc": auc(y_te[idx], p_te[idx]), "psi_vs_train": psi(s_tr, s_te[idx], bins)[0]})
    tr_t = pd.DataFrame(rows)
    ev.metric("stability", "tranche_min_auc", float(tr_t.auc.min()))
    ev.metric("stability", "tranche_max_psi", float(tr_t.psi_vs_train.max()))
    ev.check("stability", "tranche_max_psi", "Synthetic out-of-time: worst tranche score PSI",
             "psi_max", False, f"{k} tranches of the test set in id order (synthetic time)")
    ev.notes["stability"].append(
        "German Credit has no application date, so the out-of-time back-test orders the test set "
        f"by record id and splits it into {k} tranches. This is a synthetic time ordering, not a "
        "true out-of-time sample.")
    ev.table("stability", "Score distribution: train vs test", psi_t)
    ev.table("stability", "Characteristic stability (CSI) per selected variable",
             pd.DataFrame({"variable": list(csi), "csi": list(csi.values())}))
    ev.table("stability", "Synthetic out-of-time tranches (test set, id order)", tr_t)
    _psi_chart(ev, {"score": score_psi, **csi}, "Score PSI and CSI (train -> test)")
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.hist([s_tr, s_te], bins=20, density=True, label=["train", "test"], alpha=0.8)
    ax.set(xlabel="score", ylabel="density", title="Score distribution")
    ax.legend()
    ev.chart("stability", "score_distribution", fig)

    fairness_binary(ev, card["model_id"], test, y_te, p_te, s_te, s_tr, vcfg)


def fairness_binary(ev: Evidence, model_id: str, df: pd.DataFrame, y_bad, pd_, score, train_score,
                    vcfg: dict) -> None:
    fcfg = vcfg["fairness"]
    attrs = fcfg["protected"].get(model_id, [])
    cutoff = float(np.quantile(train_score, 1 - fcfg["approval_rate"]))
    approved = (score >= cutoff).astype(int)
    good = 1 - np.asarray(y_bad)
    ev.metric("fairness", "score_cutoff", cutoff)
    ev.metric("fairness", "test_approval_rate", float(approved.mean()))
    ev.notes["fairness"].append(
        f"Decision rule for the fairness tests: approve if score >= {cutoff:.1f}, the cut-off that "
        f"approves {fcfg['approval_rate']:.0%} of the training sample. Favourable outcome = approval; "
        "equalized odds compares approval rates among good and among bad applicants. Protected "
        "attributes are excluded from the model but kept for testing. Age is a model input.")
    rates = {}
    for attr in attrs:
        sf = df[attr].astype(str).reset_index(drop=True)
        mf = MetricFrame(metrics={"approval_rate": selection_rate,
                                  "approval_rate_goods": true_positive_rate,
                                  "approval_rate_bads": false_positive_rate},
                         y_true=good, y_pred=approved, sensitive_features=sf.rename(attr))
        t = mf.by_group.reset_index().rename(columns={attr: "group"})
        extra = []
        for gval in t.group:
            m = (sf == gval).to_numpy()
            yy = np.asarray(y_bad)[m]
            extra.append({"n": int(m.sum()), "bad_rate": float(yy.mean()),
                          "auc": auc(yy, np.asarray(pd_)[m]) if len(set(yy)) == 2 else None})
        t = pd.concat([t, pd.DataFrame(extra)], axis=1)
        ev.table("fairness", f"Outcomes by {attr} (test set)", t)
        rates[attr] = t
        eligible = t[t.n >= fcfg["min_group_n"]]
        small = t[t.n < fcfg["min_group_n"]]
        if len(eligible) >= 2:
            m = sf.isin(eligible.group).to_numpy()
            ev.metric("fairness", f"{attr}_adverse_impact_ratio",
                      float(eligible.approval_rate.min() / eligible.approval_rate.max()))
            ev.metric("fairness", f"{attr}_equalized_odds_difference",
                      float(equalized_odds_difference(good[m], approved[m],
                                                      sensitive_features=sf[m])))
            ev.check("fairness", f"{attr}_adverse_impact_ratio",
                     f"{attr}: adverse impact ratio of approval rates", "air_min", True)
            ev.check("fairness", f"{attr}_equalized_odds_difference",
                     f"{attr}: equalized odds difference", "eod_max", False)
        if not small.empty:
            ev.checks.append({"section": "fairness", "check": f"{attr}: group too small to rate",
                              "metric": None, "value": None,
                              "rule": f"n >= {fcfg['min_group_n']} per group", "rag": "amber",
                              "note": ", ".join(f"{r.group} n={r.n}" for r in small.itertuples())})
    if rates:
        fig, ax = plt.subplots(figsize=(5, 4))
        labels, vals = [], []
        for attr, t in rates.items():
            for r in t.itertuples():
                labels.append(f"{attr}={r.group}\n(n={r.n})")
                vals.append(r.approval_rate)
        ax.bar(range(len(vals)), vals, color="#3e7cb1")
        ax.set_xticks(range(len(vals)), labels, fontsize=7)
        ax.set(ylabel="approval rate", title="Approval rate by group (test set)")
        ev.chart("fairness", "approval_rates", fig)


def binary_classifier(card: dict, root: Path, ev: Evidence, vcfg: dict, seed: int) -> None:
    mid = card["model_id"]
    ref = root / "artifacts" / "reference" / mid
    meta = json.loads((ref / "metrics.json").read_text())
    preds = pd.read_csv(ref / "predictions_test.csv.gz")
    y, p = preds["Class"].to_numpy(), preds["p_fraud"].to_numpy()
    data = root / "data" / "processed"
    train = pd.read_parquet(data / "creditcard_train.parquet")
    test = pd.read_parquet(data / "creditcard_test.parquet")
    ev.model_under_test = (f"legacy Python model {mid}: committed reference test predictions "
                           f"(artifacts/reference/{mid}, sklearn {meta.get('sklearn_version')})")
    ev.data = (f"ULB credit-card transactions, time-ordered split: {len(train):,} train / "
               f"{len(test):,} test rows (the test set is a genuine out-of-time sample).")
    _binary_performance(ev, y, p, float(meta["metrics"]["train"]["auc"]), vcfg, seed)
    ev.metric("performance", "test_average_precision", float(average_precision_score(y, p)))
    top = np.argsort(-p, kind="stable")[: max(1, int(round(0.01 * len(p))))]
    ev.metric("performance", "capture_rate_top_1pct", float(y[top].sum() / y.sum()))
    ev.metric("performance", "precision_top_1pct", float(y[top].mean()))
    ev.notes["performance"].append(
        "Train AUC is taken from the legacy run's reference metrics; development-sample scores "
        "are not retained, so all other metrics use the out-of-time test set.")
    _roc_chart(ev, {"test (out-of-time)": (y, p)})
    _binary_calibration(ev, y, p, vcfg["calibration_bins"], hl_rated=False)

    feats = [f"V{i}" for i in range(1, 29)]
    tr = train[feats].assign(log_amount=np.log1p(train["Amount"]),
                             hour=(train["Time"] // 3600) % 24)
    te = test[feats].assign(log_amount=np.log1p(test["Amount"]),
                            hour=(test["Time"] // 3600) % 24)
    psis = {c: psi(tr[c], te[c], vcfg["psi_bins"])[0] for c in tr.columns}
    top_f = max(psis, key=psis.get)
    ev.metric("stability", "max_feature_psi", psis[top_f])
    ev.metric("stability", "features_over_amber",
              int(sum(v > ev.th["psi_max"][1] for v in psis.values())))
    ev.check("stability", "max_feature_psi", "Input stability: largest feature PSI (train -> "
             "test)", "psi_max", False, f"feature {top_f}")
    ev.notes["stability"].append(
        "Score PSI is not assessed: the legacy code does not keep development-sample scores. "
        "Stability is assessed on the model's 30 inputs instead.")
    test_hours = sorted(int(h) for h in te["hour"].unique())
    if len(test_hours) < 24:
        ev.notes["stability"].append(
            f"The out-of-time test window only contains transaction hours {test_hours[0]}-"
            f"{test_hours[-1]}, while training covers all 24 hours, so the 'hour' input is "
            "extrapolated into a narrower range. This is a property of the time-ordered split.")
    ev.table("stability", "Feature PSI (train -> test)",
             pd.DataFrame({"feature": list(psis), "psi": list(psis.values())})
             .sort_values("psi", ascending=False))
    _psi_chart(ev, psis, "Feature PSI (train -> out-of-time test)")
    ev.na["fairness"] = ("Not assessable: the dataset holds only PCA-anonymised features, amount "
                         "and time, with no protected or proxy attributes.")


def poisson_frequency(card: dict, root: Path, ev: Evidence, vcfg: dict, seed: int) -> None:
    mid = card["model_id"]
    ref = root / "artifacts" / "reference" / mid
    meta = json.loads((ref / "metrics.json").read_text())
    data = root / "data" / "processed"
    train = pd.read_parquet(data / "fremtpl2_train.parquet")
    test = pd.read_parquet(data / "fremtpl2_test.parquet")
    preds = pd.read_csv(ref / "predictions_test.csv.gz")
    df = preds.merge(test.drop(columns=["ClaimNb", "Exposure"]), on="IDpol", validate="1:1")
    if len(df) != len(preds):
        raise AgentError("reference predictions do not match the test set")
    ev.model_under_test = (f"legacy Python model {mid}: committed reference test predictions "
                           f"(artifacts/reference/{mid}, statsmodels "
                           f"{meta.get('statsmodels_version')})")
    ev.data = (f"freMTPL2 motor claims, random split seed 42: {len(train):,} train / "
               f"{len(test):,} test policies.")
    pcfg = vcfg["pricing"]
    y, mu, expo = df["ClaimNb"].to_numpy(), df["expected_claims"].to_numpy(), df["Exposure"].to_numpy()
    gini, lx, ly = lorenz_gini(y, mu, expo)
    m = {"test_mean_poisson_deviance": poisson_deviance(y, mu),
         "train_mean_poisson_deviance": float(meta["metrics"]["train"]["mean_poisson_deviance"]),
         "test_lorenz_gini": gini, "test_actual_claims": int(y.sum()),
         "test_expected_claims": float(mu.sum()), "test_policies": int(len(df))}
    m["deviance_ratio_test_train"] = m["test_mean_poisson_deviance"] / m["train_mean_poisson_deviance"]
    m["ae_overall"] = m["test_actual_claims"] / m["test_expected_claims"]
    m["ae_dev"] = abs(m["ae_overall"] - 1)
    for k, v in m.items():
        ev.metric("performance", k, v)
    ev.check("performance", "ae_dev", "Overall actual / expected claims: |A/E - 1|", "ae_dev_max",
             False)
    ev.notes["performance"].append(
        "The Lorenz Gini and deviance ratio are reported without a rating: no agreed threshold "
        "exists for this portfolio.")
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(lx, ly, label=f"model (Gini {gini:.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    ax.set(xlabel="share of exposure (riskiest first)", ylabel="share of claims",
           title="Ordered Lorenz curve (test)")
    ax.legend()
    ev.chart("performance", "lorenz_curve", fig)

    minexp = pcfg["min_segment_exposure"]
    df["predicted_frequency_decile"] = quantile_groups(mu / expo, vcfg["calibration_bins"])
    dec = segment_ae(df, "predicted_frequency_decile")
    dec["mean_predicted_frequency"] = dec.expected / dec.exposure
    dec["observed_frequency"] = dec.actual / dec.exposure
    ev.metric("calibration", "worst_decile_ae_dev",
              float((dec[dec.exposure >= minexp].ae - 1).abs().max()))
    ev.check("calibration", "worst_decile_ae_dev", "Worst predicted-frequency decile: |A/E - 1|",
             "segment_ae_dev_max", False)
    ev.table("calibration", "A/E by decile of predicted frequency", dec)
    _calibration_chart(ev, dec, "mean_predicted_frequency", "observed_frequency",
                       "Observed vs predicted claim frequency by decile")
    for seg in pcfg["segments"]:
        t = segment_ae(df, seg)
        key = f"worst_{seg}_ae_dev"
        ev.metric("calibration", key, float((t[t.exposure >= minexp].ae - 1).abs().max()))
        ev.check("calibration", key, f"Worst {seg} segment: |A/E - 1|", "segment_ae_dev_max",
                 False, f"segments with exposure >= {minexp:,}")
        ev.table("calibration", f"A/E by {seg}", t)

    cols = ["VehPower", "VehAge", "DrivAge", "BonusMalus", "Density", "Area", "VehBrand",
            "VehGas", "Region"]
    psis = {c: psi(train[c], test[c], vcfg["psi_bins"])[0] for c in cols}
    top_f = max(psis, key=psis.get)
    ev.metric("stability", "max_feature_psi", psis[top_f])
    ev.check("stability", "max_feature_psi", "Input stability: largest feature PSI (train -> "
             "test)", "psi_max", False, f"feature {top_f}")
    ev.notes["stability"].append("Score PSI is not assessed: the legacy code does not keep "
                                 "development-sample predictions.")
    ev.table("stability", "Feature PSI (train -> test)",
             pd.DataFrame({"feature": list(psis), "psi": list(psis.values())}))
    _psi_chart(ev, psis, "Feature PSI (train -> test)")

    df["DrivAge_band"] = pd.cut(df["DrivAge"], [17, 20, 25, 30, 40, 50, 70, np.inf],
                                labels=["18-20", "21-25", "26-30", "31-40", "41-50", "51-70",
                                        "71+"]).astype(str)
    age = segment_ae(df, "DrivAge_band")
    ev.metric("fairness", "worst_driver_age_ae_dev",
              float((age[age.exposure >= minexp].ae - 1).abs().max()))
    ev.check("fairness", "worst_driver_age_ae_dev", "Driver age bands: worst |A/E - 1|",
             "segment_ae_dev_max", False, "checks no age band is systematically over- or "
             "under-priced")
    ev.table("fairness", "A/E by driver age band", age)
    ev.notes["fairness"].append(
        "Sex is not in the data. Driver age is a rating factor; age-based pricing must be "
        "supported by relevant and reliable data, which the A/E by age band tests. Whether the "
        "use of age is permissible is a legal and conduct question for the reviewer.")
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(age.segment, age.ae, color="#3e7cb1")
    ax.axhline(1, color="k", ls="--", lw=0.8)
    ax.set(xlabel="driver age band", ylabel="actual / expected", title="A/E by driver age band")
    ev.chart("fairness", "ae_by_driver_age", fig)


BUILDERS = {"scorecard_rebuild": scorecard_rebuild, "binary_classifier": binary_classifier,
            "poisson_frequency": poisson_frequency}


# ---- rendering -----------------------------------------------------------------------------

def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def render_html(card: dict, ev: Evidence, val: dict, narrative: dict) -> str:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    checks = [{**c, "value_fmt": fmt(c["value"])} for c in ev.checks]
    notes = {s: ([ev.na[s]] if s in ev.na else []) + ev.notes[s] for s in SECTIONS}
    return env.get_template("validation_report.html.j2").render(
        name=card["name"], model_under_test=ev.model_under_test, generated_at=val["generated_at"],
        status=val["status"], overall=val["overall_rag"], outcome=val["outcome"],
        trigger=val.get("trigger"),
        section_rag=val["section_rag"], checks=checks, sections=SECTIONS,
        narrative=narrative, notes_by_section=notes, llm=narrative["llm"],
        metrics={s: {k: fmt(v) for k, v in ev.metrics[s].items()} for s in SECTIONS},
        tables={s: [(t, df.to_html(index=False, float_format=lambda x: fmt(x), border=0))
                    for t, df in ev.tables[s]] for s in SECTIONS},
        charts={s: [(t, _b64(p)) for t, p in ev.charts[s]] for s in SECTIONS},
        evidence_path=val["evidence_path"], elapsed_s=val["elapsed_s"])


def render_pdf(path: Path, card: dict, ev: Evidence, val: dict, narrative: dict) -> None:
    import textwrap
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        lines = [f"Independent validation report: {card['name']}", "",
                 f"Model under test: {ev.model_under_test}", f"Generated: {val['generated_at']}",
                 f"Status: {val['status']}", *([val["trigger"]] if val.get("trigger") else []), "",
                 f"OVERALL RATING: {val['overall_rag'].upper()} - {val['outcome']}", "",
                 "Ratings by area: " + ", ".join(f"{s} {r.upper()}"
                                                 for s, r in val["section_rag"].items()), "",
                 "Executive summary:"]
        lines += textwrap.wrap(narrative["executive_summary"], 95) + ["", "Checks:"]
        for c in ev.checks:
            lines += textwrap.wrap(f"[{c['rag'].upper()}] {c['section']}: {c['check']} = "
                                   f"{fmt(c['value'])} ({c['rule']}) {c['note']}", 95)
        lines += ["", "Findings:"]
        for f in narrative["findings"]:
            lines += textwrap.wrap(f"[{f['severity']}] {f['finding']} -> {f['recommendation']}", 95)
        fig.text(0.06, 0.97, "\n".join(lines[:95]), va="top", family="monospace", fontsize=7)
        pdf.savefig(fig)
        plt.close(fig)
        for s in SECTIONS:
            for title, p in ev.charts[s]:
                fig = plt.figure(figsize=(8.27, 6))
                ax = fig.add_axes([0, 0, 1, 0.94])
                ax.imshow(plt.imread(p))
                ax.axis("off")
                fig.suptitle(f"{s.capitalize()}: {title}")
                pdf.savefig(fig)
                plt.close(fig)


# ---- stage entry point ---------------------------------------------------------------------

def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def run_stage(card: dict, ctx) -> dict:
    cfg, vcfg = ctx.cfg, ctx.cfg["validate"]
    mid = card["model_id"]
    kind = vcfg["models"].get(mid)
    if kind is None or card["language"] not in cfg["discover"]["executable_languages"]:
        return {"applicable": False,
                "summary": f"Not applicable: no validation evidence builder for {mid} "
                           f"({card['language']}; SAS has no runtime here)."}
    root = ctx.model_dir.parent.parent
    t0 = time.perf_counter()
    reval = (card.get("monitoring") or {}).get("revalidation") or {}
    reval = reval if reval.get("status") == "pending" else {}
    suffix = f"_revalidation_m{reval['month']:02d}" if reval else ""
    out_dir = Path(cfg["paths"]["validation_artifacts"]) / f"{mid}{suffix}"
    ev = Evidence(vcfg["thresholds"], out_dir)
    extra = {"test_path": root / reval["data_path"]} if reval else {}
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "invalid value encountered in cast", RuntimeWarning)
        BUILDERS[kind](card, root, ev, vcfg, cfg["seed"], **extra)
    elapsed = time.perf_counter() - t0

    section_rag = {s: "n/a" if s in ev.na else worst(c["rag"] for c in ev.checks
                                                       if c["section"] == s) for s in SECTIONS}
    overall = worst(section_rag.values())
    facts = {f"{s}.{k}": fmt(v) for s in SECTIONS for k, v in ev.metrics[s].items()}
    trigger = (f"Re-validation triggered by monitoring: {reval['reason']}" if reval else None)
    notes = ([trigger] if trigger else []) + [ev.data] + [n for s in SECTIONS for n in ([ev.na[s]] if s in ev.na else [])
                         + ev.notes[s]]
    inv = card.get("inventory", {})
    context = {"name": card["name"], "model_id": mid, "model_under_test": ev.model_under_test,
               **{k: inv.get(k) for k in ("purpose", "model_type", "risk_tier", "owner")},
               "documentation_gaps": (card.get("documentation") or {}).get("gaps", []),
               "overall_rating": overall, "section_ratings": section_rag,
               "revalidation_trigger": trigger}
    checks_for_llm = [{k: c[k] for k in ("section", "check", "metric", "rule", "rag", "note")}
                      for c in ev.checks]
    feedback = discover.reviewer_feedback(cfg["paths"]["audit"], mid,
                                          card["stages"][STAGE]["completed_at"], stage=STAGE)
    narrative = report_agent.run(ctx.llm, mid, context, facts, checks_for_llm, notes, SECTIONS,
                                 vcfg["max_tokens"], feedback)

    rep_dir = Path(cfg["paths"]["validation_reports"])
    rep_dir.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path = rep_dir / f"{mid}{suffix}.html", rep_dir / f"{mid}{suffix}.pdf"
    evidence_path = out_dir / "evidence.json"
    val = {"generated_at": model_card.now_iso(), "status": "pending Gate 4", "trigger": trigger,
           "model_under_test": ev.model_under_test, "data": ev.data, "overall_rag": overall,
           "outcome": OUTCOME.get(overall, ""), "section_rag": section_rag,
           "checks": [{**c, "value": c["value"]} for c in ev.checks],
           "not_assessed": ev.na, "findings": narrative["findings"],
           "narrative_dropped": narrative["dropped_sentences"], "llm": narrative["llm"],
           "report_html": _rel(html_path, root), "report_pdf": _rel(pdf_path, root),
           "evidence_path": _rel(evidence_path, root), "elapsed_s": round(elapsed, 2),
           "reviewer_feedback_used": feedback}
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps({
        "model_id": mid, "builder": kind, "metrics": ev.metrics, "checks": ev.checks,
        "notes": ev.notes, "not_assessed": ev.na, "data": ev.data,
        "model_under_test": ev.model_under_test, "thresholds": vcfg["thresholds"],
        "tables": {s: {t: df.to_dict(orient="records") for t, df in ev.tables[s]}
                   for s in SECTIONS}}, indent=1, default=str))
    html_path.write_text(render_html(card, ev, val, narrative))
    render_pdf(pdf_path, card, ev, val, narrative)
    card["validation"] = val
    model_card.validate(card)

    llm = narrative["llm"]
    summary = "\n".join([
        *([f"RE-VALIDATION. {trigger}"] if trigger else []),
        f"Validation report: {val['report_html']} (+ PDF)",
        f"Model under test: {ev.model_under_test}",
        f"Overall: {overall.upper()} - {val['outcome']}",
        "By area: " + ", ".join(f"{s} {r.upper()}" for s, r in section_rag.items()),
        *[f"- [{c['rag'].upper()}] {c['check']}: {fmt(c['value'])}" for c in ev.checks],
        f"Findings: {len(narrative['findings'])}; narrative sentences dropped by the number "
        f"check: {len(narrative['dropped_sentences'])}",
        f"Computation {elapsed:.1f}s; LLM: {llm['model']}, "
        f"{llm['input_tokens'] + llm['output_tokens']} tokens, ${llm['cost_usd']:.4f}"
        f"{' (cached)' if llm['cached'] else ''}",
    ])
    return {"summary": summary}
