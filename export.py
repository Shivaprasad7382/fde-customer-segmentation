"""Serialisation: CSV (canonical), JSON-lines, SQL seed + DDL, data dictionary, manifest and checksums."""
from __future__ import annotations

import hashlib
import json
import os
import platform

import numpy as np
import pandas as pd

from . import config as C
from . import schema as S


def format_table(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return an all-string frame in schema column order (nulls -> NaN)."""
    t = S.TABLES[name]
    out = {}
    for f in t.fields:
        s = df[f.name]
        if f.type == "date":
            v = pd.to_datetime(s).dt.strftime("%Y-%m-%d")
        elif f.type == "datetime":
            v = pd.to_datetime(s).dt.strftime("%Y-%m-%d %H:%M:%S")
        elif f.type == "decimal":
            num = pd.to_numeric(s).round(f.scale)
            v = num.map(lambda x, sc=f.scale: np.nan if pd.isna(x) else f"{x:.{sc}f}")
        elif f.type in ("int", "bool"):
            v = pd.to_numeric(s).astype("Int64").astype("string")
        else:
            v = s.astype("string")
        out[f.name] = v.astype("object").where(v.notna(), np.nan)
    return pd.DataFrame(out)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sql_values(f: pd.DataFrame, name: str) -> pd.Series:
    t = S.TABLES[name]
    cols = []
    for fld in t.fields:
        s = f[fld.name]
        if fld.type in ("int", "bool", "decimal"):
            lit = s.fillna("NULL")
        else:
            lit = "'" + s.astype("string").str.replace("'", "''", regex=False) + "'"
            lit = lit.astype("object").where(s.notna(), "NULL")
        cols.append(lit.astype(str))
    row = cols[0]
    for c in cols[1:]:
        row = row + "," + c
    return "(" + row + ")"


def write_sql_seed(formatted: dict, path: str, chunk: int = 500):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("-- Seed data (generated). Load after schema.sql. FK-safe table order.\nBEGIN TRANSACTION;\n")
        for name in S.LOAD_ORDER:
            f = formatted[name]
            vals = _sql_values(f, name).tolist()
            cols = ", ".join(S.TABLES[name].columns)
            for i in range(0, len(vals), chunk):
                fh.write(f"INSERT INTO {name} ({cols}) VALUES\n" + ",\n".join(vals[i:i + chunk]) + ";\n")
        fh.write("COMMIT;\n")


def export_all(tables: dict, gt: pd.DataFrame, labels: dict, root: str, scale: C.Scale, seed: int,
               command: str, formats=("csv", "json", "sql"), log=print) -> dict:
    data_dir = os.path.join(root, "data")
    gt_dir = os.path.join(root, "ground_truth")
    sch_dir = os.path.join(root, "schema")
    for d in (data_dir, gt_dir, sch_dir):
        os.makedirs(d, exist_ok=True)

    formatted = {n: format_table(tables[n], n) for n in S.TABLES}
    files = {}
    for n, f in formatted.items():
        p = os.path.join(data_dir, f"{n}.csv")
        f.to_csv(p, index=False, na_rep="", lineterminator="\n")
        files[f"data/{n}.csv"] = (p, len(f))
        if "json" in formats:
            pj = os.path.join(data_dir, f"{n}.jsonl")
            f.to_json(pj, orient="records", lines=True, force_ascii=False)
            files[f"data/{n}.jsonl"] = (pj, len(f))
    if "sql" in formats:
        p = os.path.join(sch_dir, "schema.sql")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(S.ddl())
        files["schema/schema.sql"] = (p, None)
        p = os.path.join(data_dir, "seed.sql")
        write_sql_seed(formatted, p)
        files["data/seed.sql"] = (p, sum(len(f) for f in formatted.values()))

    # ground truth (kept in a separate folder; never referenced by data/ tables)
    gp = os.path.join(gt_dir, "ground_truth.csv")
    gt.to_csv(gp, index=False, lineterminator="\n")
    files["ground_truth/ground_truth.csv"] = (gp, len(gt))
    with open(os.path.join(gt_dir, "ground_truth.json"), "w", encoding="utf-8") as fh:
        json.dump({"note": "Known injected anomalies. For evaluation only; never use as model features.",
                   "counts_by_issue_type": gt["issue_type"].value_counts().sort_index().to_dict(),
                   "records": json.loads(gt.to_json(orient="records"))}, fh, indent=2)
    files["ground_truth/ground_truth.json"] = (os.path.join(gt_dir, "ground_truth.json"), len(gt))
    for n, df in labels.items():
        p = os.path.join(gt_dir, f"{n}.csv")
        df.to_csv(p, index=False, lineterminator="\n")
        files[f"ground_truth/{n}.csv"] = (p, len(df))

    # data dictionary
    pd.DataFrame(S.dictionary_rows()).to_csv(os.path.join(sch_dir, "data_dictionary.csv"), index=False, lineterminator="\n")
    with open(os.path.join(sch_dir, "data_dictionary.json"), "w", encoding="utf-8") as fh:
        json.dump(S.dictionary_rows(), fh, indent=2)
    with open(os.path.join(sch_dir, "DATA_DICTIONARY.md"), "w", encoding="utf-8") as fh:
        fh.write(S.dictionary_markdown())

    manifest = {
        "dataset": "customer-segmentation-synthetic", "generator_version": "1.0.0", "seed": seed,
        "scale": {"name": scale.name, "customers": scale.customers, "orders": scale.orders, "events": scale.events},
        "generator_command": command,
        "calendar": {"history_start": str(C.HISTORY_START), "data_end": str(C.DATA_END),
                     "snapshot_1": str(C.SNAP1), "snapshot_2": str(C.SNAP2)},
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "files": {k: {"rows": r, "bytes": os.path.getsize(p), "sha256": sha256_file(p)}
                  for k, (p, r) in sorted(files.items())},
    }
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    source_manifest = {
        "external_sources": [],
        "statement": ("No external or public datasets were used. All records are synthetic, generated by "
                      "segmentation_gen from a fixed seed; no real personal data is involved. Product taxonomy "
                      "and category names are invented for illustration."),
        "fields_discarded": [], "transformations": [], "licence": "Synthetic data released under the repository licence (MIT)."}
    with open(os.path.join(root, "source_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(source_manifest, fh, indent=2)
    log(f"exported {len(files)} files to {root}")
    return manifest
