"""End-to-end generator.

Order of operations (parents before children, derived values computed from sources):

  1. products, customers (+ latent archetypes), consent history
  2. organic SALE orders/lines, explicit RETURN orders/lines            (everything dated up to 2026-10-22)
  3. support cases dated before snapshot 1
  4. snapshot-1 segmentation (RFM + overlays) from orders/cases dated < 2026-08-01
  5. action planning on snapshot-1 memberships: eligibility -> consent -> suppression rules -> holdout -> send
  6. campaign response (documented probability model) -> induced orders; top-up organic orders keep the total exact
  7. orders get final chronological IDs; post-snapshot cases; outcome evaluation derived from orders / cases
  8. engagement events (action-caused opens/clicks + baseline) to hit the exact event count
  9. snapshot-2 segmentation, customer derived fields, membership history (effective dating)
 10. labelled anomaly injection -> ground truth
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import actions as A
from . import anomalies as AN
from . import config as C
from . import entities as E
from . import segmentation as SG
from .entities import END_I, HIST0, SNAP1_I, SNAP2_I, to_dates


def _orders_view(o: pd.DataFrame, cust: pd.DataFrame, ids: bool = False) -> pd.DataFrame:
    v = pd.DataFrame({"customer_id": cust["customer_id"].to_numpy()[o["cidx"].to_numpy()],
                      "order_date": to_dates(o["date_i"].to_numpy()), "order_type": o["order_type"].to_numpy(),
                      "net_amount": o["net_amount"].to_numpy()})
    if ids:
        v["order_id"] = o["order_id"].to_numpy()
    return v


def _cases_view(c: pd.DataFrame, cust: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"customer_id": cust["customer_id"].to_numpy()[c["cidx"].to_numpy()],
                         "opened_at": pd.to_datetime(c["opened_s"].to_numpy(), unit="s"),
                         "cost_amount": c["cost_amount"].to_numpy()})


def generate_tables(scale: C.Scale, seed: int = C.SEED, log=print):
    rng = np.random.default_rng(seed)
    log(f"[1/10] master data  (customers={scale.customers:,} orders={scale.orders:,} events={scale.events:,})")
    prod = E.ProductBook(rng, scale.n_products)
    cust = E.gen_customers(rng, scale.customers)
    cust_view = pd.DataFrame({"customer_id": cust["customer_id"], "signup_date": to_dates(cust["signup_i"].to_numpy())})
    consents = E.gen_consents(rng, cust)

    log("[2/10] organic sales + returns")
    fac = E.OrderFactory(rng, prod, cust)
    n_init = scale.orders - scale.n_returns - scale.n_reserve
    cidx, date_i = E.allocate_sale_dates(rng, cust, n_init, HIST0, END_I, force_first=True)
    so, sl = fac.sales(cidx, date_i)
    ro, rl = fac.returns(so, sl, scale.n_returns)

    log("[3/10] support cases before snapshot 1")
    n_pre = int(round(scale.n_cases * 0.85))
    cases_pre = E.gen_cases(rng, cust, so, n_pre, HIST0, SNAP1_I - 1)

    log("[4/10] snapshot-1 segmentation")
    rules = SG.build_rules()
    o1 = pd.concat([so, ro], ignore_index=True).astype({"cidx": "int64", "date_i": "int64"})
    mem1 = SG.snapshot_memberships(cust_view, _orders_view(o1, cust), _cases_view(cases_pre, cust), rules, C.SNAP1)

    log("[5/10] action planning, suppression, holdout")
    catalog = A.build_catalog()
    elig = A.build_eligibility(rules, catalog)
    out = A.plan_actions(rng, cust, mem1, rules, catalog, elig, consents, cases_pre)
    out = A.assign_action_ids(out)
    gt = []
    out, g = AN.inject_consent_violations(rng, out, catalog, cust, scale.n_anomalies)
    gt += g

    log("[6/10] campaign response -> induced orders")
    cat = catalog.set_index("catalog_id")
    code = out["catalog_id"].map(cat["action_code"]).to_numpy()
    deliv = ((out["decision"] == "SEND") & (out["delivery_status"] == "DELIVERED")
             & (out["success_metric"] == "ORDER_14D")).to_numpy()
    idxs = np.where(deliv)[0]
    p = A.response_probability(code[idxs], out["channel"].to_numpy()[idxs], cust["eng"].to_numpy()[out["cidx"].to_numpy()[idxs]])
    resp = idxs[rng.random(len(idxs)) < p]
    if len(resp) > scale.n_reserve:                         # capacity cap keeps total order count exact
        resp = np.sort(rng.choice(resp, size=scale.n_reserve, replace=False))
    off = 1 + np.minimum(rng.geometric(0.3, len(resp)) - 1, 12)
    ind_date = out["sent_s"].to_numpy()[resp] // 86400 + off
    dpct = out["catalog_id"].map(cat["discount_pct"]).astype(float).to_numpy()[resp]
    io, il = fac.sales(out["cidx"].to_numpy()[resp], ind_date.astype("int64"), dpct)
    induced_map = dict(zip(io["ok"].to_numpy(), out["action_id"].to_numpy()[resp])) if len(io) else {}
    n_top = scale.orders - len(so) - len(ro) - len(io)
    tc, td = E.allocate_sale_dates(rng, cust, n_top, SNAP1_I, END_I, force_first=False)
    to, tl = fac.sales(tc, td)

    log("[7/10] final order IDs, post-snapshot cases, outcome evaluation")
    all_o = pd.concat([x for x in (so, ro, io, to) if len(x)], ignore_index=True)
    all_o = all_o.astype({"ok": "int64", "cidx": "int64", "date_i": "int64", "parent_ok": "int64"})
    all_o = all_o.sort_values(["date_i", "ok"], kind="mergesort").reset_index(drop=True)
    all_l = pd.concat([x for x in (sl, rl, il, tl) if len(x)], ignore_index=True)
    all_l = all_l.astype({"ok": "int64", "line_number": "int64", "pidx": "int64", "quantity": "int64",
                          "p_ok": "int64", "p_ln": "int64"})
    yr = to_dates(all_o["date_i"].to_numpy()).year
    seq = pd.Series(1, index=all_o.index).groupby(np.asarray(yr)).cumsum()
    all_o["order_id"] = "ORD-" + pd.Series(np.asarray(yr), index=all_o.index).astype(str) + "-" + seq.astype(str).str.zfill(6)
    id_map = pd.Series(all_o["order_id"].to_numpy(), index=all_o["ok"].to_numpy())

    sales_final = all_o[all_o["order_type"] == "SALE"]
    cases_post = E.gen_cases(rng, cust, sales_final, scale.n_cases - n_pre, SNAP1_I, END_I)
    cases = pd.concat([cases_pre, cases_post], ignore_index=True) \
        .sort_values(["opened_s", "cidx", "order_ok"], kind="mergesort").reset_index(drop=True)
    cases["case_id"] = [f"SUP-{i:06d}" for i in range(1, len(cases) + 1)]
    cases["order_id"] = cases["order_ok"].map(id_map)

    ov = _orders_view(all_o, cust, ids=True)
    cv = _cases_view(cases, cust)
    ov_out = pd.DataFrame({
        "action_id": out["action_id"], "customer_id": out["customer_id"], "decision": out["decision"],
        "delivery_status": out["delivery_status"], "success_metric": out["success_metric"],
        "recommended_at": pd.to_datetime(out["rec_s"].to_numpy(), unit="s"),
        "sent_at": pd.to_datetime(np.where(out["sent_s"] >= 0, out["sent_s"], 0), unit="s").where(out["sent_s"].to_numpy() >= 0)})
    ev_res = A.evaluate_outcomes(ov_out, ov, cv)
    out = out.merge(ev_res, on="action_id", how="left")
    # the first-order churn of Int64 -> keep as nullable ints
    out["success_flag"] = out["success_flag"].astype("Int64")

    log("[8/10] engagement events")
    aev, engaged = A.gen_action_events(rng, out, cust["eng"].to_numpy())
    n_base = scale.events - len(aev)
    if n_base < 0:
        raise ValueError("events target too small for the number of campaign events")
    bev = E.gen_baseline_events(rng, cust, prod, n_base)
    ev = pd.concat([bev, aev], ignore_index=True).sort_values(["ts_s", "cidx", "event_type"], kind="mergesort").reset_index(drop=True)
    ev["event_id"] = [f"EVT-{i:08d}" for i in range(1, len(ev) + 1)]
    status = out["outcome_status"].to_numpy(dtype=object).copy()
    sent_ok = ((out["decision"] == "SEND") & (out["delivery_status"] == "DELIVERED")).to_numpy()
    success = (out["success_flag"].fillna(0).astype(int) == 1).to_numpy()
    is_eng = out["action_id"].isin(engaged).to_numpy()
    status[sent_ok] = np.where(success[sent_ok], "SUCCESS", np.where(is_eng[sent_ok], "ENGAGED", "NO_RESPONSE"))
    out["outcome_status"] = status

    log("[9/10] snapshot-2 segmentation, derived customer fields, membership history")
    ov_nid = _orders_view(all_o, cust)
    f2 = SG.compute_features(cust_view, ov_nid, cv, C.SNAP2)
    rec, dsf, fq = f2["recency_days"].to_numpy(), f2["days_since_first_order"].to_numpy(), f2["frequency"].to_numpy()
    life = np.select([fq == 0, dsf <= C.LIFECYCLE_NEW_DAYS, rec <= C.LIFECYCLE_ACTIVE_DAYS,
                      rec <= C.LIFECYCLE_AT_RISK_DAYS], ["PROSPECT", "NEW", "ACTIVE", "AT_RISK"], "DORMANT")
    snap2 = pd.Timestamp(C.SNAP2)
    customers = pd.DataFrame({
        "customer_id": cust["customer_id"], "signup_date": cust_view["signup_date"], "region": cust["region"],
        "acquisition_channel": cust["acquisition_channel"], "age_band": cust["age_band"],
        "preferred_channel": cust["preferred_channel"],
        "contact_frequency_cap_30d": cust["contact_frequency_cap_30d"],
        "customer_type": np.where(fq == 0, "PROSPECT", "CUSTOMER"), "lifecycle_state": life,
        "first_order_date": (snap2 - pd.to_timedelta(dsf, unit="D")).where(fq > 0),
        "last_order_date": (snap2 - pd.to_timedelta(rec, unit="D")).where(fq > 0),
        "lifetime_order_count": fq, "lifetime_net_spend": f2["net_spend"].to_numpy()})

    mem2 = SG.snapshot_memberships(cust_view, ov_nid, cv, rules, C.SNAP2)
    key = ["customer_id", "segment_id"]
    j = mem1.merge(mem2[key].assign(_in2=1), on=key, how="left")
    m1 = mem1.copy()
    m1["effective_to"] = pd.Timestamp(C.SNAP2)
    m1.loc[j["_in2"].notna().to_numpy(), "effective_to"] = pd.NaT
    j2 = mem2.merge(mem1[key].assign(_in1=1), on=key, how="left")
    m2 = mem2[j2["_in1"].isna().to_numpy()].copy()
    m2["effective_to"] = pd.NaT
    mem = pd.concat([m1, m2], ignore_index=True)
    mem["effective_to"] = pd.to_datetime(mem["effective_to"])

    log("[10/10] labelled anomalies")
    mem, g = AN.inject_conflicting_segments(rng, mem, rules, scale.n_anomalies)
    gt += g
    mem, g = AN.inject_return_not_netted(rng, mem, rules, scale.n_anomalies)
    gt += g
    out, g = AN.inject_wrong_action(rng, out, catalog, elig, mem, scale.n_anomalies)
    gt += g
    mem["is_current"] = mem["effective_to"].isna().astype(int)
    mem = mem.sort_values(["effective_from", "segment_id", "customer_id"], kind="mergesort").reset_index(drop=True)

    # ---------------- final tables in schema shape ----------------
    cid = cust["customer_id"].to_numpy()
    pid = prod.df["product_id"].to_numpy()
    orders_t = pd.DataFrame({
        "order_id": all_o["order_id"], "customer_id": cid[all_o["cidx"].to_numpy()],
        "order_date": to_dates(all_o["date_i"].to_numpy()), "order_type": all_o["order_type"],
        "parent_order_id": all_o["parent_ok"].map(id_map), "order_channel": all_o["order_channel"],
        "gross_amount": all_o["gross_amount"], "discount_amount": all_o["discount_amount"],
        "net_amount": all_o["net_amount"]})
    all_l["order_id"] = all_l["ok"].map(id_map)
    ln = all_l["line_number"].astype(int).astype(str).str.zfill(2)
    all_l["line_id"] = all_l["order_id"] + "-L" + ln
    pln = all_l["p_ln"].astype(int).astype(str).str.zfill(2)
    all_l["parent_line_id"] = (all_l["p_ok"].map(id_map) + "-L" + pln).where(all_l["p_ok"] >= 0)
    all_l = all_l.sort_values(["order_id", "line_number"], kind="mergesort").reset_index(drop=True)
    lines_t = pd.DataFrame({
        "line_id": all_l["line_id"], "order_id": all_l["order_id"], "line_number": all_l["line_number"],
        "product_id": pid[all_l["pidx"].to_numpy()], "quantity": all_l["quantity"],
        "unit_price": all_l["unit_price"], "discount_pct": all_l["discount_pct"],
        "line_amount": all_l["line_amount"], "parent_line_id": all_l["parent_line_id"],
        "return_reason": all_l["return_reason"]})
    pidx_ev = ev["pidx"].to_numpy()
    events_t = pd.DataFrame({
        "event_id": ev["event_id"], "customer_id": cid[ev["cidx"].to_numpy()],
        "event_ts": pd.to_datetime(ev["ts_s"].to_numpy(), unit="s"), "event_type": ev["event_type"],
        "channel": ev["channel"], "product_id": pd.Series(pid[np.maximum(pidx_ev, 0)], index=ev.index).where(pidx_ev >= 0),
        "action_id": ev["action_id"]})
    cases_t = pd.DataFrame({
        "case_id": cases["case_id"], "customer_id": cid[cases["cidx"].to_numpy()], "order_id": cases["order_id"],
        "opened_at": pd.to_datetime(cases["opened_s"].to_numpy(), unit="s"),
        "closed_at": pd.to_datetime(np.where(cases["closed_s"] >= 0, cases["closed_s"], 0), unit="s").where(cases["closed_s"].to_numpy() >= 0),
        "category": cases["category"], "priority": cases["priority"], "status": cases["status"],
        "escalated": cases["escalated"], "handling_minutes": cases["handling_minutes"],
        "cost_amount": cases["cost_amount"]})
    consents_t = pd.DataFrame({
        "consent_id": consents["consent_id"], "customer_id": cid[consents["cidx"].to_numpy()],
        "channel": consents["channel"], "purpose": consents["purpose"], "status": consents["status"],
        "effective_from": to_dates(consents["from_i"].to_numpy()),
        "effective_to": pd.to_datetime(np.where(consents["to_i"].notna(), consents["to_i"].fillna(0), 0), unit="D").where(consents["to_i"].notna().to_numpy()),
        "source": consents["source"]})
    mem_t = mem.copy()
    outcomes_t = pd.DataFrame({
        "action_id": out["action_id"], "customer_id": out["customer_id"], "membership_id": out["membership_id"],
        "catalog_id": out["catalog_id"], "recommended_at": pd.to_datetime(out["rec_s"].to_numpy(), unit="s"),
        "decision": out["decision"], "suppression_reason": out["suppression_reason"], "channel": out["channel"],
        "sent_at": pd.to_datetime(np.where(out["sent_s"] >= 0, out["sent_s"], 0), unit="s").where(out["sent_s"].to_numpy() >= 0),
        "delivery_status": out["delivery_status"],
        "success_metric": out["catalog_id"].map(cat["success_metric"]),
        "success_flag": out["success_flag"], "attributed_order_id": out["attributed_order_id"],
        "attributed_revenue": out["attributed_revenue"], "outcome_status": out["outcome_status"],
        "evaluated_at": pd.Timestamp(C.SNAP2)})

    tables = {"products": prod.df, "customers": customers, "orders": orders_t, "order_lines": lines_t,
              "engagement_events": events_t, "support_cases": cases_t, "consents": consents_t,
              "segment_rules": rules, "segment_memberships": mem_t, "action_catalog": catalog,
              "action_eligibility": elig, "action_outcomes": outcomes_t}

    gtdf = pd.DataFrame(gt, columns=[c for c in AN.GT_COLUMNS if c != "gt_id"])
    gtdf = gtdf.sort_values(["issue_type", "record_id"], kind="mergesort").reset_index(drop=True)
    gtdf.insert(0, "gt_id", [f"GT-{i:06d}" for i in range(1, len(gtdf) + 1)])
    ind = pd.DataFrame({"order_id": [id_map[k] for k in induced_map], "action_id": list(induced_map.values())})
    ind["customer_id"] = ind["order_id"].map(orders_t.set_index("order_id")["customer_id"])
    labels = {
        "customer_archetypes": pd.DataFrame({"customer_id": cust["customer_id"], "archetype": cust["archetype"],
                                             "engagement_propensity": cust["eng"].round(4)}),
        "campaign_induced_orders": ind,
    }
    return tables, gtdf, labels
