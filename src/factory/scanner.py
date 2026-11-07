"""Static scanner for SAS, R and Python model code.

Deterministic, no LLM: it parses the code for libraries, data inputs and outputs, functions,
modelling methods, random seeds and header metadata, and lists documentation files kept next to
the code. The inventory agent (Stage 2) works from these facts.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from factory.model_card import now_iso

LANGUAGE_BY_EXT = {".r": "R", ".py": "Python", ".sas": "SAS"}
DOC_EXTS = {".md", ".txt", ".rst", ".pdf", ".docx", ".html"}
DATA_FILE = re.compile(r"[\w./-]+\.(?:csv|parquet|json|rds|rda|rdata|sas7bdat|xlsx|pkl|joblib)"
                       r"(?:\.gz)?$", re.I)
DIR_LITERAL = re.compile(r"^[\w.-]+(?:/[\w.-]+)+/?$")

# Keyword -> method label. Matched case-insensitively against code with comments removed.
METHOD_PATTERNS = {
    r"\bglm\s*\(.*binomial|family\s*=\s*binomial|LogisticRegression|proc\s+logistic":
        "logistic regression",
    r"Poisson\s*\(|family\s*=\s*poisson": "Poisson GLM",
    r"HistGradientBoosting|GradientBoosting|xgboost|lightgbm|\bgbm\s*\(": "gradient boosting",
    r"RandomForest|randomForest|ranger\s*\(": "random forest",
    r"\bwoe\b|weight[- ]of[- ]evidence": "weight of evidence (WoE) binning",
    r"\bPDO\b|points to double": "points scaling (scorecard)",
    r"selection\s*=\s*stepwise|\bstep\s*\(": "stepwise variable selection",
    r"\boffset\s*=(?!=)": "offset term",
}

PY_READ = {"read_csv", "read_parquet", "read_excel", "read_json", "read_pickle", "read_table",
           "read_text", "read_bytes", "load", "open", "read_sas"}
PY_WRITE = {"to_csv", "to_parquet", "to_json", "to_pickle", "to_excel", "write_text",
            "write_bytes", "dump", "savefig", "save_model", "save"}
R_READ = ("read.csv", "read.table", "read.delim", "readRDS", "fread", "read_csv", "read_parquet",
          "read_excel", "load", "read_sas")
R_WRITE = ("write.csv", "write.table", "saveRDS", "fwrite", "writeLines", "write_csv",
           "write_parquet", "save")


def _header_fields(text: str) -> dict:
    """'Key : value' lines in a header block -> {key: value}."""
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Z][A-Za-z ]{1,20}?)\s*:\s*(\S.*)$", line)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def _methods(code: str) -> list[str]:
    return sorted({label for pat, label in METHOD_PATTERNS.items()
                   if re.search(pat, code, re.I)})


def _balanced(text: str, start: int) -> str:
    """Text inside the parentheses that open at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


def _join(base: str | None, name: str) -> str:
    return f"{base.rstrip('/')}/{name}" if base else name


# ---- Python ----------------------------------------------------------------------------------

def _py_strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)]


def _py_path(node: ast.AST, dirs: dict[str, str]) -> list[str]:
    """Data-file paths in an expression, resolving `dir_var / "file.csv"` to a full path."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) and isinstance(n.left, ast.Name) \
                and isinstance(n.right, ast.Constant) and isinstance(n.right.value, str) \
                and DATA_FILE.match(n.right.value):
            out.append(_join(dirs.get(n.left.id), n.right.value))
    if out:
        return out
    return [s for s in _py_strings(node) if DATA_FILE.match(s)]


def scan_python(path: Path) -> dict:
    code = path.read_text()
    tree = ast.parse(code)
    doc = ast.get_docstring(tree) or ""
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            imports.add(n.module.split(".")[0])
    dirs = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            lits = [s for s in _py_strings(n.value) if DIR_LITERAL.match(s)]
            if len(lits) == 1:
                dirs[n.targets[0].id] = lits[0]
    reads, writes = [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if name in PY_READ | PY_WRITE:
                parts = list(n.args) + [k.value for k in n.keywords]
                if isinstance(n.func, ast.Attribute):
                    parts.append(n.func.value)
                found = [p for part in parts for p in _py_path(part, dirs)]
                (reads if name in PY_READ else writes).extend(found)
    seeds = sorted({str(n.value.value) for n in ast.walk(tree)
                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, int)
                    and any(isinstance(t, ast.Name) and "SEED" in t.id.upper() for t in n.targets)})
    code_no_doc = code.replace(doc, "") if doc else code
    return {
        "header": doc,
        "header_fields": _header_fields(doc),
        "libraries": sorted(m for m in imports if m not in sys.stdlib_module_names),
        "stdlib": sorted(m for m in imports if m in sys.stdlib_module_names),
        "inputs": sorted(set(reads)),
        "outputs": sorted(set(writes)),
        "functions": [n.name for n in tree.body if isinstance(n, ast.FunctionDef)],
        "methods": _methods(code_no_doc),
        "seeds": seeds,
        "loc": sum(1 for line in code.splitlines() if line.strip()
                   and not line.strip().startswith("#")),
    }


# ---- R ---------------------------------------------------------------------------------------

def _r_paths(args: str, dirs: dict[str, str]) -> list[str]:
    out = [_join(dirs.get(d), f) for d, f in
           re.findall(r"file\.path\(\s*(\w+)\s*,\s*[\"']([^\"']+)[\"']\s*\)", args)]
    out += [s for s in re.findall(r"[\"']([^\"']+)[\"']", args)
            if DATA_FILE.match(s) and not any(s == f.split("/")[-1] for f in out)]
    return [p for p in out if DATA_FILE.match(p)]


def _r_calls(code: str, names: tuple[str, ...]) -> list[str]:
    pat = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")\s*\(")
    return [_balanced(code, m.end() - 1) for m in pat.finditer(code)]


def scan_r(path: Path) -> dict:
    code = path.read_text()
    lines = code.splitlines()
    header_lines = []
    for line in lines:
        if not line.startswith("#"):
            break
        header_lines.append(line.lstrip("#").rstrip())
    header = "\n".join(header_lines).strip()
    body = "\n".join(re.sub(r"#.*$", "", line) for line in lines)
    libs = set(re.findall(r"(?:library|require|requireNamespace)\(\s*[\"']?([\w.]+)", body))
    libs |= set(re.findall(r"\b([A-Za-z][\w.]*)::", body))
    dirs = {}
    for var, rhs in re.findall(r"^\s*(\w+)\s*<-\s*(.+)$", body, re.M):
        lits = [s for s in re.findall(r"[\"']([^\"']+)[\"']", rhs) if DIR_LITERAL.match(s)]
        if len(lits) == 1:
            dirs[var] = lits[0]
    reads = [p for a in _r_calls(body, R_READ) for p in _r_paths(a, dirs)]
    writes = [p for a in _r_calls(body, R_WRITE) for p in _r_paths(a, dirs)]
    sources = re.findall(r"\bsource\(\s*[\"']([^\"']+)[\"']", body)
    return {
        "header": header,
        "header_fields": _header_fields(header),
        "libraries": sorted(libs),
        "stdlib": [],
        "inputs": sorted(set(reads)),
        "outputs": sorted(set(writes)),
        "sourced_scripts": sources,
        "functions": re.findall(r"^\s*([\w.]+)\s*<-\s*function\b", body, re.M),
        "methods": _methods(body),
        "seeds": sorted(set(re.findall(r"set\.seed\(\s*(\d+)", body))),
        "loc": sum(1 for line in body.splitlines() if line.strip()),
    }


# ---- SAS -------------------------------------------------------------------------------------

def _strip_parens(text: str) -> str:
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _sas_ds(name: str) -> str:
    name = name.lower()
    return name if "." in name else f"work.{name}"


def scan_sas(path: Path) -> dict:
    code = path.read_text()
    m = re.match(r"\s*/\*(.*?)\*/", code, re.S)
    header = "\n".join(line.strip().lstrip("*").strip()
                       for line in m.group(1).splitlines()).strip() if m else ""
    body = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    body = re.sub(r"^\s*\*[^;]*;", " ", body, flags=re.M)
    librefs = {lib.lower(): loc for lib, loc in
               re.findall(r"\blibname\s+(\w+)\s+['\"]([^'\"]+)['\"]", body, re.I)}
    created, read = [], []
    for stmt in (s.strip() for s in body.split(";")):
        low = stmt.lower()
        if re.match(r"^data\s+[\w.]", low):
            created += [_sas_ds(d) for d in _strip_parens(stmt[4:]).split()]
        elif re.match(r"^(set|merge)\s", low):
            read += [_sas_ds(d) for d in _strip_parens(stmt.split(None, 1)[1]).split()
                     if "=" not in d]
        for opt, target in re.findall(r"\b(data|out|outmodel|outest)\s*=\s*([\w.]+)", low):
            (read if opt == "data" else created).append(_sas_ds(target))
    files_out = re.findall(r"\boutfile\s*=\s*['\"]([^'\"]+)['\"]", body, re.I)
    files_in = re.findall(r"\b(?:infile|datafile)\s*=?\s*['\"]([^'\"]+)['\"]", body, re.I)
    procs = sorted({p.upper() for p in re.findall(r"\bproc\s+(\w+)", body, re.I)})
    ext = lambda ds: not ds.startswith("work.")  # noqa: E731
    created_set = set(created)
    return {
        "header": header,
        "header_fields": _header_fields(header),
        "libraries": [f"PROC {p}" for p in procs],
        "stdlib": [],
        "librefs": librefs,
        "inputs": sorted({d for d in read if ext(d) and d not in created_set} | set(files_in)),
        "outputs": sorted({d for d in created if ext(d)} | set(files_out)),
        "intermediate": sorted({d for d in created if not ext(d)}),
        "macro_vars": dict(re.findall(r"%let\s+(\w+)\s*=\s*([^;]+);", body, re.I)),
        "functions": [],
        "methods": _methods(body),
        "seeds": sorted(set(re.findall(r"\bseed\s*=\s*(\d+)", body, re.I))),
        "loc": sum(1 for line in body.splitlines() if line.strip()),
    }


SCANNERS = {"R": scan_r, "Python": scan_python, "SAS": scan_sas}


def scan_docs(model_dir: Path, root: Path) -> list[dict]:
    docs = []
    for p in sorted(model_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in DOC_EXTS:
            text = p.read_text(errors="replace") if p.suffix.lower() in {".md", ".txt", ".rst"} \
                else ""
            docs.append({"path": str(p.relative_to(root)), "chars": len(text),
                         "years_mentioned": sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text)))})
    return docs


def scan_model(model_dir: Path, root: Path) -> dict:
    """Scan every code file and documentation file in a model folder."""
    files = []
    for p in sorted(model_dir.rglob("*")):
        lang = LANGUAGE_BY_EXT.get(p.suffix.lower())
        if lang and p.is_file():
            files.append({"path": str(p.relative_to(root)), "language": lang,
                          **SCANNERS[lang](p)})
    if not files:
        raise FileNotFoundError(f"no .R/.py/.sas files in {model_dir}")
    union = lambda key: sorted({x for f in files for x in f.get(key, [])})  # noqa: E731
    return {
        "scanned_at": now_iso(),
        "languages": sorted({f["language"] for f in files}),
        "files": files,
        "libraries": union("libraries"),
        "inputs": union("inputs"),
        "outputs": union("outputs"),
        "methods": union("methods"),
        "seeds": union("seeds"),
        "loc": sum(f["loc"] for f in files),
        "doc_files": scan_docs(model_dir, root),
    }
