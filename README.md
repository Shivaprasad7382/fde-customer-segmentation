# Explainable customer segmentation: synthetic data generator, rule engine and action recommender

An end-to-end, reproducible workflow for the *"build explainable customer segments from purchase, engagement and service
data, then recommend appropriate actions"* brief:

1. **Generate** a relational dataset (11 required entities + 1 bridge table) with fixed seed `20261022`;
2. **Segment** customers with a declarative, versioned RFM + overlay rule engine (every membership carries its metrics and a plain-language reason);
3. **Recommend** actions through eligibility, consent and suppression rules, with a 10% holdout control group;
4. **Log and evaluate** recommendations and outcomes (lift vs holdout, funnel, migration between snapshots);
5. **Inject labelled anomalies** (four required edge cases) into a separate `ground_truth/` folder and **validate** that every one is found.

Python 3.10+, `numpy` and `pandas` only (developed on Python 3.12, numpy 2.4, pandas 3.0; no network access needed).

## Quick start

```bash
pip install -r requirements.txt          # numpy, pandas
export PYTHONPATH=src                    # or: pip install -e .

make test                                # 22 tests, ~5 s  (python -m unittest discover -s tests -v)
make starter                             # 5k customers / 20k orders / 100k events  (~6 s incl. validation)
make full                                # 50k customers / 200k orders / 1M events  (~1 min incl. validation)
make reproduce                           # generate twice with the same seed, compare row counts + SHA-256
```

Generator command (the same code produces every scale; `--customers/--orders/--events` override the presets):

```bash
python -m segmentation_gen generate --scale full --out output/full            # csv + jsonl + sql
python -m segmentation_gen generate --scale full --out output/full --formats csv,sql
python -m segmentation_gen generate --customers 20000 --orders 80000 --events 400000 --seed 20261022 --out output/custom
python -m segmentation_gen validate --dir output/full                          # re-run the validation suite
python -m segmentation_gen report   --dir output/full                          # evaluation report
```

Presets: `tiny` (400 / 1,600 / 8,000, used by the tests), `starter` (5,000 / 20,000 / 100,000), `full` (50,000 / 200,000 / 1,000,000).
Row counts are **exact** (orders include return orders; events include campaign-caused opens/clicks).

## What is in `output/starter` (committed sample)

```
output/starter/
  data/            12 tables as .csv (+ .jsonl, + seed.sql)       <- model inputs
  schema/          schema.sql (DDL with PK/FK), data_dictionary.{md,csv,json}
  ground_truth/    ground_truth.{csv,json}, customer_archetypes.csv, campaign_induced_orders.csv   <- NEVER features
  reports/         validation_summary.{json,md}, evaluation_report.{json,md}, reproducibility_report.json
  manifest.json    seed, scale, generator command, versions, rows + SHA-256 per file
  source_manifest.json   provenance (no external data used)
```

The full-scale dataset is not committed (about 750 MB); regenerate it with `make full`.

## Data model

Core entities: `customers`, `orders`, `order_lines`, `products`, `engagement_events`, `support_cases`, `consents`,
`segment_rules`, `segment_memberships`, `action_catalog`, `action_outcomes` + bridge `action_eligibility`.
ER diagram, cardinalities and lifecycle table: [docs/ER_DIAGRAM.md](docs/ER_DIAGRAM.md).
Field-level data dictionary (type, required, allowed values, units, uniqueness, validation rule): [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).
Business rules, suppression logic and assumptions: [docs/DESIGN.md](docs/DESIGN.md).

| Convention | Example | Where |
|---|---|---|
| `CUS-000001` | `CUS-000042` | `customers` (sequential by sign-up date) |
| `ORD-2026-000001` | `ORD-2025-004711` | `orders` (year of order date, per-year counter, chronological); lines are `<order_id>-L01` |
| `SEG-2026-000001` | `SEG-2026-000003` | `segment_rules` |
| `MEM-<segment-id>-<customer-id>` | `MEM-SEG-2026-000003-CUS-000042` | `segment_memberships` |
| `ACTN-2026-000001` | `ACTN-2026-000001` | `action_outcomes` (one per recommended action instance, chronological) |

IDs are zero-padded and immutable; referenced IDs are byte-identical across files. IDs the brief does not specify follow the
same pattern (`PRD-`, `EVT-`, `SUP-`, `CON-`, `CAT-`, `ELG-`). See *Assumptions* below for the `ACTN` interpretation.

## How the brief's rules are met

| Requirement | Implementation |
|---|---|
| Master data first, valid parent IDs | products / customers / consents -> orders -> lines -> cases -> memberships -> outcomes -> events; every FK validated (CSV checks **and** SQLite `PRAGMA foreign_key_check`) |
| Customers + transaction history, new / dormant / returns / high-service / consent withdrawal | 10 hidden archetypes; coverage counts are in `reports/validation_summary.md` (e.g. full: 4,483 new, 15,208 dormant, 15,000 return orders, 1,060 high-service-cost accounts, 9,509 consent withdrawals) |
| Consent and contact preferences | `consents` is an effective-dated history (SCD2); `customers.preferred_channel` + `contact_frequency_cap_30d` are preferences, not consent |
| RFM / business-defined segments | `segment_rules`: 7 exclusive lifecycle/value segments + 2 overlays, evaluated by one generic engine; thresholds in `config.py` |
| Eligible actions + suppression rules | `action_eligibility` bridge + 4 ordered suppression rules (consent, open case, high-service discount block, frequency cap) |
| Log recommendations and outcomes | `action_outcomes` records SEND, SUPPRESS (with reason) and HOLDOUT rows with `recommended_at`, `sent_at`, delivery, success and attributed order |
| State changes as records, no overwriting | consent withdrawals add rows and close the previous one; returns are RETURN orders with negative lines; memberships are closed (`effective_to`), never deleted |
| Outcomes from documented rules | response probability model + 14-day attribution evaluated from `orders` (`docs/DESIGN.md` section 3) |
| Derived values computed from sources | line -> order totals, customer lifetime fields, RFM metrics, case cost, success flags are all *re-derived* by the validator |
| Edge cases labelled, separate ground truth | `ground_truth/` (four anomaly types, 0.3% of customers each); data tables carry no labels |
| Validation (PK, FK, enums, dates, arithmetic, lifecycle, duplicates) | `validate.py`: 79 checks, summary in `reports/` |
| Exports | CSV, JSONL, SQL (`schema.sql` + `seed.sql`), data dictionary, README, manifest, validation summary |
| Reproducibility | `scripts/reproduce.py` regenerates twice and compares row counts + SHA-256 (all files identical at starter and full scale) |
| Lawful provenance | no external data; see `source_manifest.json` |

## Segmentation and recommendations in one picture

```
orders (sale + return) --+                                   +--> SegmentRule (versioned, declarative)
support cases -----------+--> RFM + service + return metrics --+--> SegmentMembership (metrics + reason, effective-dated)
                                                                    |
                          action_eligibility (segment -> actions) --+--> recommendation
                                                                    |
consents --> channel + consent --> open case? --> high-service discount block? --> frequency cap? --> 10% holdout
                                                                    |
                                                    ActionOutcome (SUPPRESS / HOLDOUT / SEND) --> evaluation vs orders / cases
```

RFM bands: recency <=30/60/120/240 days -> 5/4/3/2 (else 1); frequency 1/2/3-4/5-7/8+ -> 1..5; net spend (returns netted)
<50/<150/<400/<1000 -> 1..4 (else 5). Example membership reason: `CHAMPIONS (priority 3, rule v1): R=5 F=5 M=4`.

## Results at full scale (seed 20261022)

| Table | Rows |
|---|---|
| customers | 50,000 |
| orders (sale + return) | 200,000 (15,000 returns) |
| order_lines | 390,124 |
| engagement_events | 1,000,000 |
| support_cases | 60,000 |
| consents | 149,412 |
| segment_memberships | 79,511 |
| action_outcomes | 49,817 (31,440 SEND, 15,204 SUPPRESS, 3,173 HOLDOUT) |

* **Validation:** 79 / 79 checks pass; all 600 injected anomalies (150 per type) are found and nothing else is flagged.
* **Uplift (ORDER_14D actions, delivered vs holdout):** 14.01% vs 9.83% conversion, lift +4.18 pp (95% CI +3.07 to +5.29 pp).
  Best: VIP early access +11.1 pp, welcome series +9.4 pp. Reactivation discount shows near-zero lift and negative incremental revenue per recipient (20% discount).
* **Reproducibility:** two runs with the same seed give identical row counts and SHA-256 for every file.
* Full details: `output/starter/reports/` (starter) or the same folder after `make full`.

## Validation and ground truth

`python -m segmentation_gen validate --dir <dir>` runs 79 checks from the exported files only:
PK uniqueness, FK integrity (also via SQLite), enums / types / required / patterns, date ordering, arithmetic reconciliation
(lines -> orders -> customers -> memberships -> outcomes), lifecycle transitions (consent chains, return parents, membership dating),
rule conformance (re-running the rule engine), contact constraints (consent, channel, frequency cap, open case, discount block),
outcome re-evaluation, and duplicates. A check passes when what it flags equals the *labelled* injected rows of its type.

| Injected `issue_type` | Caught by |
|---|---|
| `CONFLICTING_EXCLUSIVE_SEGMENT` | `RULES.exclusive_group_single`, `RULES.membership_follows_rules` |
| `ACTION_VIOLATES_CONSENT` | `CONTACT.consent_enforced` |
| `RETURN_NOT_NETTED` | `MATH.membership_net_spend`, `MATH.membership_metrics` |
| `OUTCOME_WRONG_ACTION` | `ACTION.eligible_for_segment` |

`ground_truth/` is consulted only *after* the checks, to score detection. It must not be used as model input
(`customer_archetypes.csv` holds the hidden generator state; `campaign_induced_orders.csv` the simulated causal effect).

## Tests

```bash
make test        # or: python -m unittest discover -s tests -v   (pytest also works)
```

22 tests: scoring-band boundaries (30/31, 60/61, ...), rule priority and exclusivity, return netting, no look-ahead,
ID conventions and zero-padding, exact volumes, determinism (same seed identical bytes, different seed differs),
no ground-truth leakage into data tables, anomaly labelling, full validation pass with every injected anomaly detected, contact
constraints outside injected rows, scenario coverage, and a **negative test** that corrupts files and confirms the validator fails.

## Assumptions and design choices

* **`ACTN-2026-000001` = one action instance (recommendation + outcome)** because it carries a year and a running counter; catalogue entries are `CAT-0001`. If you read it as the catalogue key instead, swap the two ID prefixes in `actions.py` / `schema.py`.
* Two snapshots (2026-08-01, 2026-10-23) give real effective-dated membership history; one campaign wave runs on snapshot 1 and outcomes are evaluated at snapshot 2.
* Outcome flags for `CASE_FREE_30D` and `NO_RETURN_30D` measure results without a simulated causal effect (and no holdout); only `ORDER_14D` actions have lift.
* Currency USD, timestamps naive UTC, customers carry no personal data.
* Verified on pandas 3.0 / numpy 2.4; pandas >= 2.2 is declared but was not tested here.

## Repository layout

```
src/segmentation_gen/  config.py (rules, enums) | schema.py (data dictionary, DDL) | entities.py | segmentation.py (RFM + rule engine)
                       actions.py (catalogue, suppression, outcome evaluation) | anomalies.py | generate.py | export.py
                       validate.py | evaluate.py | __main__.py (CLI)
scripts/               reproduce.py, export_schema_docs.py
tests/                 test_segmentation.py, test_pipeline.py
docs/                  DESIGN.md, ER_DIAGRAM.md, DATA_DICTIONARY.md
output/starter/        committed sample dataset with reports
```

Licence: MIT (code and synthetic data).
