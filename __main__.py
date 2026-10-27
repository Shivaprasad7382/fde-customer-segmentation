"""CLI:  python -m segmentation_gen generate --scale starter --out output/starter
        python -m segmentation_gen validate --dir output/starter
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import config as C
from . import export as X
from . import generate as G
from . import validate as V
from . import evaluate as EV


def build_scale(a) -> C.Scale:
    base = C.SCALES[a.scale]
    return C.Scale(a.scale if not (a.customers or a.orders or a.events) else "custom",
                   a.customers or base.customers, a.orders or base.orders, a.events or base.events)


def cmd_generate(a):
    scale = build_scale(a)
    t0 = time.time()
    tables, gt, labels = G.generate_tables(scale, a.seed)
    command = "python -m segmentation_gen " + " ".join(sys.argv[1:])
    fmts = tuple(x for x in a.formats.split(",") if x)
    X.export_all(tables, gt, labels, a.out, scale, a.seed, command, fmts)
    print(f"generated in {time.time() - t0:.1f}s -> {a.out}")
    if not a.skip_validate:
        cmd_validate(argparse.Namespace(dir=a.out))
    if not a.skip_report:
        EV.write_report(a.out)


def cmd_validate(a):
    t0 = time.time()
    s = V.validate_dir(a.dir)
    os.makedirs(os.path.join(a.dir, "reports"), exist_ok=True)
    with open(os.path.join(a.dir, "reports", "validation_summary.json"), "w") as fh:
        json.dump(s, fh, indent=2, default=str)
    with open(os.path.join(a.dir, "reports", "VALIDATION_SUMMARY.md"), "w") as fh:
        fh.write(V.to_markdown(s))
    print(f"validation: {s['overall_status']} ({s['checks_passed']}/{s['checks_total']} checks) in {time.time() - t0:.1f}s")
    if s["overall_status"] != "PASS":
        for c in s["checks"]:
            if c["status"] == "FAIL":
                print("  FAIL", c["check"], {k: c[k] for k in ("flagged", "expected_injected", "unexpected", "missed")},
                      c.get("sample_unexpected"))
        sys.exit(1)


def cmd_report(a):
    EV.write_report(a.dir)


def main(argv=None):
    p = argparse.ArgumentParser(prog="segmentation_gen")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate dataset + validate + evaluation report")
    g.add_argument("--scale", choices=list(C.SCALES), default="starter")
    g.add_argument("--customers", type=int)
    g.add_argument("--orders", type=int)
    g.add_argument("--events", type=int)
    g.add_argument("--seed", type=int, default=C.SEED)
    g.add_argument("--out", required=True)
    g.add_argument("--formats", default="csv,json,sql", help="comma list of csv,json,sql (csv always written)")
    g.add_argument("--skip-validate", action="store_true")
    g.add_argument("--skip-report", action="store_true")
    g.set_defaults(fn=cmd_generate)
    v = sub.add_parser("validate", help="validate an exported dataset directory")
    v.add_argument("--dir", required=True)
    v.set_defaults(fn=cmd_validate)
    r = sub.add_parser("report", help="write the evaluation report for a dataset directory")
    r.add_argument("--dir", required=True)
    r.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
