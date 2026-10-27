# Data dictionary

Generated from `src/segmentation_gen/schema.py` (single source of truth). Types: `string`, `int`, `decimal` (2 dp unless stated), `date` (ISO 8601), `datetime` (`YYYY-MM-DD HH:MM:SS`, UTC, naive), `bool` (0/1). Money is in USD.

## customers (entity: Customer)

One row per customer or prospect. Behavioural / lifecycle columns are derived from source records as of snapshot 2 (2026-10-23).

**Primary key:** `customer_id`  
**Cardinality / relationships:**

- Customer 1:N Order
- Customer 1:N EngagementEvent
- Customer 1:N SupportCase
- Customer 1:N Consent
- Customer N:M SegmentRule via SegmentMembership
- Customer 1:N ActionOutcome

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `customer_id` | string | Y | `^CUS-\d{6}$` |  | yes |  |  | Immutable; sequential by sign-up date |
| `signup_date` | date | Y |  |  |  |  |  | 2024-10-01 <= signup_date <= 2026-10-22 |
| `region` | string | Y | NA|EU|APAC|LATAM|MEA |  |  |  |  |  |
| `acquisition_channel` | string | Y | ORGANIC_SEARCH|PAID_SOCIAL|REFERRAL|EMAIL_SIGNUP|PARTNER |  |  |  |  |  |
| `age_band` | string | N | 18-24|25-34|35-44|45-54|55+ |  |  |  |  | ~8% missing (unknown) |
| `preferred_channel` | string | Y | EMAIL|SMS|PUSH|PHONE |  |  |  |  | Contact preference; does NOT imply consent |
| `contact_frequency_cap_30d` | int | Y | 1|2|3 | contacts / 30 days |  |  |  | Max SEND decisions per customer per 30 days |
| `customer_type` | string | Y | CUSTOMER|PROSPECT |  |  |  | Y | PROSPECT iff lifetime_order_count = 0 |
| `lifecycle_state` | string | Y | PROSPECT|NEW|ACTIVE|AT_RISK|DORMANT |  |  |  | Y | PROSPECT: 0 sales; NEW: first sale <= 60d before snapshot2; ACTIVE: last sale <= 90d; AT_RISK: 91-180d; DORMANT: >180d |
| `first_order_date` | date | N |  |  |  |  | Y | min(order_date) of SALE orders |
| `last_order_date` | date | N |  |  |  |  | Y | max(order_date) of SALE orders |
| `lifetime_order_count` | int | Y |  |  |  |  | Y | count of SALE orders; >= 0 |
| `lifetime_net_spend` | decimal | Y |  | USD |  |  | Y | sum(orders.net_amount) over SALE + RETURN orders (returns netted) |

## products (entity: Product)

Product master with a 2-level taxonomy (category > subcategory).

**Primary key:** `product_id`  
**Cardinality / relationships:**

- Product 1:N OrderLine
- Product 1:N EngagementEvent (optional)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `product_id` | string | Y | `^PRD-\d{6}$` |  | yes |  |  |  |
| `sku` | string | Y | `^SKU-[A-Z]{3}-\d{5}$` |  | yes |  |  |  |
| `product_name` | string | Y |  |  |  |  |  |  |
| `category` | string | Y |  |  |  |  |  | 8 top-level categories |
| `subcategory` | string | Y |  |  |  |  |  |  |
| `list_price` | decimal | Y |  | USD |  |  |  | > 0 |
| `unit_cost` | decimal | Y |  | USD |  |  |  | 0 < unit_cost < list_price |
| `status` | string | Y | ACTIVE|DISCONTINUED |  |  |  |  |  |
| `launch_date` | date | Y |  |  |  |  |  |  |
| `discontinued_date` | date | N |  |  |  |  |  | Required iff status = DISCONTINUED; no order line after this date |

## orders (entity: Order)

Order header. SALE rows are purchases; RETURN rows are explicit return events linked to their SALE via parent_order_id (history is never overwritten; spend is netted by summing both).

**Primary key:** `order_id`  
**Cardinality / relationships:**

- Customer 1:N Order
- Order 1:N OrderLine
- SALE Order 1:N RETURN Order (optional, parent_order_id)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `order_id` | string | Y | `^ORD-\d{4}-\d{6}$` |  | yes |  |  | ORD-<year of order_date>-<per-year counter>; IDs assigned in chronological order |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `order_date` | date | Y |  |  |  |  |  | >= customer.signup_date; <= 2026-10-22 |
| `order_type` | string | Y | SALE|RETURN |  |  |  |  |  |
| `parent_order_id` | string | N |  |  |  | orders.order_id |  | Required iff RETURN; parent must be a SALE of the same customer dated before the return |
| `order_channel` | string | Y | WEB|APP|STORE |  |  |  |  |  |
| `gross_amount` | decimal | Y |  | USD |  |  | Y | sum(quantity * unit_price) of lines |
| `discount_amount` | decimal | Y |  | USD |  |  | Y | gross_amount - net_amount |
| `net_amount` | decimal | Y |  | USD |  |  | Y | sum(line_amount); >0 for SALE, <0 for RETURN |

## order_lines (entity: OrderLine)

Bridge table resolving Order N:M Product. Return lines carry negative quantity / amount.

**Primary key:** `line_id`  
**Cardinality / relationships:**

- Order N:M Product via OrderLine
- SALE line 1:N RETURN line (optional, parent_line_id)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `line_id` | string | Y | `^ORD-\d{4}-\d{6}-L\d{2}$` |  | yes |  |  | <order_id>-L<line_number zero-padded to 2> |
| `order_id` | string | Y |  |  |  | orders.order_id |  |  |
| `line_number` | int | Y |  |  |  |  |  | >= 1; unique within order |
| `product_id` | string | Y |  |  |  | products.product_id |  | Product must be launched, and not discontinued, on order_date |
| `quantity` | int | Y |  | units |  |  |  | != 0; >0 for SALE, <0 for RETURN; |return qty| <= parent qty |
| `unit_price` | decimal | Y |  | USD |  |  |  | = products.list_price |
| `discount_pct` | decimal | Y |  | % |  |  |  | 0 <= x <= 50 |
| `line_amount` | decimal | Y |  | USD |  |  | Y | round(quantity * unit_price * (1 - discount_pct/100), 2) |
| `parent_line_id` | string | N |  |  |  | order_lines.line_id |  | Required iff the order is a RETURN |
| `return_reason` | string | N | DEFECTIVE|NOT_AS_DESCRIBED|WRONG_SIZE_FIT|CHANGED_MIND|LATE_DELIVERY |  |  |  |  | Required iff RETURN line |

## engagement_events (entity: EngagementEvent)

Digital / messaging engagement events. Events caused by a campaign action reference action_id.

**Primary key:** `event_id`  
**Cardinality / relationships:**

- Customer 1:N EngagementEvent
- ActionOutcome 1:N EngagementEvent (optional)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `event_id` | string | Y | `^EVT-\d{8}$` |  | yes |  |  | Chronological counter |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `event_ts` | datetime | Y |  |  |  |  |  | >= signup_date 00:00:00; <= 2026-10-22 23:59:59 |
| `event_type` | string | Y | SITE_VISIT|APP_OPEN|PRODUCT_VIEW|SEARCH|CART_ADD|WISHLIST_ADD|EMAIL_OPEN|EMAIL_CLICK|PUSH_OPEN|SMS_CLICK |  |  |  |  |  |
| `channel` | string | Y | WEB|APP|EMAIL|SMS|PUSH |  |  |  |  |  |
| `product_id` | string | N |  |  |  | products.product_id |  | Populated only for PRODUCT_VIEW / CART_ADD / WISHLIST_ADD |
| `action_id` | string | N |  |  |  | action_outcomes.action_id |  | Populated for opens/clicks caused by a SEND action; event_ts >= action sent_at |

## support_cases (entity: SupportCase)

Service cases. cost_amount is derived from handling time (agent cost) and escalation.

**Primary key:** `case_id`  
**Cardinality / relationships:**

- Customer 1:N SupportCase
- Order 1:N SupportCase (optional)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `case_id` | string | Y | `^SUP-\d{6}$` |  | yes |  |  |  |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `order_id` | string | N |  |  |  | orders.order_id |  | If present: same customer, order_date <= opened_at date |
| `opened_at` | datetime | Y |  |  |  |  |  |  |
| `closed_at` | datetime | N |  |  |  |  |  | Required iff status = CLOSED; >= opened_at |
| `category` | string | Y | DELIVERY_DELAY|PRODUCT_DEFECT|BILLING|RETURN_REFUND|WRONG_ITEM|ACCOUNT|WEBSITE_ISSUE |  |  |  |  |  |
| `priority` | string | Y | LOW|MEDIUM|HIGH |  |  |  |  |  |
| `status` | string | Y | OPEN|CLOSED |  |  |  |  | OPEN iff closed_at is null |
| `escalated` | bool | Y |  |  |  |  |  | Only HIGH priority cases can be escalated |
| `handling_minutes` | int | Y |  | minutes |  |  |  | >= 5 |
| `cost_amount` | decimal | Y |  | USD |  |  | Y | round(handling_minutes/60 * 24.00 + 15.00 * escalated, 2) |

## consents (entity: Consent)

Consent history as effective-dated rows (SCD type 2). A withdrawal closes the previous row and adds a new WITHDRAWN row; nothing is overwritten. No row for a customer/channel/purpose means no consent.

**Primary key:** `consent_id`  
**Cardinality / relationships:**

- Customer 1:N Consent
- Customer N:M (channel, purpose) over time

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `consent_id` | string | Y | `^CON-\d{7}$` |  | yes |  |  |  |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `channel` | string | Y | EMAIL|SMS|PUSH|PHONE |  |  |  |  |  |
| `purpose` | string | Y | MARKETING|SERVICE |  |  |  |  |  |
| `status` | string | Y | GRANTED|WITHDRAWN |  |  |  |  | Statuses alternate GRANTED/WITHDRAWN within customer+channel+purpose, starting with GRANTED |
| `effective_from` | date | Y |  |  |  |  |  | >= signup_date |
| `effective_to` | date | N |  |  |  |  |  | Exclusive end; equals next row's effective_from; null on the current row |
| `source` | string | Y | WEB_FORM|CHECKOUT|APP|PREFERENCE_CENTER|UNSUBSCRIBE_LINK|SUPPORT_AGENT |  |  |  |  |  |

## segment_rules (entity: SegmentRule)

Declarative, versioned segment definitions. Exclusive-group rules are evaluated by ascending priority (first match wins); OVERLAY rules are independent flags. NULL bound = not applied.

**Primary key:** `segment_id`  
**Cardinality / relationships:**

- SegmentRule N:M Customer via SegmentMembership
- SegmentRule N:M ActionCatalog via ActionEligibility

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `segment_id` | string | Y | `^SEG-2026-\d{6}$` |  | yes |  |  |  |
| `segment_code` | string | Y |  |  | yes |  |  |  |
| `segment_name` | string | Y |  |  |  |  |  |  |
| `description` | string | Y |  |  |  |  |  |  |
| `rule_type` | string | Y | RFM|OVERLAY |  |  |  |  |  |
| `exclusive_group` | string | Y | LIFECYCLE_VALUE|NONE |  |  |  |  | Customers may hold at most ONE active membership per exclusive group (NONE = overlay) |
| `priority` | int | Y |  |  |  |  |  | Lower number evaluated first |
| `min_r` | int | N |  |  |  |  |  | 0..5 |
| `max_r` | int | N |  |  |  |  |  | 0..5 |
| `min_f` | int | N |  |  |  |  |  | 0..5 |
| `max_f` | int | N |  |  |  |  |  | 0..5 |
| `min_m` | int | N |  |  |  |  |  | 0..5 |
| `max_m` | int | N |  |  |  |  |  | 0..5 |
| `max_days_since_first_order` | int | N |  | days |  |  |  |  |
| `min_cases_180d` | int | N |  | cases |  |  |  |  |
| `min_service_cost_ratio` | decimal | N |  |  |  |  |  | service_cost_180d / max(net_spend, 1) |
| `min_return_rate` | decimal | N |  |  |  |  |  | returned_amount / gross_spend |
| `min_sale_orders` | int | N |  | orders |  |  |  |  |
| `version` | int | Y |  |  |  |  |  |  |
| `status` | string | Y | ACTIVE|RETIRED |  |  |  |  |  |
| `effective_from` | date | Y |  |  |  |  |  |  |
| `effective_to` | date | N |  |  |  |  |  |  |

## segment_memberships (entity: SegmentMembership)

Bridge Customer N:M SegmentRule with effective dating. A row is opened when a customer enters a segment at a snapshot and closed when they leave. Metrics are those at effective_from (explainability).

**Primary key:** `membership_id`  
**Cardinality / relationships:**

- Customer N:M SegmentRule via SegmentMembership
- SegmentMembership 1:N ActionOutcome

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `membership_id` | string | Y | `^MEM-SEG-2026-\d{6}-CUS-\d{6}$` |  | yes |  |  | MEM-<segment_id>-<customer_id> |
| `segment_id` | string | Y |  |  |  | segment_rules.segment_id |  |  |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `effective_from` | date | Y | 2026-08-01|2026-10-23 |  |  |  |  | Snapshot date |
| `effective_to` | date | N |  |  |  |  |  | > effective_from; null when current |
| `is_current` | bool | Y |  |  |  |  | Y | 1 iff effective_to is null |
| `recency_days` | int | N |  | days |  |  |  | snapshot - last SALE date; null if no sale |
| `frequency` | int | Y |  |  |  |  |  | count SALE orders dated < snapshot |
| `gross_spend` | decimal | Y |  | USD |  |  |  | sum SALE net_amount dated < snapshot |
| `returned_amount` | decimal | Y |  | USD |  |  |  | -sum RETURN net_amount dated < snapshot |
| `net_spend` | decimal | Y |  | USD |  |  | Y | gross_spend - returned_amount |
| `r_score` | int | Y |  |  |  |  |  | 0 (no sale) or 1..5 per RECENCY_CUTS |
| `f_score` | int | Y |  |  |  |  |  | 0 (no sale) or 1..5 per FREQUENCY_CUTS |
| `m_score` | int | Y |  |  |  |  |  | 0 (no sale) or 1..5 per MONETARY_CUTS applied to net_spend |
| `days_since_first_order` | int | N |  | days |  |  |  |  |
| `cases_180d` | int | Y |  |  |  |  |  | cases opened in [snapshot-180d, snapshot) |
| `service_cost_180d` | decimal | Y |  | USD |  |  |  |  |
| `service_cost_ratio` | decimal | Y |  |  |  |  |  | service_cost_180d / max(net_spend, 1) |
| `return_rate` | decimal | Y |  |  |  |  |  | returned_amount / gross_spend (0 if no spend) |
| `rule_version` | int | Y |  |  |  |  |  |  |
| `assignment_reason` | string | Y |  |  |  |  |  | Human-readable explanation of why the rule matched |

## action_catalog (entity: ActionCatalog)

Catalogue of actions the business may take, with channel, consent purpose and suppression attributes.

**Primary key:** `catalog_id`  
**Cardinality / relationships:**

- ActionCatalog N:M SegmentRule via ActionEligibility
- ActionCatalog 1:N ActionOutcome

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `catalog_id` | string | Y | `^CAT-\d{4}$` |  | yes |  |  |  |
| `action_code` | string | Y |  |  | yes |  |  |  |
| `action_name` | string | Y |  |  |  |  |  |  |
| `action_type` | string | Y | ONBOARDING|RETENTION|WINBACK|UPSELL|SERVICE|EDUCATION|ACQUISITION |  |  |  |  |  |
| `purpose` | string | Y | MARKETING|SERVICE |  |  |  |  | Consent purpose required to contact |
| `allowed_channels` | string | Y |  |  |  |  |  | Pipe-separated subset of EMAIL|SMS|PUSH|PHONE |
| `is_discount` | bool | Y |  |  |  |  |  | Discount actions are blocked for HIGH_SERVICE_COST accounts |
| `discount_pct` | decimal | N |  | % |  |  |  |  |
| `success_metric` | string | Y | ORDER_14D|CASE_FREE_30D|NO_RETURN_30D |  |  |  |  |  |
| `attribution_window_days` | int | Y |  | days |  |  |  |  |
| `requires_holdout` | bool | Y |  |  |  |  |  | 10% of otherwise-eligible customers are held out as control |
| `cost_per_contact` | decimal | Y |  | USD |  |  |  |  |
| `status` | string | Y | ACTIVE|RETIRED |  |  |  |  |  |
| `effective_from` | date | Y |  |  |  |  |  |  |
| `effective_to` | date | N |  |  |  |  |  |  |

## action_eligibility (entity: ActionEligibility)

Bridge SegmentRule N:M ActionCatalog. Lowest priority number is recommended first.

**Primary key:** `eligibility_id`  
**Cardinality / relationships:**

- SegmentRule N:M ActionCatalog via ActionEligibility

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `eligibility_id` | string | Y | `^ELG-\d{4}$` |  | yes |  |  |  |
| `segment_id` | string | Y |  |  |  | segment_rules.segment_id |  |  |
| `catalog_id` | string | Y |  |  |  | action_catalog.catalog_id |  |  |
| `priority` | int | Y |  |  |  |  |  | >= 1; (segment_id, priority) unique |
| `effective_from` | date | Y |  |  |  |  |  |  |
| `effective_to` | date | N |  |  |  |  |  |  |

## action_outcomes (entity: ActionOutcome)

One row per recommended action instance: the recommendation, the suppression / send decision and the evaluated outcome. Suppressed and held-out recommendations are logged too.

**Primary key:** `action_id`  
**Cardinality / relationships:**

- Customer 1:N ActionOutcome
- SegmentMembership 1:N ActionOutcome
- ActionCatalog 1:N ActionOutcome
- ActionOutcome 1:N EngagementEvent (optional)
- ActionOutcome 0..1:1 Order (attributed_order_id)

| Field | Type | Req. | Allowed values / pattern | Unit | Unique | FK | Derived | Validation rule |
|---|---|---|---|---|---|---|---|---|
| `action_id` | string | Y | `^ACTN-2026-\d{6}$` |  | yes |  |  | ACTN-<year>-<counter>; chronological |
| `customer_id` | string | Y |  |  |  | customers.customer_id |  |  |
| `membership_id` | string | Y |  |  |  | segment_memberships.membership_id |  | Membership (at recommendation time) that triggered the recommendation |
| `catalog_id` | string | Y |  |  |  | action_catalog.catalog_id |  | Must be eligible for the membership's segment (action_eligibility) |
| `recommended_at` | datetime | Y |  |  |  |  |  |  |
| `decision` | string | Y | SEND|SUPPRESS|HOLDOUT |  |  |  |  |  |
| `suppression_reason` | string | N | NO_CONSENT|CONSENT_WITHDRAWN|OPEN_SUPPORT_CASE|HIGH_SERVICE_COST_DISCOUNT_BLOCK|FREQUENCY_CAP |  |  |  |  | Required iff decision = SUPPRESS |
| `channel` | string | N | EMAIL|SMS|PUSH|PHONE |  |  |  |  | Required for SEND / HOLDOUT; must be in catalog.allowed_channels and consented |
| `sent_at` | datetime | N |  |  |  |  |  | Required iff SEND; >= recommended_at |
| `delivery_status` | string | Y | DELIVERED|FAILED|NOT_APPLICABLE |  |  |  |  |  |
| `success_metric` | string | Y | ORDER_14D|CASE_FREE_30D|NO_RETURN_30D |  |  |  |  | = catalog.success_metric |
| `success_flag` | bool | N |  |  |  |  |  | ORDER_14D: sale within 14d of sent_at (or recommended_at for HOLDOUT); CASE_FREE_30D: no case opened within 30d; NO_RETURN_30D: no return within 30d. Null when suppressed / delivery failed |
| `attributed_order_id` | string | N |  |  |  | orders.order_id |  | ORDER_14D only: first SALE order inside the attribution window |
| `attributed_revenue` | decimal | N |  | USD |  |  | Y | = attributed order net_amount |
| `outcome_status` | string | Y | SUPPRESSED|HOLDOUT|DELIVERY_FAILED|NO_RESPONSE|ENGAGED|SUCCESS |  |  |  |  |  |
| `evaluated_at` | date | Y |  |  |  |  |  | Date outcomes were evaluated (2026-10-23) |
