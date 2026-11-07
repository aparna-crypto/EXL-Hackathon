"""Motor third-party liability claim frequency GLM (pricing).

Model ID : PRC-MTPL-03
Owner    : Personal Lines Pricing
Purpose  : Expected claim frequency per policy-year, used as the frequency component of the
           technical premium.
Method   : Poisson GLM, log link, log(Exposure) offset. See README.md in this folder.
Usage    : python models/pricing_glm/pricing_glm.py [data_dir] [out_dir]
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels
import statsmodels.api as sm
import statsmodels.formula.api as smf

SEED = 42
data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/processed")
out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/reference/pricing_glm")
out_dir.mkdir(parents=True, exist_ok=True)

train = pd.read_parquet(data_dir / "fremtpl2_train.parquet")
test = pd.read_parquet(data_dir / "fremtpl2_test.parquet")


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"ClaimNb": df["ClaimNb"], "Exposure": df["Exposure"]})
    out["VehPower"] = df["VehPower"].clip(upper=9).astype(int).astype(str)
    out["VehAge"] = pd.cut(df["VehAge"], [-1, 0, 5, 10, 15, np.inf],
                           labels=["0", "1-5", "6-10", "11-15", "16+"]).astype(str)
    out["DrivAge"] = pd.cut(df["DrivAge"], [17, 20, 25, 30, 40, 50, 70, np.inf],
                            labels=["18-20", "21-25", "26-30", "31-40", "41-50", "51-70",
                                    "71+"]).astype(str)
    out["BonusMalus"] = df["BonusMalus"].clip(upper=150)
    out["LogDensity"] = np.log(df["Density"])
    for col in ["Area", "VehBrand", "VehGas", "Region"]:
        out[col] = df[col].astype(str)
    return out


tr, te = prepare(train), prepare(test)
formula = ("ClaimNb ~ C(VehPower) + C(VehAge) + C(DrivAge, Treatment('31-40')) + BonusMalus"
           " + LogDensity + C(Area) + C(VehBrand) + C(VehGas) + C(Region)")
fit = smf.glm(formula, data=tr, family=sm.families.Poisson(),
              offset=np.log(tr["Exposure"])).fit()

pred_train = fit.predict(tr, offset=np.log(tr["Exposure"]))
pred_test = fit.predict(te, offset=np.log(te["Exposure"]))


def poisson_deviance(y, mu):
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * np.sum(term - (y - mu)) / len(y))


metrics = {}
for name, df, mu in [("train", tr, pred_train), ("test", te, pred_test)]:
    metrics[name] = {"n": int(len(df)), "exposure": float(df["Exposure"].sum()),
                     "observed_claims": int(df["ClaimNb"].sum()),
                     "predicted_claims": float(mu.sum()),
                     "mean_poisson_deviance": poisson_deviance(df["ClaimNb"], mu)}

pd.DataFrame({"term": fit.params.index, "estimate": fit.params.values,
              "std_error": fit.bse.values}).to_csv(out_dir / "coefficients.csv", index=False,
                                                   float_format="%.10g")
pd.DataFrame({"IDpol": test["IDpol"].values, "ClaimNb": te["ClaimNb"].values,
              "Exposure": te["Exposure"].values, "expected_claims": pred_test.values}).to_csv(
    out_dir / "predictions_test.csv.gz", index=False, float_format="%.10g")
(out_dir / "metrics.json").write_text(json.dumps({
    "model": "pricing_glm", "language": "Python", "statsmodels_version": statsmodels.__version__,
    "seed": SEED, "formula": formula, "aic": fit.aic, "metrics": metrics}, indent=2))
print(f"pricing_glm: test deviance {metrics['test']['mean_poisson_deviance']:.4f}, "
      f"observed/predicted claims {metrics['test']['observed_claims']}/"
      f"{metrics['test']['predicted_claims']:.0f}")
