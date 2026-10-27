"""Unit tests for scoring bands, the rule engine, return netting and the no-look-ahead rule."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from segmentation_gen import segmentation as SG  # noqa: E402


class TestScoringBands(unittest.TestCase):
    def test_recency_boundaries(self):
        d = np.array([1, 30, 31, 60, 61, 120, 121, 240, 241, 700, np.nan])
        self.assertEqual(SG.recency_score(d).tolist(), [5, 5, 4, 4, 3, 3, 2, 2, 1, 1, 0])

    def test_frequency_boundaries(self):
        f = np.array([0, 1, 2, 3, 4, 5, 7, 8, 40])
        self.assertEqual(SG.frequency_score(f).tolist(), [0, 1, 2, 3, 3, 4, 4, 5, 5])

    def test_monetary_boundaries(self):
        m = np.array([-5, 49.99, 50, 149.99, 150, 399.99, 400, 999.99, 1000])
        self.assertEqual(SG.monetary_score(m, np.ones(len(m))).tolist(), [1, 1, 2, 2, 3, 3, 4, 4, 5])
        self.assertEqual(SG.monetary_score(np.array([500.0]), np.array([0])).tolist(), [0])


def _fixture():
    cust = pd.DataFrame({"customer_id": ["CUS-000001", "CUS-000002", "CUS-000003", "CUS-000004"],
                         "signup_date": pd.to_datetime(["2025-01-01"] * 4)})
    rows = [  # customer, date, type, amount
        ("CUS-000001", "2026-10-10", "SALE", 100.0),
        ("CUS-000001", "2026-10-15", "RETURN", -40.0),      # returned 40 of 100 -> netted
        ("CUS-000002", "2026-10-23", "SALE", 999.0),        # ON the snapshot date -> must be ignored
        ("CUS-000003", "2025-02-01", "SALE", 30.0),
    ]
    orders = pd.DataFrame(rows, columns=["customer_id", "order_date", "order_type", "net_amount"])
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    cases = pd.DataFrame({"customer_id": ["CUS-000004"] * 3,
                          "opened_at": pd.to_datetime(["2026-09-01", "2026-09-10", "2026-10-01"]),
                          "cost_amount": [30.0, 30.0, 30.0]})
    return cust, orders, cases


class TestFeaturesAndRules(unittest.TestCase):
    def setUp(self):
        self.cust, self.orders, self.cases = _fixture()
        self.f = SG.compute_features(self.cust, self.orders, self.cases, "2026-10-23").set_index("customer_id")

    def test_returns_are_netted(self):
        r = self.f.loc["CUS-000001"]
        self.assertEqual((r["gross_spend"], r["returned_amount"], r["net_spend"]), (100.0, 40.0, 60.0))
        self.assertAlmostEqual(r["return_rate"], 0.4)
        self.assertEqual(r["frequency"], 1)               # the return is not a purchase

    def test_no_look_ahead(self):
        r = self.f.loc["CUS-000002"]
        self.assertEqual(r["frequency"], 0)
        self.assertEqual(r["r_score"], 0)

    def test_service_cost_window(self):
        r = self.f.loc["CUS-000004"]
        self.assertEqual((r["cases_180d"], r["service_cost_180d"]), (3, 90.0))
        self.assertAlmostEqual(r["service_cost_ratio"], 90.0)   # denominator floored at 1

    def test_exclusive_first_match_and_overlay(self):
        rules = SG.build_rules()
        m = SG.assign_segments(self.f.reset_index(), rules, "2026-10-23")
        code = dict(zip(rules["segment_id"], rules["segment_code"]))
        m["code"] = m["segment_id"].map(code)
        by = m.groupby("customer_id")["code"].apply(set)
        self.assertIn("NEW_CUSTOMER", by["CUS-000001"])           # first sale 13 days ago beats R/F/M rules
        self.assertIn("PROSPECT", by["CUS-000002"])
        self.assertIn("HIBERNATING", by["CUS-000003"])            # last sale ~629 days ago
        self.assertIn("HIGH_SERVICE_COST", by["CUS-000004"])      # overlay in addition to PROSPECT
        self.assertIn("PROSPECT", by["CUS-000004"])
        excl = set(rules.loc[rules["exclusive_group"] != "NONE", "segment_id"])
        per = m[m["segment_id"].isin(excl)].groupby("customer_id").size()
        self.assertTrue((per == 1).all(), "exactly one exclusive segment per customer")

    def test_membership_id_convention(self):
        m = SG.snapshot_memberships(self.cust, self.orders, self.cases, SG.build_rules(), "2026-10-23")
        self.assertTrue(m["membership_id"].str.fullmatch(r"MEM-SEG-2026-\d{6}-CUS-\d{6}").all())


if __name__ == "__main__":
    unittest.main()
