"""Run transformation unit-test cases against a generated Python module.

Executed in a subprocess by factory.redevelop so generated code never runs in the pipeline process.
Usage: python -m factory.redevelop_harness <module.py> <cases.json> <results.json>
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback

import numpy as np
import pandas as pd


def load(path: str):
    spec = importlib.util.spec_from_file_location("redeveloped_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def convert(name: str, value):
    if value is None:
        return None
    if name == "breaks":
        return [float(b) for b in value]
    if name == "table":
        return pd.DataFrame(value)
    return pd.Series(value)


def norm(r):
    """Make a result JSON-comparable with the R oracle's output."""
    if isinstance(r, pd.DataFrame):
        return [{k: norm(v) for k, v in row.items()} for row in r.to_dict("records")]
    if isinstance(r, (pd.Series, np.ndarray, list, tuple)):
        return [norm(v) for v in list(r)]
    if isinstance(r, dict):
        return {str(k): norm(v) for k, v in r.items()}
    if isinstance(r, (bool, np.bool_)):
        return bool(r)
    if isinstance(r, (int, np.integer)):
        return int(r)
    if isinstance(r, (float, np.floating)):
        f = float(r)
        return "NaN" if f != f else "Inf" if f == float("inf") else "-Inf" if f == -float("inf") else f
    return r if r is None or isinstance(r, str) else str(r)


def main(module_path: str, cases_path: str, out_path: str) -> None:
    cases = json.loads(open(cases_path).read())
    try:
        mod = load(module_path)
    except Exception:
        out = {"import_error": traceback.format_exc(limit=3)}
    else:
        results = []
        for case in cases:
            try:
                fn = getattr(mod, case["fn"])
                args = [convert(k, v) for k, v in case["args"].items()]
                results.append({"id": case["id"], "result": norm(fn(*args))})
            except Exception as e:
                results.append({"id": case["id"], "error": f"{type(e).__name__}: {e}"})
        out = {"results": results}
    with open(out_path, "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main(*sys.argv[1:4])
