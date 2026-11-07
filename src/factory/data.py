"""Download and prepare the public datasets used by the legacy models.

python -m factory.data [german_credit|creditcard|fremtpl2] ...   (default: all)

Raw files go to data/raw/, processed train/test splits to data/processed/ (both gitignored).
Splits are fixed by SEED so every run, in R or Python, sees exactly the same rows.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from factory.config import REPO_ROOT

SEED = 42
RAW = REPO_ROOT / "data" / "raw"
PROCESSED = REPO_ROOT / "data" / "processed"

SOURCES = {
    "german_credit": {
        "url": "https://archive.ics.uci.edu/ml/machine-learning-databases/statlog/german/german.data",
        "file": "german.data",
        "sha256": "b21f3d81db8071257d5ff1deaeba1fd4303b62712e6fcc9715c7a86202cb5871",
    },
    "creditcard": {  # ULB credit-card fraud (Kaggle mlg-ulb), public mirror used by TensorFlow docs
        "url": "https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv",
        "file": "creditcard.csv",
        "sha256": "76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89",
    },
    "fremtpl2": {  # French motor TPL claim frequency, from the CASdatasets R package (GitHub)
        "url": "https://github.com/dutangc/CASdatasets/raw/master/data/freMTPL2freq.rda",
        "file": "freMTPL2freq.rda",
        "sha256": "82c8598513d9fa78226b6c7d271a9940eca4b8083e9e32320a75d377bdfe15a3",
    },
}


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str) -> Path:
    src = SOURCES[name]
    path = RAW / src["file"]
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        print(f"downloading {name} from {src['url']}")
        try:
            urllib.request.urlretrieve(src["url"], path)
        except Exception as e:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise SystemExit(f"Download of {name} failed ({e}). Please download it manually from "
                             f"{src['url']} and save it as {path}") from e
    for algo in ("sha256", "md5"):
        if src.get(algo) and _digest(path, algo) != src[algo]:
            raise SystemExit(f"{path} does not match the expected {algo} checksum; delete it and retry")
    return path


# ---- German Credit (UCI Statlog) -----------------------------------------------------------

GERMAN_COLUMNS = [
    "checking_status", "duration", "credit_history", "purpose", "credit_amount", "savings",
    "employment_since", "installment_rate", "personal_status_sex", "other_debtors",
    "residence_since", "property", "age", "other_installment_plans", "housing",
    "existing_credits", "job", "num_dependents", "telephone", "foreign_worker", "class",
]


def prepare_german_credit() -> None:
    df = pd.read_csv(download("german_credit"), sep=" ", header=None, names=GERMAN_COLUMNS)
    df.insert(0, "id", np.arange(1, len(df) + 1))
    df["bad"] = (df.pop("class") == 2).astype(int)  # 1 = bad credit risk
    # Protected attributes kept for fairness testing only (not model inputs).
    df["sex"] = np.where(df["personal_status_sex"].isin(["A92", "A95"]), "female", "male")
    df["age_group"] = np.where(df["age"] < 25, "under_25", "25_plus")
    train, test = train_test_split(df, test_size=0.3, stratify=df["bad"], random_state=SEED)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    train.sort_values("id").to_csv(PROCESSED / "german_credit_train.csv", index=False)
    test.sort_values("id").to_csv(PROCESSED / "german_credit_test.csv", index=False)
    print(f"german_credit: train {len(train)}, test {len(test)}, bad rate {df['bad'].mean():.3f}")


# ---- ULB credit-card fraud -------------------------------------------------------------------

def prepare_creditcard() -> None:
    df = pd.read_csv(download("creditcard"))
    df["Class"] = df["Class"].astype(int)
    df = df.sort_values("Time", kind="stable").reset_index(drop=True)
    df.insert(0, "id", np.arange(1, len(df) + 1))
    cut = int(len(df) * 0.8)  # time-ordered split: last 20% of transactions is the test set
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.iloc[:cut].to_parquet(PROCESSED / "creditcard_train.parquet", index=False)
    df.iloc[cut:].to_parquet(PROCESSED / "creditcard_test.parquet", index=False)
    print(f"creditcard: train {cut}, test {len(df) - cut}, fraud rate {df['Class'].mean():.5f}")


# ---- freMTPL2 claim frequency -----------------------------------------------------------------

def prepare_fremtpl2() -> None:
    rda = download("fremtpl2")
    csv = rda.with_suffix(".csv")
    if not csv.exists():  # .rda is R's native format; convert once with R
        subprocess.run(["Rscript", "-e", f"load('{rda}'); write.csv(freMTPL2freq, '{csv}', "
                        "row.names = FALSE)"], check=True)
    df = pd.read_csv(csv)
    df["IDpol"] = df["IDpol"].astype(int)
    df["ClaimNb"] = df["ClaimNb"].astype(int).clip(upper=4)  # standard cleaning of this dataset
    df["Exposure"] = df["Exposure"].clip(upper=1.0)
    df = df.sort_values("IDpol").reset_index(drop=True)
    train, test = train_test_split(df, test_size=0.2, random_state=SEED)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    train.sort_values("IDpol").to_parquet(PROCESSED / "fremtpl2_train.parquet", index=False)
    test.sort_values("IDpol").to_parquet(PROCESSED / "fremtpl2_test.parquet", index=False)
    print(f"fremtpl2: train {len(train)}, test {len(test)}, "
          f"claim frequency {df['ClaimNb'].sum() / df['Exposure'].sum():.4f}")


PREPARE = {"german_credit": prepare_german_credit, "creditcard": prepare_creditcard,
           "fremtpl2": prepare_fremtpl2}

if __name__ == "__main__":
    for name in sys.argv[1:] or PREPARE:
        PREPARE[name]()
