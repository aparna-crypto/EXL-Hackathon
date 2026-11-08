"""Translation agent: rewrites a legacy R scorecard as a Python module with a fixed interface.

The interface is fixed so a deterministic harness (factory.redevelop) can unit test every
transformation against the R original and check end-to-end parity. The agent never sees data rows.
"""

from __future__ import annotations

import ast
import re

AGENT = "translation"

SYSTEM = ("You translate legacy R model code into Python 3.11 with exact numerical parity. "
          "Reply with exactly one ```python code block containing the complete module, and "
          "nothing else.")

REQUIRED_CONSTS = ["TARGET", "EXCLUDED", "N_BINS", "IV_MIN", "PDO", "BASE_SCORE", "BASE_ODDS"]
REQUIRED_FUNCS = ["is_binned_numeric", "numeric_breaks", "assign_bin", "woe_table", "apply_woe",
                  "to_score", "auc", "ks", "main"]
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "input", "breakpoint"}

CONTRACT = """\
Required module interface (the harness imports the module and calls these directly):

Module-level constants: TARGET, EXCLUDED (list of str), N_BINS, IV_MIN, PDO, BASE_SCORE, BASE_ODDS.

Functions, each with the same semantics as the R function of the same name:
- is_binned_numeric(x: pd.Series) -> bool
- numeric_breaks(x: pd.Series) -> list[float]            # outer breaks are -inf and inf
- assign_bin(x: pd.Series, breaks: list[float] | None) -> pd.Series of str
      Labels must be byte-identical to R: cut(x, breaks, right=TRUE, include.lowest=TRUE,
      dig.lab=10) when breaks are given, as.character(x) when breaks is None.
- woe_table(bin: pd.Series, y: pd.Series) -> pd.DataFrame with columns
      bin, n_good, n_bad, woe, iv_part (one row per distinct bin)
- apply_woe(bin: pd.Series, table: pd.DataFrame) -> pd.Series of float  (unseen bin -> 0.0)
- to_score(pd: array-like of PD) -> np.ndarray
- auc(y, p) -> float  and  ks(y, p) -> float   (same definitions as the R code, ties included)
- main(data_dir: str = "data/processed",
       out_dir: str = "artifacts/redevelop/credit_scorecard/outputs") -> None
      Reproduces the whole R script: reads german_credit_train.csv and german_credit_test.csv
      from data_dir and writes bins.csv, coefficients.csv, predictions_test.csv and metrics.json
      to out_dir with the same columns, row order, term names and JSON keys as the R script.
End the module with:  if __name__ == "__main__": main(*sys.argv[1:3])

Rules:
- Do not hard-code fitted values, break points, WoE values or selected variables. Parity is
  also checked on a resampled dataset you have not seen.
- Fit the logistic regression with statsmodels GLM (Binomial family), as R's glm() does.
- Read only the two CSV files; write only to out_dir. No network, no subprocesses.
- Allowed imports: {allowed}.
"""


def build_prompt(r_path: str, r_source: str, reference: dict[str, str], doc_steps: list[dict],
                 allowed: list[str], feedback: str | None = None) -> str:
    parts = [f"Translate this R model ({r_path}) into a Python module.", "",
             CONTRACT.format(allowed=", ".join(allowed)), "",
             f"R source ({r_path}):", "```r", r_source.rstrip(), "```", "",
             "Saved outputs of the R run (first lines of each file; the Python main() must write "
             "files in the same format):"]
    for name, text in reference.items():
        parts += [f"--- {name}", text.rstrip()]
    if doc_steps:
        parts += ["", "Transformation steps from the reviewer-approved model documentation:"]
        parts += [f"- {s['step']}: {s['description']}" for s in doc_steps]
    if feedback:
        parts += ["", "A human reviewer rejected or commented on the previous rebuild. Address "
                  f"this feedback:\n{feedback}"]
    return "\n".join(parts)


def repair_prompt(base: str, attempt: int, code: str | None, failures: list[str]) -> str:
    shown = failures[:25]
    more = f"\n... and {len(failures) - len(shown)} more" if len(failures) > len(shown) else ""
    prev = f"\n\nYour previous module:\n```python\n{code.rstrip()}\n```" if code else ""
    return (f"{base}\n\nAttempt {attempt} failed these checks:\n"
            + "\n".join(f"- {f}" for f in shown) + more + prev
            + "\n\nFix every failure and reply with the full corrected module.")


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text, flags=re.S)
    if not blocks:
        raise ValueError("reply contained no ```python code block")
    return max(blocks, key=len).strip() + "\n"


def static_check(code: str, allowed: list[str]) -> list[str]:
    """Problems found without running the code: syntax, imports, interface, unsafe calls."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    issues, defined = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            mods = []
        for m in mods:
            if m.split(".")[0] not in allowed:
                issues.append(f"import not allowed: {m}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in FORBIDDEN_CALLS:
            issues.append(f"call not allowed: {node.func.id}()")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined |= {t.id for t in targets if isinstance(t, ast.Name)}
    missing = [n for n in REQUIRED_CONSTS + REQUIRED_FUNCS if n not in defined]
    if missing:
        issues.append("missing from module interface: " + ", ".join(missing))
    return issues
