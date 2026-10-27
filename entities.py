"""Generators for master data and baseline transaction / event tables.

All randomness flows through one ``numpy.random.Generator`` seeded by the caller, and the call order is fixed,
so a given (seed, scale) always yields byte-identical output.
Dates are handled as integer days since 1970-01-01 (``*_i``) internally and converted at the edges.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def day(s) -> int:
    return int(np.datetime64(s, "D").astype("int64"))


HIST0 = day(C.HISTORY_START)
END_I = day(C.DATA_END)
SNAP1_I = day(C.SNAP1)
SNAP2_I = day(C.SNAP2)


def to_dates(i) -> pd.Series:
    return pd.to_datetime(np.asarray(i, dtype="int64"), unit="D")


# ======================================================================================
# Products
# ======================================================================================
CATEGORIES = {
    "ELECTRONICS": (["Audio", "Computing", "Mobile Accessories", "Smart Home"], 120, 0.60, "ELE"),
    "HOME_KITCHEN": (["Cookware", "Appliances", "Storage", "Decor"], 45, 0.55, "HOM"),
    "APPAREL": (["Tops", "Bottoms", "Outerwear", "Footwear"], 35, 0.50, "APP"),
    "BEAUTY": (["Skincare", "Haircare", "Fragrance"], 18, 0.50, "BEA"),
    "SPORTS_OUTDOORS": (["Fitness", "Camping", "Cycling"], 40, 0.60, "SPO"),
    "BOOKS_MEDIA": (["Books", "Music", "Games"], 14, 0.40, "BOK"),
    "TOYS_GAMES": (["Puzzles", "Building Sets", "Outdoor Play"], 25, 0.50, "TOY"),
    "GROCERY_GOURMET": (["Snacks", "Beverages", "Pantry"], 12, 0.45, "GRO"),
}
N_CAT = len(CATEGORIES)


class ProductBook:
    """Product master + vectorised, date-aware product picker."""

    def __init__(self, rng: np.random.Generator, n: int):
        per = n // N_CAT
        cols = {k: [] for k in ["cat", "sub", "price", "cost", "launch", "disc", "sku", "name", "cat_i"]}
        for ci, (cat, (subs, med, sig, code)) in enumerate(CATEGORIES.items()):
            k = per
            price = np.floor(np.exp(rng.normal(np.log(med), sig, k)).clip(2, 1500)) + 0.99
            cost = np.round(price * rng.uniform(0.45, 0.80, k), 2)
            sub_i = rng.integers(0, len(subs), k)
            late = rng.random(k) < 0.12
            launch = np.where(late, day("2025-01-01") + rng.integers(0, 365, k),
                              day("2024-01-01") + rng.integers(0, 274, k))
            dis = (~late) & (rng.random(k) < 0.06)
            disc = np.where(dis, day("2025-06-01") + rng.integers(0, 456, k), -1)
            cols["cat"] += [cat] * k
            cols["cat_i"] += [ci] * k
            cols["sub"] += [subs[j] for j in sub_i]
            cols["price"].append(price)
            cols["cost"].append(cost)
            cols["launch"].append(launch)
            cols["disc"].append(disc)
            cols["sku"] += [f"SKU-{code}-{j:05d}" for j in range(1, k + 1)]
            cols["name"] += [f"{subs[sub_i[j]]} {code}-{j + 1:03d}" for j in range(k)]
        self.n = per * N_CAT
        self.price = np.concatenate(cols["price"])
        self.cost = np.concatenate(cols["cost"])
        self.cat_i = np.array(cols["cat_i"])
        self.launch_i = np.concatenate(cols["launch"]).astype("int64")
        disc = np.concatenate(cols["disc"]).astype("int64")
        self.disc_i = np.where(disc < 0, 10 ** 9, disc)
        ranks = rng.permutation(self.n) + 1
        w = 1.0 / ranks ** 0.8
        self.w_all = w / w.sum()
        self.cat_idx = [np.where(self.cat_i == c)[0] for c in range(N_CAT)]
        self.cat_w = [self.w_all[ix] / self.w_all[ix].sum() for ix in self.cat_idx]
        base = (self.launch_i < HIST0) & (self.disc_i == 10 ** 9)
        self.base_idx = np.where(base)[0]
        self.base_w = self.w_all[self.base_idx] / self.w_all[self.base_idx].sum()
        has_disc = disc >= 0
        self.df = pd.DataFrame({
            "product_id": [f"PRD-{i:06d}" for i in range(1, self.n + 1)],
            "sku": cols["sku"], "product_name": cols["name"], "category": cols["cat"],
            "subcategory": cols["sub"], "list_price": self.price, "unit_cost": self.cost,
            "status": np.where(has_disc, "DISCONTINUED", "ACTIVE"),
            "launch_date": to_dates(self.launch_i),
            "discontinued_date": pd.to_datetime(np.where(has_disc, disc, 0), unit="D").where(has_disc),
        })

    def pick(self, rng, pref_cat: np.ndarray, date_i: np.ndarray) -> np.ndarray:
        """Pick a product index per line: 55% from the customer's preferred category, always valid on date."""
        n = len(date_i)
        idx = rng.choice(self.n, size=n, p=self.w_all)
        use_pref = rng.random(n) < 0.55
        for c in range(N_CAT):
            m = use_pref & (pref_cat == c)
            k = int(m.sum())
            if k:
                idx[m] = self.cat_idx[c][rng.choice(len(self.cat_idx[c]), size=k, p=self.cat_w[c])]
        bad = (date_i < self.launch_i[idx]) | (date_i > self.disc_i[idx])
        k = int(bad.sum())
        if k:
            idx[bad] = self.base_idx[rng.choice(len(self.base_idx), size=k, p=self.base_w)]
        return idx


# ======================================================================================
# Customers
# ======================================================================================
SIGNUP_RANGE = {
    "CHAMPION": (day("2024-10-01"), day("2025-09-30")),
    "LOYAL": (day("2024-10-01"), day("2026-01-31")),
    "REGULAR": (day("2024-10-01"), day("2026-04-30")),
    "OCCASIONAL": (day("2024-10-01"), day("2026-06-30")),
    "NEW": (END_I - 120, END_I - 3),
    "AT_RISK": (day("2024-10-01"), day("2025-12-31")),
    "DORMANT": (day("2024-10-01"), day("2025-06-30")),
    "HIGH_SERVICE": (day("2024-10-01"), day("2026-03-31")),
    "RETURNER": (day("2024-10-01"), day("2026-04-30")),
    "PROSPECT": (day("2024-10-01"), END_I - 1),
}


def gen_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    arch_i = rng.choice(len(C.ARCHETYPES), size=n, p=C.ARCH_P)
    names = np.array(C.ARCHETYPES)[arch_i]
    signup = np.zeros(n, dtype="int64")
    end = np.full(n, END_I, dtype="int64")
    for a in C.ARCHETYPES:
        m = names == a
        k = int(m.sum())
        if not k:
            continue
        lo, hi = SIGNUP_RANGE[a]
        signup[m] = rng.integers(lo, hi + 1, k)
        if a == "AT_RISK":
            end[m] = END_I - rng.integers(100, 231, k)
        elif a == "DORMANT":
            end[m] = signup[m] + rng.integers(30, 201, k)
    order = np.lexsort((np.arange(n), signup))        # chronological IDs, deterministic tie-break
    names, signup, end = names[order], signup[order], end[order]

    eng = np.zeros(n)
    for a, (al, be) in C.ARCH_ENG_BETA.items():
        m = names == a
        eng[m] = rng.beta(al, be, int(m.sum()))
    ev_end = np.where(np.isin(names, ["DORMANT", "AT_RISK"]), np.minimum(END_I, end + 60), END_I)
    return pd.DataFrame({
        "customer_id": [f"CUS-{i:06d}" for i in range(1, n + 1)],
        "signup_i": signup,
        "region": rng.choice(C.REGIONS, size=n, p=[0.38, 0.26, 0.20, 0.10, 0.06]),
        "acquisition_channel": rng.choice(C.ACQ_CHANNELS, size=n, p=[0.30, 0.25, 0.15, 0.20, 0.10]),
        "age_band": np.where(rng.random(n) < 0.08, None,
                             rng.choice(C.AGE_BANDS, size=n, p=[0.12, 0.30, 0.26, 0.18, 0.14])),
        "preferred_channel": rng.choice(C.CHANNELS, size=n, p=[0.55, 0.15, 0.20, 0.10]),
        "contact_frequency_cap_30d": rng.choice([1, 2, 3], size=n, p=[0.25, 0.50, 0.25]),
        # ---- latent generator state (never exported to data/) ----
        "archetype": names,
        "eng": eng,
        "pref_cat": rng.integers(0, N_CAT, n),
        "ret_prop": np.where(names == "RETURNER", 6.0, 1.0),
        "case_prop": np.where(names == "HIGH_SERVICE", 1.0, 0.1),
        "rate": np.array([C.ARCH_ORDER_RATE[a] for a in names]),
        "ev_factor": np.array([C.ARCH_EVENT_FACTOR[a] for a in names]),
        "act_start_i": signup,
        "act_end_i": end,
        "ev_end_i": ev_end,
    })


# ======================================================================================
# Consents  (effective-dated history, never overwritten)
# ======================================================================================
CONSENT_SPECS = [("EMAIL", "MARKETING", 0.70, 0.10), ("SMS", "MARKETING", 0.30, 0.06),
                 ("PUSH", "MARKETING", 0.30, 0.06), ("PHONE", "MARKETING", 0.08, 0.05),
                 ("EMAIL", "SERVICE", 0.90, 0.02), ("PHONE", "SERVICE", 0.60, 0.02)]


def gen_consents(rng: np.random.Generator, cust: pd.DataFrame) -> pd.DataFrame:
    n = len(cust)
    signup = cust["signup_i"].to_numpy()
    arch = cust["archetype"].to_numpy()
    gfac = np.where(arch == "DORMANT", 0.7, 1.0)
    wfac = np.select([arch == "DORMANT", arch == "HIGH_SERVICE", arch == "AT_RISK"], [2.0, 2.0, 1.5], 1.0)
    cluster = rng.random(n) < 0.03                      # withdraws ALL marketing consent at once
    parts = []
    for ch, pu, pg, pw in CONSENT_SPECS:
        g = rng.random(n) < pg * gfac
        gdate = np.minimum(signup + rng.integers(0, 31, n), END_I)
        w = g & ((rng.random(n) < pw * wfac) | (cluster & (pu == "MARKETING")))
        span = END_I - gdate
        w &= span >= 2
        wdate = gdate + 1 + (rng.random(n) * np.maximum(span, 1)).astype("int64")
        r = w & (rng.random(n) < 0.05)
        span2 = END_I - wdate
        r &= span2 >= 1
        rdate = wdate + 1 + (rng.random(n) * np.maximum(span2, 1)).astype("int64")
        src_g = rng.choice(["WEB_FORM", "CHECKOUT", "APP", "PREFERENCE_CENTER"], size=n, p=[0.35, 0.30, 0.20, 0.15])
        src_w = rng.choice(["UNSUBSCRIBE_LINK", "PREFERENCE_CENTER", "SUPPORT_AGENT"], size=n, p=[0.55, 0.30, 0.15])
        src_r = rng.choice(["WEB_FORM", "CHECKOUT", "APP", "PREFERENCE_CENTER"], size=n, p=[0.35, 0.30, 0.20, 0.15])
        for mask, status, dts, src in ((g, "GRANTED", gdate, src_g), (w, "WITHDRAWN", wdate, src_w),
                                       (r, "GRANTED", rdate, src_r)):
            ci = np.where(mask)[0]
            parts.append(pd.DataFrame({"cidx": ci, "channel": ch, "purpose": pu, "status": status,
                                       "from_i": dts[ci], "source": src[ci]}))
    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values(["cidx", "channel", "purpose", "from_i"], kind="mergesort").reset_index(drop=True)
    nxt = df.groupby(["cidx", "channel", "purpose"])["from_i"].shift(-1)
    df["to_i"] = nxt
    df = df.sort_values(["from_i", "cidx", "channel", "purpose"], kind="mergesort").reset_index(drop=True)
    df["consent_id"] = [f"CON-{i:07d}" for i in range(1, len(df) + 1)]
    return df


def consent_state(consents: pd.DataFrame, n_cust: int, t_i: int):
    """Granted / ever-withdrawn flags per (channel, purpose) at date t (effective_from <= t < effective_to)."""
    granted, withdrawn = {}, {}
    for ch in C.CHANNELS:
        for pu in C.CONSENT_PURPOSES:
            granted[(ch, pu)] = np.zeros(n_cust, dtype=bool)
            withdrawn[(ch, pu)] = np.zeros(n_cust, dtype=bool)
    c = consents[consents["from_i"] <= t_i]
    last = c.sort_values(["cidx", "channel", "purpose", "from_i"], kind="mergesort") \
            .drop_duplicates(["cidx", "channel", "purpose"], keep="last")
    for (ch, pu), g in last.groupby(["channel", "purpose"]):
        granted[(ch, pu)][g.loc[g["status"] == "GRANTED", "cidx"].to_numpy()] = True
    for (ch, pu), g in c[c["status"] == "WITHDRAWN"].groupby(["channel", "purpose"]):
        withdrawn[(ch, pu)][g["cidx"].to_numpy()] = True
    return granted, withdrawn


# ======================================================================================
# Orders / order lines / returns
# ======================================================================================
LINES_P = [0.45, 0.28, 0.15, 0.08, 0.04]
DISC_VALUES = np.array([0.0, 5.0, 10.0, 15.0, 20.0])
DISC_P = [0.70, 0.10, 0.10, 0.07, 0.03]

ORDER_COLS = ["ok", "cidx", "date_i", "order_type", "parent_ok", "order_channel", "gross_amount",
              "discount_amount", "net_amount"]
LINE_COLS = ["ok", "line_number", "pidx", "quantity", "unit_price", "discount_pct", "line_amount", "p_ok",
             "p_ln", "return_reason"]


class OrderFactory:
    """Builds SALE orders + lines and RETURN orders + lines. Totals are derived from lines, never drawn."""

    def __init__(self, rng, prod: ProductBook, cust: pd.DataFrame):
        self.rng, self.prod = rng, prod
        self.pref_cat = cust["pref_cat"].to_numpy()
        self.ret_prop = cust["ret_prop"].to_numpy()
        self.next_key = 0

    def sales(self, cidx: np.ndarray, date_i: np.ndarray, disc_override: np.ndarray | None = None):
        rng, n = self.rng, len(cidx)
        if n == 0:
            return pd.DataFrame(columns=ORDER_COLS), pd.DataFrame(columns=LINE_COLS)
        keys = np.arange(self.next_key, self.next_key + n)
        self.next_key += n
        nl = rng.choice(5, size=n, p=LINES_P) + 1
        starts = np.cumsum(nl) - nl
        total = int(nl.sum())
        oi = np.repeat(np.arange(n), nl)
        line_no = np.arange(total) - starts[oi] + 1
        pidx = self.prod.pick(rng, self.pref_cat[cidx[oi]], date_i[oi])
        qty = rng.choice([1, 2, 3], size=total, p=[0.80, 0.14, 0.06])
        disc = DISC_VALUES[rng.choice(5, size=total, p=DISC_P)]
        if disc_override is not None:
            ov = disc_override[oi]
            disc = np.where(np.isnan(ov), disc, ov)
        price = self.prod.price[pidx]
        amt = np.round(qty * price * (1 - disc / 100.0), 2)
        gross = np.round(np.add.reduceat(qty * price, starts), 2)
        net = np.round(np.add.reduceat(amt, starts), 2)
        orders = pd.DataFrame({
            "ok": keys, "cidx": cidx, "date_i": date_i, "order_type": "SALE", "parent_ok": -1,
            "order_channel": rng.choice(C.ORDER_CHANNELS, size=n, p=[0.5, 0.3, 0.2]),
            "gross_amount": gross, "discount_amount": np.round(gross - net, 2), "net_amount": net})
        lines = pd.DataFrame({
            "ok": keys[oi], "line_number": line_no, "pidx": pidx, "quantity": qty, "unit_price": price,
            "discount_pct": disc, "line_amount": amt, "p_ok": -1, "p_ln": 0, "return_reason": None})
        return orders, lines

    def returns(self, orders: pd.DataFrame, lines: pd.DataFrame, n_returns: int):
        """Explicit RETURN events: 60% full returns (all lines), 40% partial (line 1, random quantity)."""
        rng = self.rng
        s = orders[orders["order_type"] == "SALE"]
        elig = s[s["date_i"] <= END_I - C.RETURN_WINDOW_MAX_DAYS].reset_index(drop=True)
        R = min(n_returns, len(elig))
        w = self.ret_prop[elig["cidx"].to_numpy()]
        sel = rng.choice(len(elig), size=R, replace=False, p=w / w.sum())
        par = elig.iloc[np.sort(sel)].reset_index(drop=True)
        keys = np.arange(self.next_key, self.next_key + R)
        self.next_key += R
        delta = rng.integers(C.RETURN_WINDOW_MIN_DAYS, C.RETURN_WINDOW_MAX_DAYS + 1, R)
        full = rng.random(R) < 0.6
        pl = lines[lines["ok"].isin(par["ok"])].copy()
        ridx_map = pd.Series(np.arange(R), index=par["ok"].to_numpy())
        pl["ridx"] = pl["ok"].map(ridx_map).to_numpy()
        keep = full[pl["ridx"].to_numpy()] | (pl["line_number"].to_numpy() == 1)
        pl = pl[keep].reset_index(drop=True)
        q = pl["quantity"].to_numpy()
        rq = np.where(full[pl["ridx"].to_numpy()], q, rng.integers(1, q + 1))
        amt = -np.round(rq * pl["unit_price"].to_numpy() * (1 - pl["discount_pct"].to_numpy() / 100.0), 2)
        r = pl["ridx"].to_numpy()
        gross = np.round(np.bincount(r, weights=-rq * pl["unit_price"].to_numpy(), minlength=R), 2)
        net = np.round(np.bincount(r, weights=amt, minlength=R), 2)
        ro = pd.DataFrame({
            "ok": keys, "cidx": par["cidx"].to_numpy(), "date_i": par["date_i"].to_numpy() + delta,
            "order_type": "RETURN", "parent_ok": par["ok"].to_numpy(),
            "order_channel": par["order_channel"].to_numpy(), "gross_amount": gross,
            "discount_amount": np.round(gross - net, 2), "net_amount": net})
        rl = pd.DataFrame({
            "ok": keys[r], "line_number": pl["line_number"].to_numpy(), "pidx": pl["pidx"].to_numpy(),
            "quantity": -rq, "unit_price": pl["unit_price"].to_numpy(),
            "discount_pct": pl["discount_pct"].to_numpy(), "line_amount": amt,
            "p_ok": pl["ok"].to_numpy(), "p_ln": pl["line_number"].to_numpy(),
            "return_reason": rng.choice(C.RETURN_REASONS, size=len(pl), p=[0.25, 0.20, 0.25, 0.20, 0.10])})
        return ro, rl


def allocate_sale_dates(rng, cust: pd.DataFrame, n_orders: int, lo_i: int, hi_i: int, force_first: bool):
    """Allocate ``n_orders`` SALE orders to customers and dates.

    weight = archetype order rate x active days inside [lo_i, hi_i].  When ``force_first`` every
    non-prospect customer gets one order first (so no 'customer' is order-less), the rest are multinomial.
    """
    start = np.maximum(cust["act_start_i"].to_numpy(), lo_i)
    end = np.minimum(cust["act_end_i"].to_numpy(), hi_i)
    span = np.maximum(end - start + 1, 0)
    w = cust["rate"].to_numpy() * span
    counts = np.zeros(len(cust), dtype="int64")
    if force_first:
        has = (cust["rate"].to_numpy() > 0)
        counts[has] = 1
        n_orders -= int(has.sum())
    counts += rng.multinomial(max(n_orders, 0), w / w.sum())
    cidx = np.repeat(np.arange(len(cust)), counts)
    date_i = start[cidx] + (rng.random(len(cidx)) * span[cidx]).astype("int64")
    return cidx, date_i


# ======================================================================================
# Support cases
# ======================================================================================
def gen_cases(rng, cust: pd.DataFrame, sales: pd.DataFrame, n: int, lo_i: int, hi_i: int) -> pd.DataFrame:
    """Cases linked (80%) to a SALE order of the window or free-standing (20%), opened lo_i..hi_i."""
    n_ol = int(round(n * 0.8))
    elig = sales[(sales["date_i"] >= lo_i) & (sales["date_i"] <= hi_i)]
    if len(elig) == 0:
        n_ol = 0
    n_na = n - n_ol
    cp = cust["case_prop"].to_numpy()
    sig = cust["signup_i"].to_numpy()
    if n_ol:
        w = cp[elig["cidx"].to_numpy()]
        pick = rng.choice(len(elig), size=n_ol, p=w / w.sum())
        e = elig.iloc[pick]
        ol_cidx = e["cidx"].to_numpy()
        ol_ok = e["ok"].to_numpy()
        ol_day = np.minimum(e["date_i"].to_numpy() + rng.integers(0, 21, n_ol), hi_i)
    else:
        ol_cidx = ol_ok = ol_day = np.array([], dtype="int64")
    lo2 = np.maximum(sig, lo_i)
    span_c = np.maximum(hi_i - lo2 + 1, 0)
    cw = cp * span_c
    na_cidx = rng.choice(len(cust), size=n_na, p=cw / cw.sum())
    na_span = span_c[na_cidx]
    na_day = lo2[na_cidx] + (rng.random(n_na) * na_span).astype("int64")
    cidx = np.concatenate([ol_cidx, na_cidx])
    order_ok = np.concatenate([ol_ok, np.full(n_na, -1)])
    open_day = np.concatenate([ol_day, na_day])
    linked = order_ok >= 0
    N = len(cidx)
    cat_l = rng.choice(C.CASE_CATEGORIES[:6], size=N, p=[0.30, 0.20, 0.15, 0.15, 0.10, 0.10])
    cat_n = rng.choice(["ACCOUNT", "BILLING", "WEBSITE_ISSUE"], size=N, p=[0.5, 0.25, 0.25])
    category = np.where(linked, cat_l, cat_n)
    prio = rng.choice(C.CASE_PRIORITIES, size=N, p=[0.50, 0.35, 0.15])
    pf = np.select([prio == "LOW", prio == "MEDIUM"], [1.0, 1.3], 1.8)
    handling = np.maximum(5, np.round(rng.lognormal(np.log(25), 0.6, N) * pf)).astype(int)
    rf = np.select([prio == "LOW", prio == "MEDIUM"], [1.3, 1.0], 0.7)
    res_days = np.maximum(1, np.ceil(rng.lognormal(np.log(2), 0.7, N) * rf)).astype("int64")
    opened = open_day.astype("int64") * 86400 + rng.integers(8 * 3600, 20 * 3600, N)
    closed = opened + res_days * 86400 + rng.integers(0, 4 * 3600, N)
    end_excl = (END_I + 1) * 86400
    is_open = closed >= end_excl
    esc = (prio == "HIGH") & (rng.random(N) < 0.30)
    cost = np.round(handling / 60.0 * C.AGENT_HOURLY_RATE + C.ESCALATION_COST * esc, 2)
    return pd.DataFrame({
        "cidx": cidx, "order_ok": order_ok, "opened_s": opened,
        "closed_s": np.where(is_open, -1, closed), "category": category, "priority": prio,
        "status": np.where(is_open, "OPEN", "CLOSED"), "escalated": esc.astype(int),
        "handling_minutes": handling, "cost_amount": cost})


# ======================================================================================
# Engagement events
# ======================================================================================
BASE_TYPE_P = [0.30, 0.15, 0.28, 0.08, 0.06, 0.03, 0.06, 0.02, 0.02, 0.0]
HOUR_W = np.array([1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 7, 8, 9, 9, 8, 5, 3], dtype=float)
HOUR_W /= HOUR_W.sum()
_DIGITAL = {"SITE_VISIT", "PRODUCT_VIEW", "SEARCH", "CART_ADD", "WISHLIST_ADD"}


def gen_baseline_events(rng, cust: pd.DataFrame, prod: ProductBook, n: int) -> pd.DataFrame:
    sig = cust["signup_i"].to_numpy()
    span = np.maximum(cust["ev_end_i"].to_numpy() - sig + 1, 1)
    w = cust["eng"].to_numpy() * cust["ev_factor"].to_numpy() * span
    counts = rng.multinomial(n, w / w.sum())
    cidx = np.repeat(np.arange(len(cust)), counts)
    d = sig[cidx] + (rng.random(n) * span[cidx]).astype("int64")
    secs = rng.choice(24, size=n, p=HOUR_W) * 3600 + rng.integers(0, 3600, n)
    ts = d * 86400 + secs
    t_idx = rng.choice(len(C.EVENT_TYPES), size=n, p=BASE_TYPE_P)
    etype = np.array(C.EVENT_TYPES)[t_idx]
    digital = np.isin(etype, list(_DIGITAL))
    web_app = np.where(rng.random(n) < 0.6, "WEB", "APP")
    channel = np.select(
        [digital, etype == "APP_OPEN", np.char.startswith(etype.astype(str), "EMAIL"), etype == "PUSH_OPEN"],
        [web_app, "APP", "EMAIL", "PUSH"], "SMS")
    prod_ev = np.isin(etype, ["PRODUCT_VIEW", "CART_ADD", "WISHLIST_ADD"])
    pidx = np.full(n, -1, dtype="int64")
    k = int(prod_ev.sum())
    if k:
        pidx[prod_ev] = prod.pick(rng, cust["pref_cat"].to_numpy()[cidx[prod_ev]], d[prod_ev])
    return pd.DataFrame({"cidx": cidx, "ts_s": ts, "event_type": etype, "channel": channel,
                         "pidx": pidx, "action_id": None})
