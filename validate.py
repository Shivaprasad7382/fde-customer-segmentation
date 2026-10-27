"""Validation of an exported dataset directory.

Works only from the exported CSV files (+ rules table), so it also proves that segments, metrics and outcomes
can be re-derived from source records.  ``ground_truth/`` is used for exactly one thing: comparing what the
validator *found* with what was *injected* (detection precision / recall).  It is never an input to any check.
"""
from __future__ import annotations

import json
import os
import sqlite3

import numpy as np
import pandas as pd

from . import actions as A
from . import config as C
from . import schema as S
from . import segmentation as SG

TOL = 0.005


def load(data_dir: str) -> dict:
    return {n: pd.read_csv(os.path.join(data_dir, f"{n}.csv"), dtype=str, keep_default_na=False, na_values=[""])
            for n in S.TABLES}


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _dt(s, fmt=None):
    return pd.to_datetime(s, format=fmt, errors="coerce")


class Report:
    def __init__(self, gt: pd.DataFrame | None):
        self.checks = []
        self.gt = gt

    def add(self, cid, category, description, flagged: pd.DataFrame | None, issue: str | None = None, extra=None):
        """flagged: DataFrame with columns record_id, customer_id (may be empty)."""
        flagged = flagged if flagged is not None else pd.DataFrame({"record_id": [], "customer_id": []})
        flagged = flagged.drop_duplicates("record_id")
        n_flag = len(flagged)
        rec = {"check": cid, "category": category, "description": description, "flagged": int(n_flag),
               "expected_injected": 0, "unexpected": int(n_flag), "missed": 0}
        if issue is not None and self.gt is not None:
            g = self.gt[self.gt["issue_type"] == issue]
            gt_ids, gt_cust = set(g["record_id"]), set(g["customer_id"])
            exp = flagged["record_id"].isin(gt_ids) | flagged["customer_id"].isin(gt_cust)
            rec["injected_in_ground_truth"] = int(len(g))
            rec["expected_injected"] = int(exp.sum())
            rec["unexpected"] = int((~exp).sum())
            rec["missed"] = int(len(gt_ids - set(flagged["record_id"])))
        rec["status"] = "PASS" if rec["unexpected"] == 0 and rec["missed"] == 0 else "FAIL"
        rec["sample_unexpected"] = []
        if rec["unexpected"]:
            rec["sample_unexpected"] = flagged["record_id"].head(5).tolist() if issue is None else \
                flagged.loc[~(flagged["record_id"].isin(set(self.gt[self.gt["issue_type"] == issue]["record_id"]))
                              | flagged["customer_id"].isin(set(self.gt[self.gt["issue_type"] == issue]["customer_id"]))),
                            "record_id"].head(5).tolist()
        if extra:
            rec.update(extra)
        self.checks.append(rec)


def _flag(df, mask, id_col, cust_col="customer_id"):
    sub = df[mask]
    return pd.DataFrame({"record_id": sub[id_col].to_numpy(),
                         "customer_id": sub[cust_col].to_numpy() if cust_col in sub else ""})


# ---------------------------------------------------------------------------------------------
def check_structure(rep: Report, T: dict):
    for name, t in S.TABLES.items():
        df = T[name]
        pk = df[t.pk]
        rep.add(f"PK_UNIQUE.{name}", "primary_key", f"{name}.{t.pk} is unique and non-null",
                pd.DataFrame({"record_id": pk[pk.duplicated(keep=False) | pk.isna()].astype(str), "customer_id": ""}))
        bad = pd.Series(False, index=df.index)
        why = {}
        for f in t.fields:
            s = df[f.name]
            m = pd.Series(False, index=df.index)
            if f.required:
                m |= s.isna()
            nn = s.notna()
            if f.allowed:
                m |= nn & ~s.isin([str(a) for a in f.allowed])
            if f.pattern:
                m |= nn & ~s.fillna("").str.fullmatch(f.pattern)
            if f.type == "int":
                v = _num(s)
                m |= nn & (v.isna() | (v != v.round()))
            elif f.type == "decimal":
                m |= nn & _num(s).isna()
            elif f.type == "date":
                m |= nn & _dt(s, "%Y-%m-%d").isna()
            elif f.type == "datetime":
                m |= nn & _dt(s, "%Y-%m-%d %H:%M:%S").isna()
            elif f.type == "bool":
                m |= nn & ~s.isin(["0", "1"])
            if f.unique and f.name != t.pk:
                m |= nn & s.duplicated(keep=False)
            if m.any():
                why[f.name] = int(m.sum())
            bad |= m
        rep.add(f"SCHEMA.{name}", "types_enums_required", f"{name}: types, enums, patterns, required, uniqueness",
                _flag(df, bad, t.pk), extra={"violations_by_field": why} if why else None)
    for child, col, parent, pcol in S.FOREIGN_KEYS:
        c = T[child]
        vals = c[col].dropna()
        missing = ~vals.isin(set(T[parent][pcol]))
        sub = c.loc[vals.index[missing]]
        rep.add(f"FK.{child}.{col}", "foreign_key", f"{child}.{col} -> {parent}.{pcol}",
                pd.DataFrame({"record_id": sub[S.TABLES[child].pk].to_numpy(),
                              "customer_id": sub["customer_id"].to_numpy() if "customer_id" in sub else ""}))


def check_sqlite(rep: Report, T: dict):
    con = sqlite3.connect(":memory:")
    con.executescript(S.ddl())
    problems = []
    for name in S.LOAD_ORDER:
        df = T[name][S.TABLES[name].columns]
        rows = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
        q = f"INSERT INTO {name} VALUES ({','.join('?' * len(df.columns))})"
        try:
            con.executemany(q, rows)
        except sqlite3.IntegrityError as e:          # duplicate PK / NOT NULL: report, do not crash
            problems.append(f"{name}: {e}")
    viol = [f"{v[0]}:{v[1]}" for v in con.execute("PRAGMA foreign_key_check").fetchall()]
    con.close()
    rep.add("SQLITE.FK_CHECK", "foreign_key",
            "All tables load into the SQLite DDL (PK / NOT NULL) and PRAGMA foreign_key_check is clean",
            pd.DataFrame({"record_id": problems + viol, "customer_id": ""}))


def typed(T):
    cu = T["customers"].copy()
    cu["signup_date"] = _dt(cu["signup_date"])
    o = T["orders"].copy()
    o["order_date"] = _dt(o["order_date"])
    for c in ["gross_amount", "discount_amount", "net_amount"]:
        o[c] = _num(o[c])
    ln = T["order_lines"].copy()
    for c in ["line_number", "quantity", "unit_price", "discount_pct", "line_amount"]:
        ln[c] = _num(ln[c])
    cs = T["support_cases"].copy()
    cs["opened_at"], cs["closed_at"] = _dt(cs["opened_at"]), _dt(cs["closed_at"])
    for c in ["handling_minutes", "cost_amount", "escalated"]:
        cs[c] = _num(cs[c])
    co = T["consents"].copy()
    co["effective_from"], co["effective_to"] = _dt(co["effective_from"]), _dt(co["effective_to"])
    m = T["segment_memberships"].copy()
    m["effective_from"], m["effective_to"] = _dt(m["effective_from"]), _dt(m["effective_to"])
    for c in ["recency_days", "frequency", "gross_spend", "returned_amount", "net_spend", "r_score", "f_score",
              "m_score", "days_since_first_order", "cases_180d", "service_cost_180d", "service_cost_ratio",
              "return_rate"]:
        m[c] = _num(m[c])
    out = T["action_outcomes"].copy()
    out["recommended_at"], out["sent_at"] = _dt(out["recommended_at"]), _dt(out["sent_at"])
    out["success_flag"] = _num(out["success_flag"])
    out["attributed_revenue"] = _num(out["attributed_revenue"])
    pr = T["products"].copy()
    pr["launch_date"], pr["discontinued_date"] = _dt(pr["launch_date"]), _dt(pr["discontinued_date"])
    pr["list_price"] = _num(pr["list_price"])
    ev = T["engagement_events"][["event_id", "customer_id", "event_ts", "action_id"]].copy()
    ev["event_ts"] = _dt(ev["event_ts"])
    return dict(cu=cu, o=o, ln=ln, cs=cs, co=co, m=m, out=out, pr=pr, ev=ev,
                rules=T["segment_rules"].copy(), cat=T["action_catalog"].copy(), el=T["action_eligibility"].copy())


def check_dates_and_math(rep: Report, X: dict):
    cu, o, ln, cs, co, m, out, pr, ev = (X[k] for k in ["cu", "o", "ln", "cs", "co", "m", "out", "pr", "ev"])
    sign = cu.set_index("customer_id")["signup_date"]
    end = pd.Timestamp(C.DATA_END)
    # ---- orders
    bad = (o["order_date"] < o["customer_id"].map(sign)) | (o["order_date"] > end)
    rep.add("DATE.order_after_signup", "date_ordering", "order_date within [signup_date, 2026-10-22]", _flag(o, bad, "order_id"))
    by_id = o.drop_duplicates("order_id").set_index("order_id")
    ret = o[o["order_type"] == "RETURN"]
    p = ret["parent_order_id"]
    pt, pc, pdte = p.map(by_id["order_type"]), p.map(by_id["customer_id"]), p.map(by_id["order_date"])
    gap = (ret["order_date"] - pdte).dt.days
    bad = p.isna() | (pt != "SALE") | (pc != ret["customer_id"]) | (gap < C.RETURN_WINDOW_MIN_DAYS) | (gap > C.RETURN_WINDOW_MAX_DAYS)
    bad2 = (o["order_type"] == "SALE") & o["parent_order_id"].notna()
    rep.add("LIFECYCLE.return_parent", "lifecycle", "RETURN has a SALE parent of same customer, 3-30 days earlier; SALE has none",
            pd.concat([_flag(ret, bad, "order_id"), _flag(o, bad2, "order_id")]))
    # ---- lines
    amt = np.round(ln["quantity"] * ln["unit_price"] * (1 - ln["discount_pct"] / 100), 2)
    rep.add("MATH.line_amount", "reconciliation", "line_amount = round(qty*price*(1-disc),2)",
            _flag(ln.assign(c=""), (amt - ln["line_amount"]).abs() > TOL, "line_id", "c"))
    price = ln["product_id"].map(pr.set_index("product_id")["list_price"])
    rep.add("MATH.unit_price", "reconciliation", "unit_price equals product list price",
            _flag(ln.assign(c=""), (price - ln["unit_price"]).abs() > TOL, "line_id", "c"))
    ot = ln["order_id"].map(by_id["order_type"])
    sign_ok = ((ot == "SALE") & (ln["quantity"] > 0)) | ((ot == "RETURN") & (ln["quantity"] < 0))
    rep.add("MATH.line_sign", "reconciliation", "SALE lines positive, RETURN lines negative",
            _flag(ln.assign(c=""), ~sign_ok, "line_id", "c"))
    g = ln.assign(gross=ln["quantity"] * ln["unit_price"]).groupby("order_id").agg(
        gross=("gross", "sum"), net=("line_amount", "sum"), n=("line_id", "size"))
    chk = o.drop_duplicates("order_id").set_index("order_id").join(g)
    bad = chk["n"].isna() | ((chk["gross"].round(2) - chk["gross_amount"]).abs() > TOL) | \
        ((chk["net"].round(2) - chk["net_amount"]).abs() > TOL) | \
        ((chk["gross_amount"] - chk["net_amount"] - chk["discount_amount"]).abs() > TOL)
    rep.add("MATH.order_totals", "reconciliation", "order gross/net/discount reconcile to lines",
            pd.DataFrame({"record_id": chk.index[bad], "customer_id": chk["customer_id"][bad].to_numpy()}))
    # returned quantity <= sold quantity
    rl = ln[ln["parent_line_id"].notna()]
    rq = (-rl["quantity"]).groupby(rl["parent_line_id"]).sum()
    sq = ln.drop_duplicates("line_id").set_index("line_id")["quantity"]
    over = rq[rq > sq.reindex(rq.index)]
    rep.add("MATH.return_qty", "reconciliation", "cumulative returned quantity <= sold quantity per line",
            pd.DataFrame({"record_id": over.index, "customer_id": ""}))
    # line date vs product validity
    odate = ln["order_id"].map(by_id["order_date"])
    pi = pr.set_index("product_id")
    bad = (ot == "SALE") & ((odate < ln["product_id"].map(pi["launch_date"])) |
                            (odate > ln["product_id"].map(pi["discontinued_date"])))
    rep.add("DATE.product_validity", "date_ordering",
            "SALE lines dated within product launch/discontinue dates (returns of discontinued items are valid)",
            _flag(ln.assign(c=""), bad, "line_id", "c"))
    # ---- support cases
    cost = np.round(cs["handling_minutes"] / 60 * C.AGENT_HOURLY_RATE + C.ESCALATION_COST * cs["escalated"], 2)
    bad = (cost - cs["cost_amount"]).abs() > TOL
    rep.add("MATH.case_cost", "reconciliation", "case cost = handling time x rate + escalation", _flag(cs, bad, "case_id"))
    bad = (cs["closed_at"] < cs["opened_at"]) | ((cs["status"] == "OPEN") != cs["closed_at"].isna()) | \
        (cs["opened_at"].dt.normalize() < cs["customer_id"].map(sign)) | (cs["opened_at"] > end + pd.Timedelta(days=1))
    odt = cs["order_id"].map(by_id["order_date"])
    bad |= cs["order_id"].notna() & ((cs["opened_at"].dt.normalize() < odt) | (cs["order_id"].map(by_id["customer_id"]) != cs["customer_id"]))
    rep.add("LIFECYCLE.case_dates", "date_ordering", "case closed>=opened, status matches closed_at, order link valid", _flag(cs, bad, "case_id"))
    # ---- consent lifecycle
    c = co.sort_values(["customer_id", "channel", "purpose", "effective_from"])
    key = ["customer_id", "channel", "purpose"]
    prev_status = c.groupby(key)["status"].shift(1)
    prev_to = c.groupby(key)["effective_to"].shift(1)
    nxt_from = c.groupby(key)["effective_from"].shift(-1)
    first = prev_status.isna()
    bad = (first & (c["status"] != "GRANTED")) | (~first & (c["status"] == prev_status)) | \
          (~first & (prev_to != c["effective_from"])) | (c["effective_to"].notna() & (c["effective_to"] <= c["effective_from"])) | \
          (c["effective_to"].isna() != nxt_from.isna()) | (c["effective_from"] < c["customer_id"].map(sign))
    rep.add("LIFECYCLE.consent_chain", "lifecycle", "consent history alternates GRANTED/WITHDRAWN, chained, no overwrite", _flag(c, bad, "consent_id"))
    # ---- events
    bad = (ev["event_ts"] < ev["customer_id"].map(sign)) | (ev["event_ts"] >= end + pd.Timedelta(days=1))
    sent = out.set_index("action_id")["sent_at"]
    a = ev["action_id"].notna()
    bad |= a & (ev["event_ts"] < ev["action_id"].map(sent))
    rep.add("DATE.events", "date_ordering", "events after signup, before data end, action events after send", _flag(ev, bad, "event_id"))


def check_customers(rep: Report, X: dict):
    cu, o, cs = X["cu"], X["o"], X["cs"]
    ov = o[["customer_id", "order_date", "order_type", "net_amount"]]
    cv = cs[["customer_id", "opened_at", "cost_amount"]]
    f = SG.compute_features(cu, ov, cv, C.SNAP2).set_index("customer_id")
    c = cu.set_index("customer_id").join(f)
    life = np.select([c["frequency"] == 0, c["days_since_first_order"] <= C.LIFECYCLE_NEW_DAYS,
                      c["recency_days"] <= C.LIFECYCLE_ACTIVE_DAYS, c["recency_days"] <= C.LIFECYCLE_AT_RISK_DAYS],
                     ["PROSPECT", "NEW", "ACTIVE", "AT_RISK"], "DORMANT")
    ctype = np.where(c["frequency"] == 0, "PROSPECT", "CUSTOMER")
    sales = o[o["order_type"] == "SALE"].groupby("customer_id")["order_date"].agg(["min", "max"])
    c = c.join(sales)
    bad = (_num(c["lifetime_order_count"]) != c["frequency"]) | ((_num(c["lifetime_net_spend"]) - c["net_spend"]).abs() > TOL) | \
        (c["lifecycle_state"] != life) | (c["customer_type"] != ctype) | \
        (_dt(c["first_order_date"]).fillna(pd.Timestamp(0)) != c["min"].fillna(pd.Timestamp(0))) | \
        (_dt(c["last_order_date"]).fillna(pd.Timestamp(0)) != c["max"].fillna(pd.Timestamp(0)))
    rep.add("MATH.customer_derived", "reconciliation", "customer lifetime / lifecycle fields re-derive from orders",
            pd.DataFrame({"record_id": c.index[bad], "customer_id": c.index[bad]}))
    return f


def check_segments(rep: Report, X: dict):
    cu, o, cs, m, rules = X["cu"], X["o"], X["cs"], X["m"], X["rules"]
    ov = o[["customer_id", "order_date", "order_type", "net_amount"]]
    cv = cs[["customer_id", "opened_at", "cost_amount"]]
    rules_t = rules.copy()
    for c in ["min_r", "max_r", "min_f", "max_f", "min_m", "max_m", "max_days_since_first_order", "min_cases_180d",
              "min_sale_orders", "priority", "version", "min_service_cost_ratio", "min_return_rate"]:
        rules_t[c] = _num(rules_t[c])
    snaps = [C.SNAP1, C.SNAP2]
    excl_ids = set(rules.loc[rules["exclusive_group"] != "NONE", "segment_id"])
    # membership id and dating sanity
    bad = (m["membership_id"] != "MEM-" + m["segment_id"] + "-" + m["customer_id"]) | \
        (m["effective_to"].notna() & (m["effective_to"] <= m["effective_from"])) | \
        ((m["effective_to"].isna()).astype(int).astype(str) != m["is_current"])
    rep.add("LIFECYCLE.membership_dating", "lifecycle", "membership id, effective dating and is_current are consistent",
            _flag(m, bad, "membership_id"))
    cols = ["recency_days", "frequency", "gross_spend", "returned_amount", "net_spend", "r_score", "f_score", "m_score",
            "days_since_first_order", "cases_180d", "service_cost_180d", "service_cost_ratio", "return_rate"]
    mm_bad, extra_rows, missing_rows, overlap_rows = [], [], [], []
    for snap in snaps:
        sd = pd.Timestamp(snap)
        feats = SG.compute_features(cu, ov, cv, snap)
        exp = SG.assign_segments(feats, rules_t, snap)
        act = m[(m["effective_from"] <= sd) & (m["effective_to"].isna() | (m["effective_to"] > sd))]
        ek = set(zip(exp["customer_id"], exp["segment_id"]))
        ak = list(zip(act["customer_id"], act["segment_id"]))
        extra = act[[k not in ek for k in ak]]
        extra_rows.append(extra[["membership_id", "customer_id"]])
        akset = set(ak)
        miss = [k for k in ek if k not in akset]
        missing_rows.append(pd.DataFrame({"membership_id": ["MISSING:" + a + ":" + b for a, b in miss],
                                          "customer_id": [a for a, b in miss]}))
        # exclusivity
        ex = act[act["segment_id"].isin(excl_ids)]
        dup = ex[ex["customer_id"].duplicated(keep=False)]
        overlap_rows.append(dup[["membership_id", "customer_id"]])
        # metrics at entry
        ent = m[m["effective_from"] == sd].merge(feats, on="customer_id", suffixes=("", "_re"))
        bad = pd.Series(False, index=ent.index)
        for c in cols:
            a, b = ent[c], ent[c + "_re"]
            tol = 0.0001 if c in ("service_cost_ratio", "return_rate") else TOL
            bad |= ((a - b).abs() > tol) | (a.isna() != b.isna())
        mm_bad.append(_flag(ent, bad, "membership_id"))
    rep.add("RULES.membership_follows_rules", "business_rule", "active memberships == rule engine output (no extras)",
            pd.concat(extra_rows).rename(columns={"membership_id": "record_id"}), issue="CONFLICTING_EXCLUSIVE_SEGMENT")
    rep.add("RULES.no_missing_membership", "business_rule", "every rule match has an active membership",
            pd.concat(missing_rows).rename(columns={"membership_id": "record_id"}))
    rep.add("RULES.exclusive_group_single", "business_rule", "at most one active exclusive-group membership per customer",
            pd.concat(overlap_rows).rename(columns={"membership_id": "record_id"}), issue="CONFLICTING_EXCLUSIVE_SEGMENT")
    rep.add("MATH.membership_metrics", "reconciliation",
            "membership metrics re-derive from orders/cases at effective_from (returns netted)",
            pd.concat(mm_bad), issue="RETURN_NOT_NETTED")
    net_ok = (m["net_spend"] - (m["gross_spend"] - m["returned_amount"])).abs() > TOL
    rep.add("MATH.membership_net_spend", "reconciliation", "net_spend = gross_spend - returned_amount",
            _flag(m, net_ok, "membership_id"), issue="RETURN_NOT_NETTED")


def check_actions(rep: Report, X: dict, T: dict):
    out, cat, el, m, cs, co, cu, o = (X[k] for k in ["out", "cat", "el", "m", "cs", "co", "cu", "o"])
    catx = cat.set_index("catalog_id")
    seg_of = m.drop_duplicates("membership_id").set_index("membership_id")
    mseg = out["membership_id"].map(seg_of["segment_id"])
    # eligibility / attribution to the right action
    ok = {(s, c) for s, c in zip(el["segment_id"], el["catalog_id"])}
    bad = pd.Series([(s, c) not in ok for s, c in zip(mseg, out["catalog_id"])], index=out.index) | \
        (out["membership_id"].map(seg_of["customer_id"]) != out["customer_id"])
    rep.add("ACTION.eligible_for_segment", "business_rule", "action_outcome.catalog_id is eligible for the membership's segment",
            _flag(out, bad, "action_id"), issue="OUTCOME_WRONG_ACTION")
    rep.add("ACTION.metric_matches_catalog", "business_rule", "success_metric = catalog.success_metric",
            _flag(out, out["success_metric"] != out["catalog_id"].map(catx["success_metric"]), "action_id"))
    # decision / reason / dating consistency
    bad = ((out["decision"] == "SUPPRESS") != out["suppression_reason"].notna()) | \
          ((out["decision"] == "SEND") != out["sent_at"].notna()) | \
          (out["sent_at"].notna() & (out["sent_at"] < out["recommended_at"])) | \
          ((out["decision"] != "SUPPRESS") & out["channel"].isna())
    rep.add("ACTION.decision_consistency", "lifecycle", "decision / reason / channel / sent_at are consistent", _flag(out, bad, "action_id"))
    send = out[out["decision"] == "SEND"].copy()
    send["purpose"] = send["catalog_id"].map(catx["purpose"])
    send["day"] = send["sent_at"].dt.normalize()
    # channel allowed
    allowed = send["catalog_id"].map(catx["allowed_channels"]).fillna("")
    badc = [ch not in a.split("|") for ch, a in zip(send["channel"], allowed)]
    rep.add("CONTACT.channel_allowed", "contact_constraint", "channel is allowed for the action", _flag(send, np.array(badc), "action_id"))
    # consent
    g = co[co["status"] == "GRANTED"]
    j = send.reset_index().merge(g, on=["customer_id"], how="left", suffixes=("", "_c"))
    okc = (j["channel"] == j["channel_c"]) & (j["purpose"] == j["purpose_c"]) & (j["effective_from"] <= j["day"]) & \
        (j["effective_to"].isna() | (j["effective_to"] > j["day"]))
    good = set(j.loc[okc, "action_id"])
    rep.add("CONTACT.consent_enforced", "contact_constraint", "every SEND has GRANTED consent for channel+purpose on the send date",
            _flag(send, ~send["action_id"].isin(good), "action_id"), issue="ACTION_VIOLATES_CONSENT")
    # frequency cap
    cap = out[out["decision"] == "SEND"].groupby("customer_id").size()
    capmax = cu.set_index("customer_id")["contact_frequency_cap_30d"].astype(int)
    over = cap[cap > capmax.reindex(cap.index)]
    rep.add("CONTACT.frequency_cap", "contact_constraint", "SEND count per customer <= contact_frequency_cap_30d",
            pd.DataFrame({"record_id": over.index, "customer_id": over.index}))
    # open case suppression
    mk = send[send["purpose"] == "MARKETING"][["action_id", "customer_id", "recommended_at"]]
    jc = mk.merge(cs[["customer_id", "opened_at", "closed_at"]], on="customer_id")
    openc = (jc["opened_at"] <= jc["recommended_at"]) & (jc["closed_at"].isna() | (jc["closed_at"] > jc["recommended_at"]))
    rep.add("CONTACT.open_case_suppression", "contact_constraint", "no MARKETING SEND while a support case is open",
            jc.loc[openc, ["action_id", "customer_id"]].rename(columns={"action_id": "record_id"}))
    # discount to HIGH_SERVICE_COST accounts
    hs_id = X["rules"].loc[X["rules"]["segment_code"] == "HIGH_SERVICE_COST", "segment_id"].iloc[0]
    hs = m[m["segment_id"] == hs_id][["customer_id", "effective_from", "effective_to"]]
    disc = send[send["catalog_id"].map(catx["is_discount"]) == "1"][["action_id", "customer_id", "recommended_at"]]
    jd = disc.merge(hs, on="customer_id")
    hit = (jd["effective_from"] <= jd["recommended_at"]) & (jd["effective_to"].isna() | (jd["effective_to"] > jd["recommended_at"]))
    rep.add("CONTACT.high_service_discount_block", "contact_constraint", "no discount SEND to HIGH_SERVICE_COST accounts",
            jd.loc[hit, ["action_id", "customer_id"]].rename(columns={"action_id": "record_id"}))
    # holdout only on holdout-eligible actions
    badh = (out["decision"] == "HOLDOUT") & (out["catalog_id"].map(catx["requires_holdout"]) != "1")
    rep.add("ACTION.holdout_scope", "business_rule", "HOLDOUT only for requires_holdout actions", _flag(out, badh, "action_id"))
    # outcome re-evaluation
    ov = o[["order_id", "customer_id", "order_date", "order_type", "net_amount"]]
    cv = cs[["customer_id", "opened_at"]]
    ev = A.evaluate_outcomes(out, ov, cv).set_index("action_id")
    o2 = out.set_index("action_id")
    e_flag = pd.to_numeric(ev["success_flag"].astype("float64"), errors="coerce")
    mism = (e_flag.fillna(-1) != o2["success_flag"].fillna(-1)) | \
           (ev["attributed_order_id"].fillna("") != o2["attributed_order_id"].fillna("")) | \
           ((ev["attributed_revenue"].fillna(-1) - o2["attributed_revenue"].fillna(-1)).abs() > TOL)
    rep.add("OUTCOME.reevaluation", "reconciliation", "success_flag / attributed order / revenue re-derive from orders & cases",
            pd.DataFrame({"record_id": mism.index[mism], "customer_id": o2.loc[mism, "customer_id"].to_numpy()}))
    # status coherence
    st = out["outcome_status"]
    bad = ((out["decision"] == "SUPPRESS") != (st == "SUPPRESSED")) | ((out["decision"] == "HOLDOUT") != (st == "HOLDOUT")) | \
          ((out["delivery_status"] == "FAILED") != (st == "DELIVERY_FAILED")) | \
          ((st == "SUCCESS") != ((out["success_flag"] == 1) & (out["decision"] == "SEND") & (out["delivery_status"] == "DELIVERED")))
    rep.add("OUTCOME.status_coherent", "lifecycle", "outcome_status agrees with decision / delivery / success_flag", _flag(out, bad, "action_id"))


def check_duplicates(rep: Report, T: dict):
    for name, cols in {"segment_memberships": ["customer_id", "segment_id"],
                       "action_outcomes": ["customer_id", "membership_id", "catalog_id"],
                       "consents": ["customer_id", "channel", "purpose", "effective_from"],
                       "order_lines": ["order_id", "line_number"],
                       "action_eligibility": ["segment_id", "priority"]}.items():
        df = T[name]
        d = df[df.duplicated(cols, keep=False)]
        rep.add(f"DUP.{name}", "duplicates", f"no duplicate {'/'.join(cols)} in {name}",
                pd.DataFrame({"record_id": d[S.TABLES[name].pk].to_numpy(), "customer_id": d["customer_id"].to_numpy() if "customer_id" in d else ""}))


def coverage(X: dict, f2: pd.DataFrame) -> dict:
    cu, o, cs, co, out = X["cu"], X["o"], X["cs"], X["co"], X["out"]
    f = f2.copy()
    cov = {
        "customers_by_lifecycle_state": cu["lifecycle_state"].value_counts().to_dict(),
        "new_customers(first_sale<=60d)": int((cu["lifecycle_state"] == "NEW").sum()),
        "dormant_customers": int((cu["lifecycle_state"] == "DORMANT").sum()),
        "prospects": int((cu["customer_type"] == "PROSPECT").sum()),
        "return_orders": int((o["order_type"] == "RETURN").sum()),
        "customers_with_returns": int(o.loc[o["order_type"] == "RETURN", "customer_id"].nunique()),
        "high_service_cost_accounts(snapshot2)": int(((f["cases_180d"] >= 3) & (f["service_cost_ratio"] >= 0.10)).sum()),
        "consent_withdrawal_rows": int((co["status"] == "WITHDRAWN").sum()),
        "customers_withdrawing_any_consent": int(co.loc[co["status"] == "WITHDRAWN", "customer_id"].nunique()),
        "open_support_cases": int((cs["status"] == "OPEN").sum()),
        "recency_boundary_days": {str(d): int((f["recency_days"] == d).sum()) for d in [30, 31, 60, 61, 120, 121, 240, 241]},
        "decisions": out["decision"].value_counts().to_dict(),
        "suppression_reasons": out["suppression_reason"].value_counts().to_dict(),
    }
    return cov


def validate_dir(root: str, log=print) -> dict:
    data_dir = os.path.join(root, "data")
    T = load(data_dir)
    gtp = os.path.join(root, "ground_truth", "ground_truth.csv")
    gt = pd.read_csv(gtp, dtype=str) if os.path.exists(gtp) else None
    rep = Report(gt)
    check_structure(rep, T)
    check_sqlite(rep, T)
    X = typed(T)
    check_dates_and_math(rep, X)
    f2 = check_customers(rep, X)
    check_segments(rep, X)
    check_actions(rep, X, T)
    check_duplicates(rep, T)
    cov = coverage(X, f2.reset_index())

    failed = [c for c in rep.checks if c["status"] == "FAIL"]
    detection = {}
    if gt is not None:
        for it, g in gt.groupby("issue_type"):
            chk = [c for c in rep.checks if _issue_of(c["check"]) == it]
            detection[it] = {"injected": int(len(g)),
                             "checks": {c["check"]: {"flagged_expected": c["expected_injected"], "missed": c["missed"]}
                                        for c in chk}}
    summary = {
        "overall_status": "PASS" if not failed else "FAIL",
        "checks_total": len(rep.checks), "checks_passed": len(rep.checks) - len(failed), "checks_failed": len(failed),
        "row_counts": {n: int(len(df)) for n, df in T.items()},
        "injected_anomaly_detection": detection,
        "coverage": cov,
        "checks": rep.checks,
    }
    return summary


_ISSUE_BY_CHECK = {
    "RULES.membership_follows_rules": "CONFLICTING_EXCLUSIVE_SEGMENT",
    "RULES.exclusive_group_single": "CONFLICTING_EXCLUSIVE_SEGMENT",
    "MATH.membership_metrics": "RETURN_NOT_NETTED",
    "MATH.membership_net_spend": "RETURN_NOT_NETTED",
    "ACTION.eligible_for_segment": "OUTCOME_WRONG_ACTION",
    "CONTACT.consent_enforced": "ACTION_VIOLATES_CONSENT",
}


def _issue_of(check):
    return _ISSUE_BY_CHECK.get(check)


def to_markdown(s: dict) -> str:
    L = ["# Validation summary", "", f"**Overall: {s['overall_status']}** - {s['checks_passed']}/{s['checks_total']} checks passed.", "",
         "A check PASSES when everything it flags is a *labelled injected anomaly* (see `ground_truth/`) and every "
         "injected anomaly of its type is found; checks without a ground-truth issue type must flag nothing.", "",
         "## Row counts", "", "| table | rows |", "|---|---|"]
    L += [f"| {k} | {v:,} |" for k, v in s["row_counts"].items()]
    L += ["", "## Injected anomaly detection", "", "| issue type | injected | detected by |", "|---|---|---|"]
    for it, d in s["injected_anomaly_detection"].items():
        det = "; ".join(f"`{c}` (found {v['flagged_expected']}, missed {v['missed']})" for c, v in d["checks"].items())
        L.append(f"| {it} | {d['injected']} | {det} |")
    L += ["", "## All checks", "", "| check | category | flagged | expected (injected) | unexpected | missed | status |",
          "|---|---|---|---|---|---|---|"]
    for c in s["checks"]:
        L.append(f"| `{c['check']}` | {c['category']} | {c['flagged']} | {c['expected_injected']} | {c['unexpected']} | {c['missed']} | {c['status']} |")
    L += ["", "## Scenario coverage", "", "```json", json.dumps(s["coverage"], indent=2, default=str), "```", ""]
    return "\n".join(L)
