"""Deliberate, *labelled* edge cases.

Each injector touches only the rows it labels, returns ground-truth records, and never changes unrelated rows.
Issue types (the four required negative scenarios):
  CONFLICTING_EXCLUSIVE_SEGMENT : a customer holds two active memberships in the exclusive lifecycle group
  ACTION_VIOLATES_CONSENT       : a SEND to a customer with no active consent for the channel / purpose
  RETURN_NOT_NETTED             : membership net_spend ignores returns (= gross_spend)
  OUTCOME_WRONG_ACTION          : outcome logged against an action that is not eligible for the segment
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

GT_COLUMNS = ["gt_id", "table_name", "record_id", "customer_id", "issue_type", "field_affected",
              "observed_value", "expected_value", "detail"]


def _gt(table, record_id, customer_id, issue, field, observed, expected, detail=""):
    return dict(table_name=table, record_id=record_id, customer_id=customer_id, issue_type=issue,
                field_affected=field, observed_value=observed, expected_value=expected, detail=detail)


def inject_consent_violations(rng, out: pd.DataFrame, catalog: pd.DataFrame, cust: pd.DataFrame, n: int):
    """Flip consent-suppressed recommendations to SEND (the customer has no usable consent)."""
    cat = catalog.set_index("catalog_id")
    cand = out[out["suppression_reason"].isin(["NO_CONSENT", "CONSENT_WITHDRAWN"])]
    busy = set(out.loc[out["decision"] != "SUPPRESS", "cidx"])         # keep exactly one SEND per victim
    cand = cand[~cand["cidx"].isin(busy) & ~cand["other_block"]].drop_duplicates("cidx")
    pick = cand.iloc[rng.permutation(len(cand))[:n]]
    gt = []
    pref = cust["preferred_channel"].to_numpy()
    for i, row in pick.iterrows():
        allowed = cat.loc[row["catalog_id"], "allowed_channels"].split("|")
        ch = pref[row["cidx"]] if pref[row["cidx"]] in allowed else allowed[0]
        gt.append(_gt("action_outcomes", row["action_id"], row["customer_id"], "ACTION_VIOLATES_CONSENT",
                      "decision", "SEND", "SUPPRESS", f"original suppression_reason={row['suppression_reason']}"))
        out.loc[i, ["decision", "suppression_reason", "channel", "delivery_status"]] = ["SEND", None, ch, "DELIVERED"]
        out.loc[i, "sent_s"] = row["rec_s"] + int(rng.integers(1800, 18000))
        out.loc[i, "outcome_status"] = ""
    return out, gt


def inject_conflicting_segments(rng, mem: pd.DataFrame, rules: pd.DataFrame, n: int):
    """Add a second, different exclusive-group membership for the same customer at the same time."""
    excl = rules[rules["exclusive_group"] != "NONE"]
    excl_ids = excl["segment_id"].tolist()
    cur = mem[(mem["effective_to"].isna()) & mem["segment_id"].isin(excl_ids)]
    cur = cur.iloc[rng.permutation(len(cur))]
    existing = set(mem["membership_id"])
    code = dict(zip(rules["segment_id"], rules["segment_code"]))
    new_rows, gt = [], []
    for _, r in cur.iterrows():
        if len(new_rows) >= n:
            break
        others = [s for s in excl_ids if s != r["segment_id"] and f"MEM-{s}-{r['customer_id']}" not in existing]
        if not others:
            continue
        s = others[int(rng.integers(0, len(others)))]
        row = r.copy()
        row["segment_id"] = s
        row["membership_id"] = f"MEM-{s}-{r['customer_id']}"
        row["assignment_reason"] = (f"{code[s]} (priority {int(excl.loc[excl.segment_id == s, 'priority'].iloc[0])}, "
                                    f"rule v1): R={r['r_score']} F={r['f_score']} M={r['m_score']}")
        existing.add(row["membership_id"])
        new_rows.append(row)
        gt.append(_gt("segment_memberships", row["membership_id"], r["customer_id"],
                      "CONFLICTING_EXCLUSIVE_SEGMENT", "segment_id", code[s], code[r["segment_id"]],
                      f"customer also holds {r['membership_id']}"))
    if new_rows:
        mem = pd.concat([mem, pd.DataFrame(new_rows)], ignore_index=True)
    return mem, gt


def inject_return_not_netted(rng, mem: pd.DataFrame, rules: pd.DataFrame, n: int):
    """Record gross_spend as net_spend for customers with a material return value."""
    excl_ids = rules.loc[rules["exclusive_group"] != "NONE", "segment_id"]
    cand = mem[mem["segment_id"].isin(excl_ids) & (mem["returned_amount"] >= 20.0)]
    cand = cand[~cand["membership_id"].duplicated(keep=False)]
    pick = cand.iloc[rng.permutation(len(cand))[:n]]
    gt = []
    for i, r in pick.iterrows():
        gt.append(_gt("segment_memberships", r["membership_id"], r["customer_id"], "RETURN_NOT_NETTED",
                      "net_spend", f"{r['gross_spend']:.2f}", f"{r['net_spend']:.2f}",
                      f"returned_amount={r['returned_amount']:.2f} not deducted"))
        mem.loc[i, "net_spend"] = r["gross_spend"]
    return mem, gt


def inject_wrong_action(rng, out: pd.DataFrame, catalog: pd.DataFrame, elig: pd.DataFrame, mem: pd.DataFrame, n: int):
    """Credit a (preferably converted) outcome to an action that is NOT eligible for the membership's segment.

    The substitute has the same purpose / success metric, is a non-discount action and allows the channel,
    so no *other* rule is broken: the only defect is the wrong attribution.
    """
    cat = catalog.set_index("catalog_id")
    seg_of = mem.drop_duplicates("membership_id").set_index("membership_id")["segment_id"]
    eligible = elig.groupby("segment_id")["catalog_id"].apply(set).to_dict()
    cand = out[(out["decision"] == "SEND") & (out["success_metric"] == "ORDER_14D")]
    conv = cand[cand["success_flag"] == 1]
    rest = cand[cand["success_flag"] != 1]
    order = pd.concat([conv.iloc[rng.permutation(len(conv))], rest.iloc[rng.permutation(len(rest))]])
    gt, done = [], 0
    for i, r in order.iterrows():
        if done >= n:
            break
        seg = seg_of.get(r["membership_id"])
        opts = [c for c, row in cat.iterrows()
                if c not in eligible.get(seg, set()) and c != r["catalog_id"]
                and row["purpose"] == "MARKETING" and row["success_metric"] == "ORDER_14D"
                and not row["is_discount"] and r["channel"] in row["allowed_channels"].split("|")]
        if not opts:
            continue
        new = opts[int(rng.integers(0, len(opts)))]
        gt.append(_gt("action_outcomes", r["action_id"], r["customer_id"], "OUTCOME_WRONG_ACTION", "catalog_id",
                      new, r["catalog_id"], f"segment_id={seg}; success_flag={r['success_flag']}"))
        out.loc[i, "catalog_id"] = new
        done += 1
    return out, gt
