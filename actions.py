"""Action catalogue, eligibility, suppression rules, outcome evaluation.

Suppression rules (applied in this order, first hit wins; every decision is logged):
  1. NO_CONSENT / CONSENT_WITHDRAWN : no GRANTED consent for any allowed channel (for the action's purpose)
                                      at the recommendation date. WITHDRAWN if the customer ever withdrew.
  2. OPEN_SUPPORT_CASE              : MARKETING actions are suppressed while a support case is open.
  3. HIGH_SERVICE_COST_DISCOUNT_BLOCK: discount actions are never sent to HIGH_SERVICE_COST accounts.
  4. FREQUENCY_CAP                  : at most ``contact_frequency_cap_30d`` SEND per customer per wave
                                      (exclusive-segment action has precedence over overlay actions).
Survivors of attribution-eligible (ORDER_14D) actions are randomly held out at 10% as the control group.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .entities import END_I, SNAP1_I, consent_state

CATALOG_COLUMNS = ["catalog_id", "action_code", "action_name", "action_type", "purpose", "allowed_channels",
                   "is_discount", "discount_pct", "success_metric", "attribution_window_days",
                   "requires_holdout", "cost_per_contact", "status", "effective_from", "effective_to"]


def build_catalog() -> pd.DataFrame:
    eff = str(C.RULE_EFFECTIVE_FROM)
    spec = [
        ("WELCOME_SERIES", "Welcome series", "ONBOARDING", "MARKETING", "EMAIL|PUSH", 0, None, "ORDER_14D", 1, 0.05),
        ("VIP_EARLY_ACCESS", "VIP early access", "RETENTION", "MARKETING", "EMAIL|PUSH|SMS", 0, None, "ORDER_14D", 1, 0.08),
        ("LOYALTY_REWARD", "Loyalty reward points", "RETENTION", "MARKETING", "EMAIL|PUSH", 0, None, "ORDER_14D", 1, 0.06),
        ("CROSS_SELL_BUNDLE", "Cross-sell bundle (10% off)", "UPSELL", "MARKETING", "EMAIL|PUSH|SMS", 1, 10, "ORDER_14D", 1, 0.07),
        ("WINBACK_OFFER_10", "Win-back offer (10% off)", "WINBACK", "MARKETING", "EMAIL|SMS", 1, 10, "ORDER_14D", 1, 0.09),
        ("REACTIVATION_DISCOUNT_20", "Reactivation discount (20% off)", "WINBACK", "MARKETING", "EMAIL|SMS", 1, 20, "ORDER_14D", 1, 0.10),
        ("PROSPECT_NURTURE", "Prospect nurture content", "ACQUISITION", "MARKETING", "EMAIL|PUSH", 0, None, "ORDER_14D", 1, 0.04),
        ("SERVICE_OUTREACH_CALL", "Proactive service outreach", "SERVICE", "SERVICE", "PHONE|EMAIL", 0, None, "CASE_FREE_30D", 0, 4.50),
        ("RETURN_POLICY_GUIDE", "Returns & fit guide", "EDUCATION", "SERVICE", "EMAIL", 0, None, "NO_RETURN_30D", 0, 0.03),
    ]
    rows = []
    for i, (code, name, typ, purpose, chans, disc, dpct, metric, hold, cost) in enumerate(spec, start=1):
        rows.append({"catalog_id": f"CAT-{i:04d}", "action_code": code, "action_name": name, "action_type": typ,
                     "purpose": purpose, "allowed_channels": chans, "is_discount": disc, "discount_pct": dpct,
                     "success_metric": metric, "attribution_window_days": 14 if metric == "ORDER_14D" else 30,
                     "requires_holdout": hold, "cost_per_contact": cost, "status": "ACTIVE",
                     "effective_from": eff, "effective_to": None})
    return pd.DataFrame(rows, columns=CATALOG_COLUMNS)


ELIGIBILITY = {  # segment_code -> [action_code in priority order]
    "PROSPECT": ["PROSPECT_NURTURE"],
    "NEW_CUSTOMER": ["WELCOME_SERIES"],
    "CHAMPIONS": ["VIP_EARLY_ACCESS", "LOYALTY_REWARD"],
    "LOYAL": ["LOYALTY_REWARD", "CROSS_SELL_BUNDLE"],
    "POTENTIAL_LOYALIST": ["CROSS_SELL_BUNDLE"],
    "AT_RISK": ["WINBACK_OFFER_10"],
    "HIBERNATING": ["REACTIVATION_DISCOUNT_20", "WINBACK_OFFER_10"],
    "HIGH_SERVICE_COST": ["SERVICE_OUTREACH_CALL"],
    "HIGH_RETURNS": ["RETURN_POLICY_GUIDE"],
}


def build_eligibility(rules: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    seg = dict(zip(rules["segment_code"], rules["segment_id"]))
    cat = dict(zip(catalog["action_code"], catalog["catalog_id"]))
    rows = []
    for code, actions in ELIGIBILITY.items():
        for p, a in enumerate(actions, start=1):
            rows.append({"segment_id": seg[code], "catalog_id": cat[a], "priority": p,
                         "effective_from": str(C.RULE_EFFECTIVE_FROM), "effective_to": None})
    df = pd.DataFrame(rows)
    df.insert(0, "eligibility_id", [f"ELG-{i:04d}" for i in range(1, len(df) + 1)])
    return df


# --------------------------------------------------------------------------------------
# Planning (recommendation + suppression + send decision)
# --------------------------------------------------------------------------------------
def plan_actions(rng, cust, memberships, rules, catalog, elig, consents, cases_pre) -> pd.DataFrame:
    """Decide, for every snapshot-1 membership, which action is recommended and whether it may be sent."""
    n_cust = len(cust)
    top = elig[elig["priority"] == 1][["segment_id", "catalog_id"]]
    rseg = rules.set_index("segment_id")
    recs = memberships[["membership_id", "customer_id", "segment_id"]].merge(top, on="segment_id", how="inner")
    recs = recs.merge(catalog[["catalog_id", "action_code", "purpose", "allowed_channels", "is_discount",
                               "requires_holdout", "success_metric"]], on="catalog_id")
    recs["cidx"] = recs["customer_id"].str[4:].astype(int) - 1
    recs["is_overlay"] = (recs["segment_id"].map(rseg["exclusive_group"]) == "NONE").astype(int)
    recs["seg_prio"] = recs["segment_id"].map(rseg["priority"]).astype(int)
    recs = recs.sort_values(["cidx", "is_overlay", "seg_prio"], kind="mergesort").reset_index(drop=True)
    n = len(recs)
    cidx = recs["cidx"].to_numpy()
    pref = cust["preferred_channel"].to_numpy()[cidx]
    cap = cust["contact_frequency_cap_30d"].to_numpy()[cidx]

    granted, withdrawn = consent_state(consents, n_cust, SNAP1_I)
    chosen = np.full(n, "", dtype=object)
    ever_wd = np.zeros(n, dtype=bool)
    for cid, g in recs.groupby("catalog_id"):
        pos = g.index.to_numpy()
        allowed = g["allowed_channels"].iloc[0].split("|")
        purpose = g["purpose"].iloc[0]
        ci = cidx[pos]
        sel = np.full(len(pos), "", dtype=object)
        for ch in allowed:                                  # 1) the customer's preferred channel
            ok = granted[(ch, purpose)][ci] & (pref[pos] == ch) & (sel == "")
            sel[ok] = ch
        for ch in [c for c in C.CONTACT_FIXED_ORDER if c in allowed]:   # 2) fixed fallback order
            ok = granted[(ch, purpose)][ci] & (sel == "")
            sel[ok] = ch
        chosen[pos] = sel
        wd = np.zeros(len(pos), dtype=bool)
        for ch in allowed:
            wd |= withdrawn[(ch, purpose)][ci]
        ever_wd[pos] = wd

    rec_s = SNAP1_I * 86400 + 6 * 3600 + rng.integers(0, 3600, n)
    reason = np.full(n, "", dtype=object)
    no = chosen == ""
    reason[no & ever_wd] = "CONSENT_WITHDRAWN"
    reason[no & ~ever_wd] = "NO_CONSENT"

    # open support case at recommendation time (cases pre-dating the snapshot only exist at this point)
    max_close = np.full(n_cust, -1, dtype="int64")
    if len(cases_pre):
        cl = cases_pre[cases_pre["opened_s"] <= SNAP1_I * 86400 + 7 * 3600]
        cl_close = np.where(cl["closed_s"] < 0, 2 ** 60, cl["closed_s"])
        s = pd.Series(cl_close).groupby(cl["cidx"].to_numpy()).max()
        max_close[s.index.to_numpy()] = s.to_numpy()
    open_case = max_close[cidx] > rec_s
    marketing = (recs["purpose"] == "MARKETING").to_numpy()
    m = (reason == "") & marketing & open_case
    reason[m] = "OPEN_SUPPORT_CASE"
    other_block = marketing & open_case          # kept so injected anomalies never break a second rule

    hs_id = rules.loc[rules["segment_code"] == "HIGH_SERVICE_COST", "segment_id"].iloc[0]
    hs = np.zeros(n_cust, dtype=bool)
    hs[memberships.loc[memberships["segment_id"] == hs_id, "customer_id"].str[4:].astype(int).to_numpy() - 1] = True
    m = (reason == "") & recs["is_discount"].to_numpy().astype(bool) & hs[cidx]
    reason[m] = "HIGH_SERVICE_COST_DISCOUNT_BLOCK"
    other_block = other_block | (recs["is_discount"].to_numpy().astype(bool) & hs[cidx])

    surv = reason == ""
    rank = pd.Series(surv.astype(int)).groupby(cidx).cumsum().to_numpy()
    m = surv & (rank > cap)
    reason[m] = "FREQUENCY_CAP"

    surv = reason == ""
    holdout = surv & recs["requires_holdout"].to_numpy().astype(bool) & (rng.random(n) < C.HOLDOUT_RATE)
    decision = np.where(reason != "", "SUPPRESS", np.where(holdout, "HOLDOUT", "SEND"))

    send = decision == "SEND"
    p_deliv = np.array([C.DELIVERY_RATE.get(c, 1.0) for c in chosen])
    delivered = rng.random(n) < p_deliv
    delivery = np.where(send, np.where(delivered, "DELIVERED", "FAILED"), "NOT_APPLICABLE")
    sent_s = np.where(send, rec_s + rng.integers(1800, 18000, n), -1)
    channel = np.where(chosen == "", None, chosen)
    status = np.where(decision == "SUPPRESS", "SUPPRESSED",
                      np.where(decision == "HOLDOUT", "HOLDOUT",
                               np.where(delivery == "FAILED", "DELIVERY_FAILED", "")))
    out = pd.DataFrame({
        "cidx": cidx, "customer_id": recs["customer_id"], "membership_id": recs["membership_id"],
        "catalog_id": recs["catalog_id"], "rec_s": rec_s, "decision": decision,
        "suppression_reason": np.where(reason == "", None, reason), "channel": channel, "sent_s": sent_s,
        "delivery_status": delivery, "success_metric": recs["success_metric"], "outcome_status": status,
        "other_block": other_block})
    return out


def assign_action_ids(out: pd.DataFrame) -> pd.DataFrame:
    out = out.sort_values(["rec_s", "cidx", "membership_id"], kind="mergesort").reset_index(drop=True)
    out.insert(0, "action_id", [f"ACTN-2026-{i:06d}" for i in range(1, len(out) + 1)])
    return out


# --------------------------------------------------------------------------------------
# Campaign response model (documented rule) -> which delivered ORDER_14D sends convert
# --------------------------------------------------------------------------------------
def response_probability(code: np.ndarray, channel: np.ndarray, eng: np.ndarray) -> np.ndarray:
    base = np.array([C.RESPONSE_BASE.get(c, 0.0) for c in code])
    mult = np.array([C.CHANNEL_RESPONSE_MULT.get(c, 1.0) for c in channel])
    return np.minimum(0.5, base * (0.6 + 0.8 * eng) * mult)


# --------------------------------------------------------------------------------------
# Outcome evaluation (derived from orders / cases, never drawn)
# --------------------------------------------------------------------------------------
def evaluate_outcomes(out: pd.DataFrame, orders: pd.DataFrame, cases: pd.DataFrame) -> pd.DataFrame:
    """Return success_flag (Int64), attributed_order_id, attributed_revenue for each outcome row.

    out: action_id, customer_id, decision, delivery_status, success_metric, recommended_at, sent_at (datetimes)
    orders: order_id, customer_id, order_date, order_type, net_amount | cases: customer_id, opened_at
    """
    res = pd.DataFrame({"action_id": out["action_id"].to_numpy()})
    res["success_flag"] = pd.array([pd.NA] * len(out), dtype="Int64")
    res["attributed_order_id"] = None
    res["attributed_revenue"] = np.nan

    sent = out["decision"].eq("SEND") & out["delivery_status"].eq("DELIVERED")
    elig = sent | out["decision"].eq("HOLDOUT")
    ref = out["sent_at"].where(out["decision"].eq("SEND"), out["recommended_at"])
    base = out[["action_id", "customer_id"]].assign(ref=ref.to_numpy(), ref_date=ref.dt.normalize().to_numpy())
    idx = res.set_index("action_id")

    # ORDER_14D --------------------------------------------------------------------
    sel = base[elig.to_numpy() & out["success_metric"].eq("ORDER_14D").to_numpy()]
    sales = orders.loc[orders["order_type"] == "SALE", ["order_id", "customer_id", "order_date", "net_amount"]]
    j = sel.merge(sales, on="customer_id", how="left")
    win = pd.Timedelta(days=C.ATTRIBUTION_WINDOW_DAYS)
    j = j[(j["order_date"] >= j["ref_date"]) & (j["order_date"] <= j["ref_date"] + win)]
    j = j.sort_values(["action_id", "order_date", "order_id"]).drop_duplicates("action_id")
    idx.loc[sel["action_id"], "success_flag"] = 0
    idx.loc[j["action_id"], "success_flag"] = 1
    idx.loc[j["action_id"], "attributed_order_id"] = j["order_id"].to_numpy()
    idx.loc[j["action_id"], "attributed_revenue"] = j["net_amount"].to_numpy()

    # CASE_FREE_30D ----------------------------------------------------------------
    sel = base[sent.to_numpy() & out["success_metric"].eq("CASE_FREE_30D").to_numpy()]
    if len(sel):
        cj = sel.merge(cases[["customer_id", "opened_at"]], on="customer_id", how="inner")
        w30 = pd.Timedelta(days=C.SERVICE_METRIC_WINDOW_DAYS)
        hit = cj[(cj["opened_at"] > cj["ref"]) & (cj["opened_at"] <= cj["ref"] + w30)]["action_id"].unique()
        idx.loc[sel["action_id"], "success_flag"] = 1
        idx.loc[hit, "success_flag"] = 0

    # NO_RETURN_30D ----------------------------------------------------------------
    sel = base[sent.to_numpy() & out["success_metric"].eq("NO_RETURN_30D").to_numpy()]
    if len(sel):
        rets = orders.loc[orders["order_type"] == "RETURN", ["customer_id", "order_date"]]
        rj = sel.merge(rets, on="customer_id", how="inner")
        w30 = pd.Timedelta(days=C.SERVICE_METRIC_WINDOW_DAYS)
        hit = rj[(rj["order_date"] > rj["ref_date"]) & (rj["order_date"] <= rj["ref_date"] + w30)]["action_id"].unique()
        idx.loc[sel["action_id"], "success_flag"] = 1
        idx.loc[hit, "success_flag"] = 0
    return idx.reset_index()


# --------------------------------------------------------------------------------------
# Engagement events caused by sent actions
# --------------------------------------------------------------------------------------
def gen_action_events(rng, out: pd.DataFrame, eng: np.ndarray):
    """Opens / clicks for delivered messages. Returns (events DataFrame, set of engaged action_ids)."""
    d = out[(out["decision"] == "SEND") & (out["delivery_status"] == "DELIVERED")]
    e = eng[d["cidx"].to_numpy()]
    ch = d["channel"].to_numpy()
    n = len(d)
    u = rng.random(n)
    p_open = np.where(ch == "EMAIL", 0.25 + 0.45 * e,
                      np.where(ch == "PUSH", 0.15 + 0.30 * e, np.where(ch == "SMS", 0.08 + 0.20 * e, 0.0)))
    opened = u < p_open
    delay = rng.integers(300, 43200, n)
    ts_open = d["sent_s"].to_numpy() + delay
    rows = []
    typ_open = np.where(ch == "EMAIL", "EMAIL_OPEN", np.where(ch == "PUSH", "PUSH_OPEN", "SMS_CLICK"))
    chan_open = np.where(ch == "EMAIL", "EMAIL", np.where(ch == "PUSH", "PUSH", "SMS"))
    succ = (d["success_flag"].fillna(0).to_numpy() == 1)
    rows.append(pd.DataFrame({"cidx": d["cidx"].to_numpy()[opened], "ts_s": ts_open[opened],
                              "event_type": typ_open[opened], "channel": chan_open[opened],
                              "pidx": -1, "action_id": d["action_id"].to_numpy()[opened]}))
    click = opened & (ch == "EMAIL") & (rng.random(n) < 0.25 + 0.40 * succ)
    rows.append(pd.DataFrame({"cidx": d["cidx"].to_numpy()[click],
                              "ts_s": ts_open[click] + rng.integers(60, 1800, int(click.sum())),
                              "event_type": "EMAIL_CLICK", "channel": "EMAIL", "pidx": -1,
                              "action_id": d["action_id"].to_numpy()[click]}))
    ev = pd.concat(rows, ignore_index=True)
    return ev, set(d["action_id"].to_numpy()[opened])
