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

## 9. Cursor stalling for Meta campaigns + Shiprocket shipments

**What happens**: The runner extracts the cursor from `updated_at`, `date_stop`, or `created_at` on each record. These fields are absent from Meta campaign objects and Shiprocket shipment objects. As a result, no cursor advances and every run re-fetches all records for those resources.

**Root cause**: The runner uses a single cursor-extraction strategy across all resources; it does not know which field to use per resource type.

**Why not fixed**: Idempotent — upsert on `source_record_id` means re-fetching produces no corruption or duplicates. Acceptable at demo scale. Production fix: add a resource-specific cursor field map to runner config (e.g. `{"meta/campaigns": "updated_time", "shiprocket/shipments": "created_at"}`).

## 10. Stale vendor API versions

**What happens**: Shopify `2024-01` and Meta `v20.0` are past their support windows. Field names or response shapes may have changed in current API versions.

**Root cause**: API version constants are hardcoded in each connector.

**Why not fixed**: Verifying and migrating to current versions requires live API access to test against production responses. This cannot be done safely in a demo environment without credentials scoped to the new versions.

## 11. Indian lakh-format numbers (₹1,23,456) partially escape bare-number scan

**What happens**: The bare-number regex requires comma groups of exactly 3 digits (`\d{1,3}(?:,\d{3})+`). Indian lakh format uses 2-digit groups after the first comma (e.g. `1,23,456`). Numbers in this format may not be detected as bare numbers and could survive the validator unstripped.

**Root cause**: Validator's comma-number regex assumes Western grouping exclusively.

**Why not fixed**: Affects only numbers ≥ ₹1,00,000 (one lakh). Demo D2C revenue in the seed data is well below that threshold so the gap has no observable impact in testing. Fix requires extending the regex to cover the Indian grouping pattern.

## 12. LLM call budget is 36 (3 attempts × 12 turns), not 12

**What happens**: The "12-turn max" cited in the README is the per-attempt limit. The agent makes up to 3 attempts before cascading, so the worst-case LLM call count is 36 turns per query, not 12. With a cascade fallback, the absolute ceiling is 72.

**Root cause**: README simplifies "12-turn max" without qualifying that it is per-attempt.

**Why not fixed**: The per-attempt framing is accurate for the common case and is the operationally meaningful limit (each attempt resets context). The aggregate ceiling is documented here. README wording is intentionally simplified for readability.

## 13. `compare` tool synthetic provenance IDs not resolvable by `get_raw`

The `compare(metric, period_a, period_b)` tool generates synthetic IDs like `computed:metric:delta:AvsB` and `computed:metric:pct_change:AvsB`. These appear in `provenance_ids` and are accepted by the validator, but `get_raw(provenance_id)` cannot resolve them (no raw row exists). The "answer → citation → original JSON" round-trip breaks for delta/percent-change values. Fix: a `compute()` tool that generates a `computed:` provenance record stored in the warehouse and resolvable by `get_raw`. Scope: v0.2 (requires new provenance table row type).

## 14. `rto_rate` metric denominator is all-time, not time-windowed

`query_metric(rto_rate, time_range="30d")` returns `RTOs(last 30d) / shipments(all time)`. The denominator counts all shipments ever ingested regardless of the query time window, because filtering shipment entities by first-seen would exclude shipments that had RTOs within the window but were first seen earlier. A "30d RTO rate" computed this way understates the true rate for growing merchants. Fix: add a time-windowed denominator option, or recalculate denominator as "shipments whose delivery window overlaps the query period." Scope: requires rethinking the shipment lifecycle model (v0.2).

## 15. Wall-clock `NOW()` makes README ₹ figures time-bounded

Metric queries use `NOW() - INTERVAL '...'` while seed data is anchored to BASE_DATE=2026-05-17. README figures (₹37,053 30d revenue, ROAS 1.27x, ₹4,642/month) are accurate only while the rolling window intersects the seeded date range. Past roughly 2026-07-17, the 30d window returns zero seed orders. Fix: a configurable `AS_OF_DATE` parameter in metric queries, or advance BASE_DATE before each submission demo. Scope: v0.2.

## 16. `raw_shopify_products` and `raw_shopify_customers` have no downstream normalizer

The Shopify connector ingests products and customers into `raw_shopify_products` and `raw_shopify_customers`, but no normalizer processes them into `entities`/`events`. The ingestion cost is paid with no analytical benefit. Fix: add normalizers that create product entities (for SKU-level margin) and customer entities (for cohort analysis). Scope: v0.2 (requires new metric catalog entries and identity resolution).

## 17. `d2c_app` may own its tables when running without `.env`

`create_tables()` uses `app/warehouse/db.py:engine`, which is built from `settings.database_url`. The default value of `database_url` in `app/config.py` is `postgresql://d2c_app:d2c_app@localhost:5432/d2c` (the runtime role). If a developer runs without a `.env` file (so no `DATABASE_URL` env var is set), `create_tables()` connects as `d2c_app` and `d2c_app` becomes the table owner. Table owners can ALTER/DROP their own RLS policies even under `NOSUPERUSER NOBYPASSRLS`. Under the documented bootstrap flow (`.env.example` sets `DATABASE_URL=postgresql://d2c:d2c@...`) and in CI, `DATABASE_URL` points to the `d2c` superuser, so tables are created as `d2c` and the ownership concern does not apply. Fix: pass an explicit engine to `create_tables()` rather than using the module-level `engine` singleton so the migration always runs as a designated superuser regardless of env. Scope: migration refactor.

## 18. CI RLS tests run under superuser, not `d2c_app` role

GitHub Actions sets `DATABASE_URL=postgresql://d2c:d2c@...` (superuser). The `d2c_app` role is created by `create_app_role()` called from `seed_demo_merchant.py` (which `make seed` invokes), so the role does exist in CI. However, all test sessions connect via the superuser `DATABASE_URL`, not as `d2c_app`. Merchant-isolation tests pass because metric catalog SQL includes explicit `merchant_id` filters, but they do NOT verify that the RLS policy itself would block a query that omits the filter. A regression removing `set_merchant()` would pass CI but break production. Fix: run isolation tests connecting as `d2c_app`. Scope: CI infrastructure change.

## 19. `"last N,NNN"` temporal prefix exempts comma-formatted numbers from bare-number scan

The bare-number validator excludes numbers whose start position falls within a `r'\b(?:last|past|…|next)\s+\d+\b'` span. This span covers the first number group of a comma-formatted number (e.g., "last 30,000" → "30,000" starts inside the "last 30" span and is not stripped). A model response like "the last 30,000 orders showed no RTO" would pass the validator with "30,000" uncited. Fix: extend the temporal regex to also cover comma-formatted numbers (`\s+\d{1,3}(?:,\d{3})*`), or change the exclusion logic to only protect the matching span text, not the whole comma-formatted token. Scope: validator regex refactor.

## 20. `history` field enables prompt-injection via the chat API

`/chat` accepts a `history: list[dict]` field that is spliced directly after the system prompt with no role or content validation. A caller can inject `{"role": "system", ...}` entries to override instructions, or `{"role": "tool", ...}` entries to fabricate tool results with arbitrary provenance IDs. The validator's provenance check only covers IDs collected from actual `dispatch_tool` calls in the current attempt, so injected tool messages with real-looking IDs can survive the check if their content doesn't trigger bare-number detection. Fix: filter history to only `user`/`assistant` roles; validate tool messages against known tool names and schemas. Scope: security hardening in v0.2.

## 21. Streamlit `merchant_id` sidebar input is silently ignored

The sidebar "Merchant ID" text input in `app/ui/streamlit_app.py` sends `merchant_id` in the POST body and as a `/runs?merchant_id=` query param, but `ChatRequest` in `app/api/main.py` has no `merchant_id` field (Pydantic drops extras), and the `/runs` endpoint derives merchant from the `X-API-Key` header, not a query param. The input does nothing. Fix: remove the input or wire it through the API key mapping. Scope: UI/API redesign in v0.2.

## 22. Multi-turn history is stripped of cite tags before storage

`app/ui/streamlit_app.py` stores `data["answer"]` in history, but the API returns the validator-cleaned answer (all `<cite>` tags stripped by `app/chat/validator.py`). The system prompt instructs the model to cite provenance IDs in follow-up turns, but the history it receives has no cite tags to reference. Fix: return both a `raw_answer` (with tags, for history) and a `display_answer` (tag-free, for UI) from the `/chat` endpoint; store `raw_answer` in history. Scope: API response schema change in v0.2.

## 23. `raise_price` proposals target completed historical orders

The raise-price proposal logic in `MarginWatchAgent._execute` (`app/agents/margin_watch.py`) constructs `PUT /admin/api/2024-01/orders/{order_id}.json` as the would-do API call. Shopify's order update endpoint does not support repricing completed orders; the correct action is updating the product variant price for future orders. The proposal's ₹ impact figure (`abs(margin)`) is the historical loss on a past order, not a forward-looking saving. The proposal is directionally correct (identifies which SKUs are underwater) but the action target and impact accounting are wrong. Fix: change the proposal to target the product/variant endpoint and compute impact as (expected future order volume) × (required margin restoration). Scope: requires linking orders to product variants via the product normalizer (not yet implemented, wont_fix #16).

## 24. Shiprocket DESC pagination can silently skip records during concurrent writes

`_paginate_orders` in `app/connectors/shiprocket/connector.py` uses `sort=DESC` (newest first) with offset-based pagination (`page=1, 2, ...`). If new orders are inserted between page fetches, all subsequent pages shift by one record, and the record at the page boundary is silently skipped. Since `from=since` limits to recent orders, this window is small in practice, but a busy merchant with many orders per second could see gaps. Fix: use `sort=ASC` (stable pagination direction) or paginate by last-seen order ID rather than offset. Scope: connector refactor.

## 25. `refunded_order_count` in the refunds metric counts refund events, not distinct orders

`catalog.py` computes `COUNT(DISTINCT ev.entity_id) AS refunded_order_count` for the `refunds` metric. For refund events, `ev.entity_id` is the refund entity (one per refund transaction), not the order entity. An order with two partial refunds counts as 2. The column name implies "distinct orders that were refunded." Fix: join to the `order_number` attribute to count distinct refunded orders. Scope: catalog SQL change.

## 26. `_collect_tool_numbers` in the chat loop includes JSONB identifier strings

`app/chat/loop.py` walks all tool result fields and adds any `float()`-parseable string to `tool_value_set`. This includes numeric-string identifiers like order numbers (`"1031"`), Shopify IDs, and postal codes. A model can then cite a number that matches an identifier (e.g. `<cite ref="prov:X">1031</cite>`) and pass the value-check even if 1031 was never a metric value in the tool results. Fix: only add numbers from fields known to be metric values (e.g., `amount`, `revenue`, `ad_spend`, `rto_rate`), not from JSONB attribute blobs. Scope: `_collect_tool_numbers` refactor.

## 27. `tool_value_set` empty when model answers without tool calls

The citation validator skips numeric value verification when `tool_value_set` is empty (`if not ref_unresolvable and tool_value_set and value.strip()`). If the model produces a cited answer in a turn where it made no tool calls (e.g., answering from conversation history), no value check runs. A model that cites a real provenance ID with a fabricated number passes the validator in this scenario. Fix: treat an empty `tool_value_set` on a turn that contains citations as a validation failure (no tool data to verify against), not a pass. Scope: validator + loop interaction design change.

## 28. Multi-number unverified cite values may get partially double-annotated

When step 1 of `validate_and_clean` marks a cite tag unresolvable, it replaces `<cite ref="X">VALUE</cite>` with `VALUE *(unverified)*`. If VALUE contains multiple numbers (e.g. `"₹31,814 (was ₹99,999)"`), only the last number is immediately followed by `*(unverified)*`. Step 2's bare-number scan checks `cleaned[m.end():m.end()+20]` for `*(unverified)*`; the first number's lookahead sees `(was ₹*(uncited)*) *(un...` — which starts with `)`, not `\s*\*` — and does not match, so it also gets stripped to `*(uncited)*`. The output becomes `₹*(uncited)* (was ₹*(uncited)*) *(unverified)*`. This is cosmetically wrong (double annotation, wrong marker on first number) but not a security issue — both numbers are still marked as unverified/uncited. Fix: track unverified span boundaries during step 1 and add them to excluded_spans in step 2. Scope: validator refactor.

## 29. ingest_cursors RLS policy has no WITH CHECK — write-side unfiltered

The RLS policy added to `ingest_cursors` in `app/warehouse/migrations/create_cursors.py` has only a `USING` clause. In Postgres, `USING` governs SELECT/UPDATE/DELETE row visibility; INSERT rows are governed by `WITH CHECK`. Without `WITH CHECK`, `d2c_app` can insert cursor rows with any `merchant_id` regardless of `app.current_merchant`. In practice the ingest runner is a trusted server-side process that correctly sets the GUC before writing, so this is defense-in-depth rather than an active exploit path. Fix: add `WITH CHECK (merchant_id = current_setting('app.current_merchant', true))` to the policy. Same pattern applies to other tables' RLS policies. Scope: migration refactor; consistent with the rest of the codebase (other tables also lack WITH CHECK).

## 30. Meta Ads purchase count uses first matching attribution window

`app/normalize/meta_to_universal.py` uses `next(a for a in actions if a["action_type"]=="purchase")` which takes the first `purchase` entry from Meta's `actions[]` array. Meta may return multiple `purchase` entries for different attribution windows (e.g., `1d_click`, `7d_click`, `7d_click_1d_view`). The selected window depends on the API's return order, which is not guaranteed. Fix: explicitly select a canonical attribution window (e.g., prefer `7d_click` or the default Meta attribution window) rather than taking the first. Scope: normalizer update; requires an attribution window policy decision.

## 31. ~~Validator value-check uses `any`~~ — FIXED in v0.1.10

`validate_and_clean` now uses `all(any(...) for num in all_nums)` — every number inside a cite's display value must match a tool result. Fixed at `app/chat/validator.py:175-182`. Entry kept for changelog traceability; no longer a known limitation.

## 32. Revenue group_by=date buckets refund events by refund date, not order date

`query_metric(revenue, group_by="date")` uses `DATE(ev.occurred_at)` for all event types. Refund events carry `occurred_at = refund.created_at`, not the original order date. "Revenue on 2026-05-01" nets refunds processed on that date against orders placed on that date — cash-basis recognition. This can produce negative daily revenue and confuse date-anchored trend analysis. Fix: add a bucketing policy parameter (event-date vs order-date) and join refund events to their original order entity to use the order date for refund attribution. Scope: catalog SQL change.

## 33. `set_merchant` absence silently returns zero rows rather than erroring

If a code path forgets to call `set_merchant()` before a query, `current_setting('app.current_merchant', true)` returns `''`, the RLS policy reduces to `merchant_id = ''`, and all queries return zero rows instead of raising an error. Combined with wont_fix #18 (CI uses superuser, doesn't exercise `d2c_app`), a regression that removes a `set_merchant()` call would produce silent empty results in production without any test failure. Fix: add an assertion in `get_db()` or a trigger that raises when `app.current_merchant` is unset. Scope: DB session initialization change.

## 34. `query_metric` `filters` parameter is silently ignored

`app/warehouse/metrics/catalog.py` accepts a `filters: dict | None = None` parameter in the function signature, but the parameter is never substituted into the SQL (`extra_filters` is always `""`). Callers passing filters expecting them to apply get unfiltered results with no error or warning. Fix: implement the filter substitution, or remove the parameter. Scope: catalog SQL update.

## 35. Single-digit numbers (0-9) bypass the bare-number scan

`bare_number_re` in `validate_and_clean` requires `\d{2,}` for plain integers, `\d{1,3}(?:,\d{3})+` for comma-formatted numbers, or `\d+\.\d+` for decimals. Single-digit integers (1-9, including 0) are not matched by any alternative. A model response saying "3 orders had negative margin" without citing the "3" passes validation. Low practical impact: single-digit counts are rarely the sole analytical claim in an answer, and the system prompt instructs the model to use the `query_metric` tool for counts. Fix: change the second alternative to `\b\d+(?:\.\d+)?\b` with a single-digit exemption for time-period references (existing `_timeref_re` handles "7d", "30 days", etc.). Scope: validator regex + regression test.

## 36. loop.py _collect_tool_numbers adds numeric string IDs to value set

**What**: `_collect_tool_numbers` recurses all tool result values and tries `float()` on strings. Pure-digit record IDs (order IDs, customer IDs), year numbers (2026), area codes etc. all become "legitimate" cited values, weakening the validator's value-set guard.

**Why deferred**: Proper fix requires field-level whitelisting of numeric fields per tool/resource type — significant schema coupling. Current validator still catches hallucinated domain-specific values (₹ amounts, percentages) that don't appear anywhere in tool results.

**Production fix**: maintain an explicit `NUMERIC_FIELDS` allowlist per tool schema; only add values from those fields to the value set.

## 37. get_raw results pollute tool_value_set

**What**: When the model calls `get_raw`, the entire raw payload JSON is scanned for floatable values. Raw payloads contain customer IDs, phone numbers, zip codes, line-item subtotals — all of which become "legitimate" validator values. One `get_raw` call significantly weakens the value-set check.

**Why deferred**: Same root cause as above; field-level whitelisting needed. Severity is bounded because the validator still strips *uncited* bare numbers.

**Production fix**: exclude get_raw results from `_collect_tool_numbers`; let the provenance round-trip serve as the citation signal for raw payloads.

## 38. meta_to_universal timezone assumption

**What**: `occurred_at` for Meta insights is synthesised as `date_start + "+00:00"` (UTC), but Meta buckets days in the ad account's configured timezone. For IST accounts, a "2024-01-15" bucket is actually 2024-01-14T18:30Z, shifting ad_spend events 5.5h vs. correlated order_revenue events.

**Why deferred**: Meta's Insights API does not return the account timezone in the payload itself; fetching it requires a separate `/act_<id>?fields=timezone_name` call and would require storing per-merchant TZ config.

**Production fix**: store `timezone_name` during connector auth; apply tzdata offset when synthesising occurred_at.

## 39. margin_watch adset pause expected_inr_impact overstated

**What**: `expected_inr_impact = total_spend * pause_fraction` treats the full spend as pure savings. Real net impact = `spend_reduction − revenue_lost` ≈ `total_spend * pause_fraction * (1 − blended_roas)`. At ROAS > 1, computed "savings" are actually negative.

**Why deferred**: Proposal is labelled as `would_do_api_call` (simulated, not executed); the calculation is directionally correct (low-ROAS campaign → reduce spend) even if the ₹ estimate is wrong.

**Production fix**: compute `net_impact = spend_cut * (1 - blended_roas)`; add both gross and net lines to the proposal note.

## 40. `compare()` tool sums rate metrics when group_by is added

`_compare()` in `app/chat/tools.py` calls `sum(float(r[value_col]) for r in rows)` to aggregate multi-row results. For rate/ratio metrics (`rto_rate`, `roas`, `cac`, `average_order_value`), summing values across groups produces an arithmetically meaningless number (e.g. summing per-courier RTO rates). The tool schema doesn't currently expose `group_by` for `compare`, so the model can't trigger this today. Latent risk if the tool schema is extended. Fix: for rate metrics, compute the aggregate from the underlying numerator/denominator rather than summing the ratio values, or reject `compare()` with `group_by` for rate metrics. Scope: catalog + compare handler change.

## 41. DuckDB sandbox has no memory limit or statement timeout

`duckdb.connect(database=":memory:")` in `app/warehouse/duckdb_view.py` uses no `memory_limit`, thread count, or query timeout. A pathological query (large CROSS JOIN, recursive CTE, explosive `ARRAY_AGG`) can OOM the host process or hang indefinitely. `SET` is in the forbidden tokens so the model cannot tune at runtime, and the app doesn't set a sane default at connect time. Fix: before adding `SET` to the forbidden-token list, execute `conn.execute("SET memory_limit='1GB'; SET threads=2;")` at connect time; wrap `conn.execute(query_exec)` in a thread with a timeout. Scope: sandbox connect setup.

## 42. CAC metric provenance omits order events

`query_metric(cac)` collects provenance only from the ad_spend events (the numerator). The denominator `total_orders` is computed from `order_revenue` events but those event IDs are not added to the provenance bundle. A user who asks "how was the CAC calculated?" can trace the spend half but not the order-count half. Fix: collect order event_ids in the `orders` CTE and union them into the provenance lookup, mirroring the `roas` metric which already does this. Scope: catalog SQL change.

## 43. Meta campaigns cursor never advances

The campaigns resource for Meta Ads (`app/connectors/meta_ads/connector.py`) requests fields `id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time` — none of which is `updated_at`/`date_stop`/`created_at`. The ingest runner's cursor logic (`runner.py:93`) finds no timestamp to advance, so the cursor stays at `None` and ALL campaigns are re-fetched on every ingest run. This is idempotent (upsert dedups on `source_record_id`) but wasteful and burns Meta rate-limit quota. Fix: add `updated_time` to the campaigns fields list and key the cursor on it. Scope: connector fields list.

## 44. `compare()` tool uses overlapping trailing windows, not period-over-period

The `compare(period_a, period_b)` tool accepts values from `["7d","14d","30d","90d"]` — all rolling windows anchored to NOW(). Any two values produce strictly nested windows (e.g., "7d" is a subset of "30d"), so `delta = val_b - val_a` measures the non-overlapping tail (days 8-30), and `pct_change = delta/val_a` is a ratio of one window's tail to its head. A "compare 7d vs 30d" query does not compare "this week vs this month" in the conventional sense. The system prompt steers the model to call `query_metric` twice for true period-over-period comparisons, but `compare`'s own tool description is misleading. Fix: accept explicit start/end timestamps, or rename the params to make the nested-window semantics explicit. Scope: catalog SQL + tool schema change.

## 45. `_pull_refunds` in Shopify connector re-fetches all orders via API

`app/connectors/shopify/connector.py:_pull_refunds()` iterates `_pull_orders(since)` solely to discover order IDs, then calls the Shopify `/orders/{id}/refunds.json` endpoint for each. This means every ingest run that includes "refunds" makes a full second sweep of all recent orders via the Shopify API — doubling the rate-limit cost and wall-clock time. The orders were already fetched moments earlier under the "orders" resource. Fix: read order IDs from `raw_shopify_orders` in the local Postgres DB instead of re-calling Shopify. Scope: connector + runner coordination.

## 46. Ingest cursor advances based on data that was never written

`app/ingest/runner.py` advances `latest_cursor` from `record.payload` timestamps even when the upsert uses `on_conflict_do_update(..., set_={"fetched_at": ..., "run_id": ...})` — meaning the payload was NOT overwritten. If the connector returns a new timestamp for a record that already exists, the cursor advances past it. On the next ingest run, records before the advanced cursor will be skipped even though they were never written. In practice idempotency and preserved payloads limit the blast radius, but the cursor can drift ahead of the actual write frontier. Fix: only advance the cursor when the upsert actually inserted a new row (check `result.rowcount` or use `ON CONFLICT DO UPDATE RETURNING xmax`). Deferred: requires schema/query changes and careful handling of the RETURNING clause across engines.

## 47. `BaseConnector._post` is dead code

`app/connectors/base.py` defines `_post(url, ...)` but no connector ever calls it — Shopify, Meta Ads, and Shiprocket are all read-only during ingestion. The method has correct implementation but adds surface area that could be misused (e.g., accidentally writing to a live API during ingest). Fix: remove unless a write-back connector is added. Deferred: no active caller; safe to leave until a POST-using connector is needed.

## 48. `compare()` tool sums non-additive metrics producing nonsense aggregates

`app/chat/tools.py:_compare._total` sums per-group metric values to produce an overall total: `sum(float(r.get(value_col, 0) or 0) for r in rows)`. For additive metrics (revenue, ad_spend) this is correct. For ratio/average metrics — `average_order_value`, `cac`, `rto_rate` — summing per-group values is meaningless (sum of per-courier RTO rates ≠ overall RTO rate). The `compare` tool exposes this for any metric the catalog supports; a query like "compare AOV by courier between 7d and 30d" produces a wrong `delta` and `pct_change` that are cited with full provenance. Fix: disallow `group_by` with ratio/average metrics in `compare`, or document that `compare` is additive-only. Deferred: requires metric type metadata in the catalog.

## 49. Normalizers abort entire batch on single malformed row

`app/normalize/shopify_to_universal.py`, `meta_to_universal.py`, and `shiprocket_to_universal.py` process raw rows in a flat for-loop. A single `KeyError`, `ValueError`, or `Decimal` overflow propagates out of the loop and prevents `db.commit()` from running, rolling back all previously normalized rows in the same call. In practice, seed data is clean and this path does not fire. Fix: wrap each row in a SQLAlchemy savepoint (`db.begin_nested()`) with per-row rollback on exception. Deferred: savepoint per row adds overhead; acceptable as a production v2 concern.

## 50. DateTime columns store naive timestamps; time-window metrics are UTC-dependent

`app/warehouse/models.py` defines all DateTime columns as `DateTime(timezone=False)` (naive). Normalizers pass tz-aware UTC datetimes, which SQLAlchemy silently strips to naive UTC. Metric SQL compares `ev.occurred_at >= NOW() - INTERVAL '7d'` where `NOW()` returns TIMESTAMPTZ. On a Postgres instance configured for UTC (the Docker default), the implicit cast is safe. On a non-UTC Postgres, every time-window metric miscounts by the local UTC offset. Fix: `DateTime(timezone=True)` on all columns. Deferred: schema migration needed; demo setup uses UTC Docker Postgres where behavior is correct.

## 51. `Retry-After` HTTP-date format crashes connector retry delay

`app/connectors/base.py:_get` parses `Retry-After` with `float(resp.headers.get("Retry-After", 5))`. RFC 7231 allows both a delay-seconds integer (`Retry-After: 60`) and an HTTP-date (`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`). The latter causes `ValueError` from `float()`, which is caught by tenacity and retried without honoring the actual wait time. In practice, major APIs (Shopify, Meta, Shiprocket) send delta-seconds form. Fix: parse both forms (delta-seconds and HTTP-date diff from `datetime.now()`). Deferred: low practical risk; tenacity still retries correctly, just without the preferred delay.

## 52. `rto_rate` metric provenance cites all shipments, not just RTOs

`app/warehouse/metrics/catalog.py:155-156` ARRAY_AGGs all shipment entity IDs into `event_ids` and cites the raw records for every shipment. When a user sees "RTO rate = 12%", the cited provenance shows all 100 shipment payloads rather than just the 12 RTO'd ones. The numeric claim is correct; only the citation breadth is misleading. Fix: also collect entity_ids for RTO events specifically and cite those, or filter `event_ids` to the rto-events subset. Deferred: provenance structure change; the numeric value is accurate.

## 53. `margin_watch` blended ROAS trigger uses all-channel revenue

`app/agents/margin_watch.py:153-163` computes `blended_roas = total_all_channel_revenue / ad_spend`. If organic or direct revenue is high, ROAS looks healthy even when paid campaigns are losing money. The adset pause proposal fires when `blended_roas < 2.0` — meaning it may fail to fire when paid ROAS is terrible but organic masks it, or may fire incorrectly when non-paid revenue dips seasonally. Fix: attribute revenue to paid sources via UTM or discount-code matching before computing paid ROAS. Deferred: attribution requires UTM data not in the current schema.

## 54. Shiprocket pagination exits early if server caps `per_page` below requested

`app/connectors/shiprocket/connector.py:_pull_shipments` terminates when `len(items) < params["per_page"]` (100). If Shiprocket server-side caps `per_page` at, say, 50, every page returns <100 → connector stops after page 1, silently missing all but the first page. The test `test_pagination_stops_when_partial_page` confirms this is the intended contract (partial page = last page). In practice, Shiprocket honors `per_page=100`. A server-cap scenario would require reading `meta.total_pages` from the response. Deferred: no observed cap; the partial-page termination is the documented pagination contract.

## 56. `make seed` omits `[dev]` extras; `make test` fails on seed-then-test path

`Makefile:seed` runs `pip install -e . -q` without `[dev]`, so pytest and ruff are not installed. A developer who runs `make seed && make test` on a fresh clone (without `make bootstrap`) hits a `command not found: pytest` error. The documented entry point is `make bootstrap` which does install dev extras. Fix: add `[dev]` to the seed target. Deferred: `make seed` is intentionally lightweight; the fix would add ~30s of dev-dep installation to a path that doesn't need them.
