#!/usr/bin/env python
"""Step 10 - reproducibility: regenerate twice with the same seed and compare row counts + SHA-256 checksums.

    python scripts/reproduce.py --scale starter --out output/reproducibility
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from segmentation_gen import config as C  # noqa: E402
from segmentation_gen import export as X  # noqa: E402
from segmentation_gen import generate as G  # noqa: E402


def run(scale, seed, path, fmts):
    tables, gt, labels = G.generate_tables(scale, seed, log=lambda *_: None)
    return X.export_all(tables, gt, labels, path, scale, seed, f"reproduce.py --scale {scale.name} --seed {seed}",
                        fmts, log=lambda *_: None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=list(C.SCALES), default="starter")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--formats", default="csv,json,sql")
    ap.add_argument("--out", default=None, help="folder for reproducibility_report.json")
    a = ap.parse_args()
    scale, fmts = C.SCALES[a.scale], tuple(a.formats.split(","))
    tmp = tempfile.mkdtemp(prefix="repro_")
    try:
        m = [run(scale, a.seed, os.path.join(tmp, f"run{i}"), fmts)["files"] for i in (1, 2)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows, ok = [], True
    for f in sorted(m[0]):
        same = m[0][f]["sha256"] == m[1][f]["sha256"] and m[0][f]["rows"] == m[1][f]["rows"]
        ok &= same
        rows.append({"file": f, "rows_run1": m[0][f]["rows"], "rows_run2": m[1][f]["rows"],
                     "sha256_run1": m[0][f]["sha256"], "sha256_run2": m[1][f]["sha256"], "identical": same})
    rep = {"seed": a.seed, "scale": a.scale, "all_identical": ok, "files": rows}
    print(f"{'file':44s} {'rows':>10s}  identical")
    for r in rows:
        print(f"{r['file']:44s} {str(r['rows_run1']):>10s}  {r['identical']}")
    print("REPRODUCIBLE" if ok else "NOT REPRODUCIBLE")
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "reproducibility_report.json"), "w") as fh:
            json.dump(rep, fh, indent=2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
