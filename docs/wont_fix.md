# Known Limitations — Won't Fix

These are known edge cases where the system behaves imperfectly. Each entry explains
the root cause and why a general fix is not implemented.

## 1. Per-campaign percentage breakdown is uncitable

**Query type**: "What percentage of spend went to Campaign X?"

**What happens**: The model computes X/total in prose and the validator strips it
as an uncited number.

**Root cause**: `query_metric(ad_spend, group_by=campaign)` returns per-campaign
absolute spend with per-campaign provenance IDs. The *percentage* is a derived
ratio that has no row in the warehouse and therefore no provenance ID.

**Why not fixed**: Storing derived ratios would require a new metric type that
stores computed values — significant schema and ingestion work. The absolute spend
per campaign IS citeable and answers the same business question.

## 2. Contribution margin sum total is uncitable

**Query type**: "What is the total contribution margin across all orders?"

**What happens**: The `contribution_margin` metric returns per-ORDER rows (one per
order_number). Summing them in prose produces an uncited number.

**Root cause**: The metric is designed at order grain. There is no aggregate CM
row in the warehouse, so there is no provenance ID for the sum.

**Why not fixed**: An aggregate CM metric could be added, but it raises ambiguity
(which cost components are included). The per-order view is more actionable for
diagnosing margin leaks. Workaround: use the `sql` tool with a SUM aggregate and
JOIN to provenance.

## 3. Refund rate percentage is uncitable

**Query type**: "What percentage of orders were refunded?"

**What happens**: The model computes refunded_order_count / total_order_count in
prose and the validator strips it.

**Root cause**: The `refunds` metric returns a count and gross amount; the
`orders` metric returns total order count. Their ratio is a derived value with no
direct provenance ID.

**Why not fixed**: Storing refund rates would require a new metric. Low priority
because the absolute `refunds` count is citeable and answers the business question
directly. Workaround: use the `sql` tool with a subquery returning both counts and
join to provenance.

## 4. Order numbers in "not found" responses

**Query type**: "What is the contribution margin for order 9999?" (non-existent)

**What happens**: The validator may flag the order number (e.g. "9999") as an
uncited number if the model echoes it numerically.

**Root cause**: Order numbers are identifiers, not metrics, but the validator's
bare-number scanner does not distinguish identifiers from values. Rule 10 in the
system prompt instructs the model to write them as text, not cite tags.

**Why not fixed**: Teaching the validator to distinguish numeric identifiers from
metric values requires either a whitelist (too brittle) or semantic parsing (too
expensive). The system-prompt rule handles the common case; edge cases where the
model deviates will show as *(uncited)* which is an honest signal.

## 5. Overlapping-window comparison ambiguity

**Query type**: "What's our growth over the last 30d vs 90d?"

**What happens**: The 90d window contains the 30d window entirely, so delta and
pct_change from `compare` are not interpretable as "growth".

**Root cause**: `compare` has no notion of overlapping vs adjacent periods.

**Why not fixed**: Properly detecting overlapping rolling windows requires a
redesign of the compare tool's semantics. The catalog doesn't support adjacent
calendar periods, so there is no clean alternative to offer.

## 6. Campaign attribution (which campaign drove which order)

**Query type**: "Which campaign drove order 1031?"

**What happens**: No data connects orders to campaigns.

**Root cause**: No UTM, click-through, or attribution data is ingested. Shopify
orders in the seed payload carry no `referring_campaign` field.

**Why not fixed**: Requires an attribution source (e.g. Meta CAPI click-id
matching) not present in any of the three connectors.

## 7. Break-even / unit-economics analysis

**Query type**: "Are we profitable on campaign X?"

**What happens**: Cannot compute — no COGS data.

**Root cause**: `contribution_margin` only subtracts shipping and RTO. Product
cost is not in Shopify, Meta, or Shiprocket feeds.

**Why not fixed**: COGS would need to be supplied as a separate data source and
joined at order or SKU level.

## 8. Links table not yet consumed by metric queries

identity.py writes cross-source entity links (Shopify order ↔ Shiprocket shipment at conf 1.0; Meta campaign ↔ Shopify order at conf 0.6) to the `links` table. The metric catalog currently re-implements these joins inline via JSONB attribute equality — it does not query the `links` table or filter by confidence score.

Why not fixed: wiring links into the SQL catalog means rewriting multi-join CTEs in contribution_margin and roas, where the discount-code attribution (conf 0.6) would need a configurable confidence threshold. This is v0.2 scope — the per-query JSONB equality is deterministically correct for the demo merchant; the confidence threshold matters at scale when many low-confidence links compete.

Impact: low for v0 (single merchant, clean data). At 10k merchants with fuzzy identity, queries without confidence filtering will silently over-count attributed revenue.

## 9. Indian-format numbers in bare_number_re

**Query type**: Model writes bare "₹1,01,472" outside a cite tag.

**What happens**: The lakhs-format comma pattern `1,01,472` doesn't match the
Western `\d{1,3}(?:,\d{3})+` branch of `bare_number_re`. The sub-sequences may
partially survive or be stripped depending on their length.

**Root cause**: Validator's comma-number regex assumes Western grouping.

**Why not fixed**: Low impact — model writes numbers inside cite tags where the
format is irrelevant. Observed failures in testing were all inside cite tags.
Revisit if adversarial testing shows uncited Indian-format leakage in the wild.
