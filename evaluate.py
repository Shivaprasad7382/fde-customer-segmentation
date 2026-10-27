"""Measurable outputs: segment distribution, S1->S2 migration, action funnel, uplift vs holdout control."""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

from . import config as C


def _read(root, name):
    return pd.read_csv(os.path.join(root, "data", f"{name}.csv"), dtype=str, keep_default_na=False, na_values=[""])


def compute_report(root: str) -> dict:
    rules = _read(root, "segment_rules")
    m = _read(root, "segment_memberships")
    out = _read(root, "action_outcomes")
    cat = _read(root, "action_catalog")
    code = dict(zip(rules["segment_id"], rules["segment_code"]))
    excl = set(rules.loc[rules["exclusive_group"] != "NONE", "segment_id"])
    m["code"] = m["segment_id"].map(code)

    def active(d):
        return m[(m["effective_from"] <= d) & (m["effective_to"].isna() | (m["effective_to"] > d))]

    sizes = {}
    for label, d in (("snapshot_1_2026-08-01", str(C.SNAP1)), ("snapshot_2_2026-10-23", str(C.SNAP2))):
        a = active(d)
        sizes[label] = a["code"].value_counts().to_dict()
    # migration (exclusive group, first membership per customer to keep injected duplicates out)
    a1 = active(str(C.SNAP1))
    a1 = a1[a1["segment_id"].isin(excl)].drop_duplicates("customer_id").set_index("customer_id")["code"]
    a2 = active(str(C.SNAP2))
    a2 = a2[a2["segment_id"].isin(excl)].drop_duplicates("customer_id").set_index("customer_id")["code"]
    mig = pd.crosstab(a1, a2.reindex(a1.index).fillna("NOT_YET_REGISTERED")).to_dict(orient="index")

    cc = cat.set_index("catalog_id")["action_code"]
    out["code"] = out["catalog_id"].map(cc)
    out["success"] = pd.to_numeric(out["success_flag"], errors="coerce")
    out["rev"] = pd.to_numeric(out["attributed_revenue"], errors="coerce")
    funnel = {}
    for c, g in out.groupby("code"):
        sent = g[g["decision"] == "SEND"]
        deliv = sent[sent["delivery_status"] == "DELIVERED"]
        funnel[c] = {
            "recommended": int(len(g)), "suppressed": int((g["decision"] == "SUPPRESS").sum()),
            "suppression_reasons": g["suppression_reason"].value_counts().to_dict(),
            "holdout": int((g["decision"] == "HOLDOUT").sum()), "sent": int(len(sent)),
            "delivered": int(len(deliv)),
            "engaged_or_success": int(deliv["outcome_status"].isin(["ENGAGED", "SUCCESS"]).sum()),
            "success": int((deliv["success"] == 1).sum()),
            "success_rate_delivered": round(float((deliv["success"] == 1).mean()), 4) if len(deliv) else None,
        }
    lift = {}
    for c, g in out[out["success_metric"] == "ORDER_14D"].groupby("code"):
        t = g[(g["decision"] == "SEND") & (g["delivery_status"] == "DELIVERED")]
        h = g[g["decision"] == "HOLDOUT"]
        if len(t) < 30 or len(h) < 30:
            continue
        pt, ph = float((t["success"] == 1).mean()), float((h["success"] == 1).mean())
        se = math.sqrt(pt * (1 - pt) / len(t) + ph * (1 - ph) / len(h))
        lift[c] = {"treated_n": int(len(t)), "holdout_n": int(len(h)), "treated_conversion": round(pt, 4),
                   "holdout_conversion": round(ph, 4), "absolute_lift": round(pt - ph, 4),
                   "ci95": [round(pt - ph - 1.96 * se, 4), round(pt - ph + 1.96 * se, 4)],
                   "incremental_revenue_per_recipient": round(float(t["rev"].fillna(0).mean() - h["rev"].fillna(0).mean()), 2)}
    allt = out[(out["success_metric"] == "ORDER_14D") & (out["decision"] == "SEND") & (out["delivery_status"] == "DELIVERED")]
    allh = out[(out["success_metric"] == "ORDER_14D") & (out["decision"] == "HOLDOUT")]
    overall = None
    if len(allt) and len(allh):
        pt, ph = float((allt["success"] == 1).mean()), float((allh["success"] == 1).mean())
        se = math.sqrt(pt * (1 - pt) / len(allt) + ph * (1 - ph) / len(allh))
        overall = {"treated_n": int(len(allt)), "holdout_n": int(len(allh)), "treated_conversion": round(pt, 4),
                   "holdout_conversion": round(ph, 4), "absolute_lift": round(pt - ph, 4),
                   "ci95": [round(pt - ph - 1.96 * se, 4), round(pt - ph + 1.96 * se, 4)]}
    decisions = out["decision"].value_counts().to_dict()
    return {"segment_sizes": sizes, "segment_migration_s1_to_s2": mig, "action_funnel": funnel,
            "uplift_vs_holdout_by_action": lift, "uplift_vs_holdout_overall": overall,
            "decision_counts": decisions,
            "suppression_rate": round(decisions.get("SUPPRESS", 0) / max(len(out), 1), 4)}


def to_markdown(r: dict) -> str:
    L = ["# Evaluation report", "",
         "Computed from the exported CSVs only (no ground truth). Injected anomalies are included, so figures differ "
         "from a perfectly clean run by a fraction of a percent.", "", "## Segment sizes (active memberships)", ""]
    for snap, d in r["segment_sizes"].items():
        L += [f"**{snap}**", "", "| segment | customers |", "|---|---|"]
        L += [f"| {k} | {v:,} |" for k, v in sorted(d.items(), key=lambda kv: -kv[1])]
        L.append("")
    L += ["## Action funnel", "", "| action | recommended | suppressed | holdout | sent | delivered | engaged/success | success | success rate |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k, v in r["action_funnel"].items():
        L.append(f"| {k} | {v['recommended']:,} | {v['suppressed']:,} | {v['holdout']:,} | {v['sent']:,} | {v['delivered']:,} | "
                 f"{v['engaged_or_success']:,} | {v['success']:,} | {v['success_rate_delivered']} |")
    L += ["", "## Uplift vs holdout (ORDER_14D actions)", "", "| action | treated n | holdout n | treated CR | holdout CR | lift | 95% CI | incr. revenue / recipient |",
          "|---|---|---|---|---|---|---|---|"]
    for k, v in r["uplift_vs_holdout_by_action"].items():
        L.append(f"| {k} | {v['treated_n']:,} | {v['holdout_n']:,} | {v['treated_conversion']:.2%} | {v['holdout_conversion']:.2%} | "
                 f"{v['absolute_lift']:+.2%} | [{v['ci95'][0]:+.2%}, {v['ci95'][1]:+.2%}] | {v['incremental_revenue_per_recipient']} |")
    o = r["uplift_vs_holdout_overall"]
    if o:
        L += ["", f"**Overall:** treated {o['treated_conversion']:.2%} vs holdout {o['holdout_conversion']:.2%} "
                  f"(lift {o['absolute_lift']:+.2%}, 95% CI [{o['ci95'][0]:+.2%}, {o['ci95'][1]:+.2%}])."]
    L += ["", f"Decisions: {r['decision_counts']} - suppression rate {r['suppression_rate']:.1%}.", ""]
    return "\n".join(L)


def write_report(root: str):
    r = compute_report(root)
    d = os.path.join(root, "reports")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "evaluation_report.json"), "w") as fh:
        json.dump(r, fh, indent=2, default=str)
    with open(os.path.join(d, "EVALUATION_REPORT.md"), "w") as fh:
        fh.write(to_markdown(r))
    print(f"evaluation report written to {d}")
    return r
