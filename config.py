"""Central configuration: seed, calendar, scale presets, enums and business constants.

Everything that is a *documented business rule* lives here so that the generator,
the validator and the docs all read the same values.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SEED = 20261022

# ---- calendar -----------------------------------------------------------------
HISTORY_START = np.datetime64("2024-10-01")   # first possible sign-up
DATA_END = np.datetime64("2026-10-22")        # last day with data (inclusive)
SNAP1 = np.datetime64("2026-08-01")           # segmentation snapshot #1 + campaign wave
SNAP2 = np.datetime64("2026-10-23")           # segmentation snapshot #2 (day after DATA_END)
RULE_EFFECTIVE_FROM = np.datetime64("2026-01-01")

# ---- scale --------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    name: str
    customers: int
    orders: int          # total rows in orders (sales + returns)
    events: int          # total rows in engagement_events

    @property
    def n_products(self) -> int:
        return 240 if self.customers >= 20000 else (120 if self.customers >= 2000 else 64)

    @property
    def n_returns(self) -> int:
        return int(round(0.075 * self.orders))

    @property
    def n_reserve(self) -> int:           # orders reserved for campaign-induced + top-up sales
        return int(round(0.006 * self.orders))

    @property
    def n_cases(self) -> int:
        return int(round(0.30 * self.orders))

    @property
    def n_anomalies(self) -> int:         # per anomaly type
        return max(3, int(round(0.003 * self.customers)))


SCALES = {
    "tiny": Scale("tiny", 400, 1_600, 8_000),
    "starter": Scale("starter", 5_000, 20_000, 100_000),
    "full": Scale("full", 50_000, 200_000, 1_000_000),
}

# ---- enums --------------------------------------------------------------------
REGIONS = ["NA", "EU", "APAC", "LATAM", "MEA"]
ACQ_CHANNELS = ["ORGANIC_SEARCH", "PAID_SOCIAL", "REFERRAL", "EMAIL_SIGNUP", "PARTNER"]
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]
CHANNELS = ["EMAIL", "SMS", "PUSH", "PHONE"]
CONTACT_FIXED_ORDER = ["EMAIL", "PUSH", "SMS", "PHONE"]
ORDER_CHANNELS = ["WEB", "APP", "STORE"]
ORDER_TYPES = ["SALE", "RETURN"]
RETURN_REASONS = ["DEFECTIVE", "NOT_AS_DESCRIBED", "WRONG_SIZE_FIT", "CHANGED_MIND", "LATE_DELIVERY"]
CUSTOMER_TYPES = ["CUSTOMER", "PROSPECT"]
LIFECYCLE_STATES = ["PROSPECT", "NEW", "ACTIVE", "AT_RISK", "DORMANT"]
PRODUCT_STATUS = ["ACTIVE", "DISCONTINUED"]
EVENT_TYPES = ["SITE_VISIT", "APP_OPEN", "PRODUCT_VIEW", "SEARCH", "CART_ADD", "WISHLIST_ADD",
               "EMAIL_OPEN", "EMAIL_CLICK", "PUSH_OPEN", "SMS_CLICK"]
EVENT_CHANNELS = ["WEB", "APP", "EMAIL", "SMS", "PUSH"]
CASE_CATEGORIES = ["DELIVERY_DELAY", "PRODUCT_DEFECT", "BILLING", "RETURN_REFUND", "WRONG_ITEM",
                   "ACCOUNT", "WEBSITE_ISSUE"]
CASE_PRIORITIES = ["LOW", "MEDIUM", "HIGH"]
CASE_STATUS = ["OPEN", "CLOSED"]
CONSENT_PURPOSES = ["MARKETING", "SERVICE"]
CONSENT_STATUS = ["GRANTED", "WITHDRAWN"]
CONSENT_SOURCES = ["WEB_FORM", "CHECKOUT", "APP", "PREFERENCE_CENTER", "UNSUBSCRIBE_LINK", "SUPPORT_AGENT"]
RULE_TYPES = ["RFM", "OVERLAY"]
EXCLUSIVE_GROUPS = ["LIFECYCLE_VALUE", "NONE"]
RULE_STATUS = ["ACTIVE", "RETIRED"]
ACTION_TYPES = ["ONBOARDING", "RETENTION", "WINBACK", "UPSELL", "SERVICE", "EDUCATION", "ACQUISITION"]
SUCCESS_METRICS = ["ORDER_14D", "CASE_FREE_30D", "NO_RETURN_30D"]
DECISIONS = ["SEND", "SUPPRESS", "HOLDOUT"]
SUPPRESSION_REASONS = ["NO_CONSENT", "CONSENT_WITHDRAWN", "OPEN_SUPPORT_CASE",
                       "HIGH_SERVICE_COST_DISCOUNT_BLOCK", "FREQUENCY_CAP"]
DELIVERY_STATUS = ["DELIVERED", "FAILED", "NOT_APPLICABLE"]
OUTCOME_STATUS = ["SUPPRESSED", "HOLDOUT", "DELIVERY_FAILED", "NO_RESPONSE", "ENGAGED", "SUCCESS"]

# ---- business rules -----------------------------------------------------------
# RFM scoring bands (documented in README / data dictionary)
RECENCY_CUTS = [30, 60, 120, 240]          # days; score 5 if <=30, 4 if <=60, 3 if <=120, 2 if <=240, else 1
FREQUENCY_CUTS = [2, 3, 5, 8]              # orders; 1->1, 2->2, 3-4->3, 5-7->4, >=8->5 ; 0 -> 0
MONETARY_CUTS = [50, 150, 400, 1000]       # net spend; <50->1, <150->2, <400->3, <1000->4, else 5

LIFECYCLE_NEW_DAYS = 60
LIFECYCLE_ACTIVE_DAYS = 90
LIFECYCLE_AT_RISK_DAYS = 180
SERVICE_WINDOW_DAYS = 180

AGENT_HOURLY_RATE = 24.0
ESCALATION_COST = 15.0
HOLDOUT_RATE = 0.10
ATTRIBUTION_WINDOW_DAYS = 14
SERVICE_METRIC_WINDOW_DAYS = 30
RETURN_WINDOW_MIN_DAYS = 3
RETURN_WINDOW_MAX_DAYS = 30

# customer archetypes (latent generator state; published ONLY in ground_truth/)
ARCHETYPES = ["CHAMPION", "LOYAL", "REGULAR", "OCCASIONAL", "NEW", "AT_RISK", "DORMANT",
              "HIGH_SERVICE", "RETURNER", "PROSPECT"]
ARCH_P = [0.06, 0.14, 0.22, 0.19, 0.08, 0.08, 0.10, 0.04, 0.03, 0.06]
ARCH_ORDER_RATE = {"CHAMPION": 0.030, "LOYAL": 0.012, "REGULAR": 0.006, "OCCASIONAL": 0.002,
                   "NEW": 0.020, "AT_RISK": 0.008, "DORMANT": 0.010, "HIGH_SERVICE": 0.008,
                   "RETURNER": 0.010, "PROSPECT": 0.0}
ARCH_EVENT_FACTOR = {"CHAMPION": 1.5, "LOYAL": 1.2, "REGULAR": 1.0, "OCCASIONAL": 0.7, "NEW": 1.3,
                     "AT_RISK": 0.8, "DORMANT": 0.5, "HIGH_SERVICE": 1.0, "RETURNER": 1.0, "PROSPECT": 0.6}
ARCH_ENG_BETA = {"CHAMPION": (5, 2), "LOYAL": (4, 2.5), "REGULAR": (3, 3), "OCCASIONAL": (2, 4),
                 "NEW": (3, 3), "AT_RISK": (2, 4), "DORMANT": (1.5, 5), "HIGH_SERVICE": (3, 3),
                 "RETURNER": (3, 3), "PROSPECT": (2, 4)}

# campaign response model (documented): p = base * (0.6 + 0.8*engagement) * channel_multiplier, capped 0.5
RESPONSE_BASE = {"WELCOME_SERIES": 0.10, "VIP_EARLY_ACCESS": 0.12, "LOYALTY_REWARD": 0.08,
                 "CROSS_SELL_BUNDLE": 0.06, "WINBACK_OFFER_10": 0.05, "REACTIVATION_DISCOUNT_20": 0.03,
                 "PROSPECT_NURTURE": 0.04}
CHANNEL_RESPONSE_MULT = {"EMAIL": 1.0, "PUSH": 1.1, "SMS": 1.3, "PHONE": 1.5}
DELIVERY_RATE = {"EMAIL": 0.97, "SMS": 0.98, "PUSH": 0.90, "PHONE": 0.60}
