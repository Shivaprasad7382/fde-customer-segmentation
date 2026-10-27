"""Integration tests on a tiny dataset: volumes, IDs, joins, determinism, ground-truth separation, anomalies."""
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from segmentation_gen import config as C  # noqa: E402
from segmentation_gen import export as X  # noqa: E402
from segmentation_gen import generate as G  # noqa: E402
from segmentation_gen import schema as S  # noqa: E402
from segmentation_gen import validate as V  # noqa: E402

SCALE = C.SCALES["tiny"]
TMP = tempfile.mkdtemp(prefix="seg_tests_")
DIR_A, DIR_B, DIR_C = (os.path.join(TMP, n) for n in "abc")


def _build(path, seed):
    tables, gt, labels = G.generate_tables(SCALE, seed, log=lambda *_: None)
    X.export_all(tables, gt, labels, path, SCALE, seed, "test", formats=("csv", "sql"), log=lambda *_: None)
    return tables, gt, labels


def setUpModule():
    global TABLES, GT, LABELS, SUMMARY
    TABLES, GT, LABELS = _build(DIR_A, C.SEED)
    _build(DIR_B, C.SEED)
    _build(DIR_C, C.SEED + 1)
    SUMMARY = V.validate_dir(DIR_A)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read(d, name):
    return pd.read_csv(os.path.join(d, "data", f"{name}.csv"), dtype=str, keep_default_na=False, na_values=[""])


class TestVolumesAndIds(unittest.TestCase):
    def test_exact_requested_volumes(self):
        self.assertEqual(len(TABLES["customers"]), SCALE.customers)
        self.assertEqual(len(TABLES["orders"]), SCALE.orders)
        self.assertEqual(len(TABLES["engagement_events"]), SCALE.events)

    def test_id_patterns_and_unique_pks(self):
        for name, t in S.TABLES.items():
            df = read(DIR_A, name)
            self.assertTrue(df[t.pk].is_unique, name)
            pat = t.col(t.pk).pattern
            self.assertTrue(df[t.pk].str.fullmatch(pat).all(), f"{name} {pat}")

    def test_required_id_conventions(self):
        self.assertEqual(read(DIR_A, "customers")["customer_id"].iloc[0], "CUS-000001")
        self.assertTrue(read(DIR_A, "orders")["order_id"].str.fullmatch(r"ORD-\d{4}-\d{6}").all())
        self.assertEqual(read(DIR_A, "segment_rules")["segment_id"].iloc[0], "SEG-2026-000001")
        self.assertEqual(read(DIR_A, "action_outcomes")["action_id"].iloc[0], "ACTN-2026-000001")
        m = read(DIR_A, "segment_memberships")
        self.assertTrue((m["membership_id"] == "MEM-" + m["segment_id"] + "-" + m["customer_id"]).all())

    def test_ids_are_chronological_and_year_scoped(self):
        o = read(DIR_A, "orders")
        self.assertTrue((o["order_id"].str[4:8] == o["order_date"].str[:4]).all())
        self.assertTrue(o["order_date"].is_monotonic_increasing)


class TestDerivedValues(unittest.TestCase):
    def test_lifetime_spend_nets_returns(self):
        o, c = read(DIR_A, "orders"), read(DIR_A, "customers")
        o["net_amount"] = pd.to_numeric(o["net_amount"])
        spend = o.groupby("customer_id")["net_amount"].sum()
        got = pd.to_numeric(c.set_index("customer_id")["lifetime_net_spend"])
        diff = (got - spend.reindex(got.index).fillna(0)).abs()
        self.assertLess(diff.max(), 0.011)
        self.assertGreater((o["order_type"] == "RETURN").sum(), 0)

    def test_returns_are_explicit_events_linked_to_sales(self):
        o = read(DIR_A, "orders")
        ret = o[o["order_type"] == "RETURN"]
        self.assertTrue(ret["parent_order_id"].isin(set(o.loc[o["order_type"] == "SALE", "order_id"])).all())
        self.assertTrue((pd.to_numeric(ret["net_amount"]) < 0).all())


class TestDeterminism(unittest.TestCase):
    def test_same_seed_identical_files(self):
        for name in S.TABLES:
            a = _bytes(os.path.join(DIR_A, "data", f"{name}.csv"))
            b = _bytes(os.path.join(DIR_B, "data", f"{name}.csv"))
            self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest(), name)
        for f in ("data/seed.sql", "ground_truth/ground_truth.csv"):
            self.assertEqual(_bytes(os.path.join(DIR_A, f)), _bytes(os.path.join(DIR_B, f)), f)

    def test_different_seed_differs(self):
        a = _bytes(os.path.join(DIR_A, "data", "orders.csv"))
        c = _bytes(os.path.join(DIR_C, "data", "orders.csv"))
        self.assertNotEqual(a, c)


class TestGroundTruthSeparation(unittest.TestCase):
    def test_no_label_leakage_into_data_tables(self):
        banned = {"archetype", "issue_type", "ground_truth", "injected", "engagement_propensity", "expected_value"}
        for name in S.TABLES:
            cols = set(read(DIR_A, name).columns)
            self.assertFalse(cols & banned, name)
        arch = set(C.ARCHETYPES)
        with open(os.path.join(DIR_A, "data", "customers.csv")) as fh:
            txt = fh.read()
        self.assertFalse(any(f",{a}," in txt for a in ("CHAMPION", "HIGH_SERVICE", "RETURNER")))
        self.assertTrue(os.path.exists(os.path.join(DIR_A, "ground_truth", "ground_truth.csv")))
        self.assertTrue(set(LABELS["customer_archetypes"]["archetype"]) <= arch)

    def test_anomalies_are_labelled_with_issue_type(self):
        self.assertEqual(set(GT["issue_type"]), {"CONFLICTING_EXCLUSIVE_SEGMENT", "ACTION_VIOLATES_CONSENT",
                                                 "RETURN_NOT_NETTED", "OUTCOME_WRONG_ACTION"})
        self.assertTrue((GT.groupby("issue_type").size() == SCALE.n_anomalies).all())


class TestValidation(unittest.TestCase):
    def test_overall_pass_and_every_injected_anomaly_detected(self):
        failed = [c for c in SUMMARY["checks"] if c["status"] == "FAIL"]
        self.assertEqual(failed, [], failed)
        for it, d in SUMMARY["injected_anomaly_detection"].items():
            for chk, v in d["checks"].items():
                self.assertEqual(v["missed"], 0, (it, chk))
                self.assertGreaterEqual(v["flagged_expected"], d["injected"], (it, chk))

    def test_contact_constraints_hold_outside_injected_rows(self):
        out = read(DIR_A, "action_outcomes")
        sends = out[out["decision"] == "SEND"]
        injected = set(GT.loc[GT["issue_type"] == "ACTION_VIOLATES_CONSENT", "record_id"])
        co = read(DIR_A, "consents")
        co = co[co["status"] == "GRANTED"]
        cat = read(DIR_A, "action_catalog").set_index("catalog_id")["purpose"]
        sends = sends[~sends["action_id"].isin(injected)].assign(purpose=lambda d: d["catalog_id"].map(cat))
        j = sends.merge(co, on="customer_id", suffixes=("", "_c"))
        j = j[(j["channel"] == j["channel_c"]) & (j["purpose"] == j["purpose_c"]) & (j["effective_from"] <= j["sent_at"].str[:10])
              & (j["effective_to"].isna() | (j["effective_to"] > j["sent_at"].str[:10]))]
        self.assertEqual(set(j["action_id"]), set(sends["action_id"]))

    def test_scenario_coverage_present(self):
        cov = SUMMARY["coverage"]
        for k in ("new_customers(first_sale<=60d)", "dormant_customers", "return_orders",
                  "high_service_cost_accounts(snapshot2)", "consent_withdrawal_rows"):
            self.assertGreater(cov[k], 0, k)
        self.assertGreater(cov["suppression_reasons"].get("CONSENT_WITHDRAWN", 0), 0)

    def test_validator_catches_real_corruption(self):
        d = os.path.join(TMP, "corrupt")
        shutil.copytree(DIR_A, d)
        p = os.path.join(d, "data", "orders.csv")
        o = pd.read_csv(p, dtype=str, keep_default_na=False, na_values=[""])
        o.loc[0, "net_amount"] = "9999.99"                         # breaks reconciliation to lines
        o.loc[1, "customer_id"] = "CUS-999999"                     # breaks FK
        o.to_csv(p, index=False, na_rep="")
        ol = os.path.join(d, "data", "order_lines.csv")
        L = pd.read_csv(ol, dtype=str, keep_default_na=False, na_values=[""])
        L.loc[2, "line_id"] = L.loc[3, "line_id"]                  # duplicate PK
        L.to_csv(ol, index=False, na_rep="")
        s = V.validate_dir(d)
        failed = {c["check"] for c in s["checks"] if c["status"] == "FAIL"}
        self.assertEqual(s["overall_status"], "FAIL")
        self.assertIn("MATH.order_totals", failed)
        self.assertIn("FK.orders.customer_id", failed)
        self.assertIn("PK_UNIQUE.order_lines", failed)


if __name__ == "__main__":
    unittest.main()
