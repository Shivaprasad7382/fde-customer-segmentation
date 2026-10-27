# Design notes: business logic, rules and assumptions

## 1. Generation pipeline (parents before children, derived from sources)

| # | Stage | Key point |
|---|---|---|
| 1 | Products, customers, consents | Customers get a hidden *archetype* (CHAMPION, LOYAL, REGULAR, OCCASIONAL, NEW, AT_RISK, DORMANT, HIGH_SERVICE, RETURNER, PROSPECT) that drives order rate, active window, engagement, return and case propensity. Archetypes are published **only** in `ground_truth/`. |
| 2 | Orders, lines, returns | Order counts are *allocated* (every non-prospect gets one order, the remainder is multinomial on rate x active days) so volumes are exact. Order totals are summed from lines. Returns are explicit `RETURN` orders linked to a `SALE`; 60% full, 40% partial. |
| 3 | Support cases | 80% linked to an order. `cost_amount = minutes/60 x 24 + 15 x escalated`. |
| 4 | Snapshot-1 segmentation (2026-08-01) | Only records dated **before** the snapshot are used (no look-ahead). |
| 5 | Action planning | eligibility -> channel selection -> consent -> suppression rules -> frequency cap -> 10% holdout -> send / delivery. Every recommendation is logged, including suppressed ones. |
| 6 | Campaign response | `p = base[action] x (0.6 + 0.8 x engagement) x channel_multiplier` (capped at 0.5). Responders place an extra order 1-13 days after the send (discount from the action's discount). Count is capped by a reserve so the order total stays exact; any shortfall is topped up with organic orders dated after the snapshot. |
| 7 | Order IDs, post-snapshot cases, outcome evaluation | `success_flag` / `attributed_order_id` are *derived* from orders and cases (same function the validator re-runs). |
| 8 | Engagement events | Opens / clicks for delivered messages (probability rises with engagement; clicks more likely on success), then baseline events fill the exact event count. |
| 9 | Snapshot-2 segmentation (2026-10-23) | Customer derived fields (lifetime spend, lifecycle state) computed from orders; membership history merged with effective dates. |
| 10 | Anomaly injection | Four labelled edge cases (below). |

## 2. Segment rules (declarative, in `segment_rules`)

RFM scores (bands in `config.py`):

| Score | Recency (days since last sale) | Frequency (sale orders) | Monetary (net spend, returns netted) |
|---|---|---|---|
| 5 | <= 30 | >= 8 | >= 1000 |
| 4 | <= 60 | 5-7 | 400 - <1000 |
| 3 | <= 120 | 3-4 | 150 - <400 |
| 2 | <= 240 | 2 | 50 - <150 |
| 1 | > 240 | 1 | < 50 |
| 0 | no sale | no sale | no sale |

Exclusive group `LIFECYCLE_VALUE` (priority order, first match wins, exactly one per customer):
`PROSPECT` (F=0) -> `NEW_CUSTOMER` (first sale <= 60d ago) -> `CHAMPIONS` (R>=4,F>=4,M>=4) -> `LOYAL` (R>=3,F>=3) ->
`POTENTIAL_LOYALIST` (R>=3) -> `AT_RISK` (R=2) -> `HIBERNATING` (R=1).

Overlays (independent flags): `HIGH_SERVICE_COST` (>= 3 cases in 180d and service cost >= 10% of net spend),
`HIGH_RETURNS` (>= 3 orders and returned value >= 30% of gross).

Every membership stores the metrics it was scored on and a plain-language `assignment_reason`
(e.g. `CHAMPIONS (priority 3, rule v1): R=5 F=5 M=4`).

## 3. Actions, eligibility and suppression

Eligibility is the bridge `action_eligibility` (segment -> actions, lowest `priority` first); the first action is recommended.
Suppression rules are applied in this order and the first hit is logged in `suppression_reason`:

1. `NO_CONSENT` / `CONSENT_WITHDRAWN` - no GRANTED consent for any allowed channel for the action's purpose on the recommendation date (WITHDRAWN if the customer ever withdrew).
2. `OPEN_SUPPORT_CASE` - MARKETING actions are held while a case is open.
3. `HIGH_SERVICE_COST_DISCOUNT_BLOCK` - discount actions never go to HIGH_SERVICE_COST accounts.
4. `FREQUENCY_CAP` - at most `contact_frequency_cap_30d` sends per customer per wave (exclusive-segment action wins over overlay).

Channel selection: customer's `preferred_channel` if allowed and consented, otherwise EMAIL > PUSH > SMS > PHONE.
`preferred_channel` is a *contact preference*; it never overrides consent.

Success metrics (all derived from source tables):

| Metric | Definition |
|---|---|
| `ORDER_14D` | a SALE dated within 14 days of `sent_at` (of `recommended_at` for HOLDOUT). Lift is measured treated vs. 10% holdout. |
| `CASE_FREE_30D` | no support case opened within 30 days of the send |
| `NO_RETURN_30D` | no RETURN order within 30 days of the send |

`CASE_FREE_30D` and `NO_RETURN_30D` have no simulated causal effect (and no holdout); they measure outcomes, not lift.

## 4. Injected anomalies (`ground_truth/ground_truth.csv`)

| issue_type | What is changed | How it is detected |
|---|---|---|
| `CONFLICTING_EXCLUSIVE_SEGMENT` | a second, different exclusive-group membership for the same customer | `RULES.exclusive_group_single`, `RULES.membership_follows_rules` |
| `ACTION_VIOLATES_CONSENT` | a consent-suppressed recommendation is flipped to SEND on a channel with no consent | `CONTACT.consent_enforced` |
| `RETURN_NOT_NETTED` | membership `net_spend` set to `gross_spend` for a customer with returns | `MATH.membership_net_spend`, `MATH.membership_metrics` |
| `OUTCOME_WRONG_ACTION` | outcome logged against a catalogue action that is not eligible for the segment (same purpose and metric, so no other rule is broken) | `ACTION.eligible_for_segment` |

Each injector touches only the rows it labels. The validator never reads `ground_truth/` while checking; it is read afterwards to
verify that every injected row was found (recall) and that nothing else was flagged (precision).

## 5. Assumptions and interpretations (please review)

* **`ACTN-2026-000001` identifies an action instance** (one recommendation + its outcome, in `action_outcomes`) because the
  convention carries a year and a running counter. Catalogue entries are `CAT-0001`, eligibility rows `ELG-0001`.
  Changing this is a one-line change in `actions.py` / `schema.py`.
* IDs not specified in the brief use the same pattern: `PRD-000001`, `EVT-00000001`, `SUP-000001`, `CON-0000001`.
* `ORD-<year>-<counter>`: the year is the order date's year, the counter restarts each year. Order lines are `<order_id>-L01`.
* Two segmentation snapshots (2026-08-01, 2026-10-23) give real membership history; a single campaign wave runs on snapshot 1.
* Currency is USD; all timestamps are naive UTC. No real-world personal data: customers have only IDs and coarse attributes.
* Customers are never deleted or updated in place: consent changes, returns, segment moves are new rows.
