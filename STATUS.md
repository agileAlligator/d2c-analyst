# Project Status

**Last updated:** 2026-05-17
**Phase:** Complete — v0.1.12 (final stress test + push)

## What's built

| Component | Status | Notes |
|---|---|---|
| Repo skeleton | ✅ | pyproject, Docker Compose, Makefile, CI |
| Shopify connector | ✅ | orders, products, refunds, customers — cursor + retry |
| Meta Ads connector | ✅ | campaigns, insights (daily) |
| Shiprocket connector | ✅ | shipments — Bearer token from .env |
| Ingestion runner | ✅ | idempotent upsert, cursor tracking, set_merchant per job |
| Universal schema | ✅ | entities, events, links, provenance + RLS |
| Shopify normalizer | ✅ | orders → order_revenue events; MD5 event IDs for idempotency |
| Meta normalizer | ✅ | insights → ad_spend events with provenance |
| Shiprocket normalizer | ✅ | shipments → shipping_cost + rto events |
| Identity resolution | ✅ | Shopify↔Shiprocket (order_number exact); Meta↔Shopify (discount code heuristic, conf 0.6) |
| Metric catalog | ✅ | revenue, ad_spend, rto_rate, contribution_margin, cac; GROUP BY fixed |
| SQL sandbox | ✅ | SELECT-only DuckDB over Postgres, provenance bundle |
| Chat tool-use loop | ✅ | 6 tools, gpt-4o/gpt-4o-mini via router, 12-turn max, json serialization fixed |
| Model router | ✅ | HeuristicRouter: 8 signals → gpt-4o-mini or gpt-4o; FrugalGPT cascade on citation fail |
| Citation validator | ✅ | server-side, always scans bare numbers ≥2 digits (≥10), 2 retries, unverified badge fallback |
| Margin Watch agent | ✅ | courier switch, ad pause (ROAS 1.27x below 2.0 threshold), price raise proposals; NOT_SENT enforced |
| Streamlit UI | ✅ | chat + tool call trace + routing badge (⚡/🧠) |
| FastAPI | ✅ | /chat, /runs, /health; RoutingInfo in ChatResponse |
| Seed data | ✅ | demo: 80 orders, 30d Meta (ROAS 1.27x), 80 shipments; demo2: 5 orders for RLS isolation |
| RLS hardening | ✅ | d2c_app role (NOSUPERUSER NOBYPASSRLS); NullPool + after_begin listener; GUC enforced end-to-end |
| Eval suite | ✅ | 19 golden questions (incl. 3 adversarial), non-adversarial citation 100%, adversarial ≥66%, accuracy ≥60% |
| Adversarial hardening | ✅ | 7-round loop; 0 Slytherin points in final round |
| Connector fixture tests | ✅ | Meta Ads + Shiprocket fixture JSON + 16 offline tests |
| Bench script | ✅ | scripts/bench_ingest.py; 200 rows at ~335 rows/sec; make bench |
| Seed determinism | ✅ | BASE_DATE=2026-05-17 anchor; re-seeds produce identical analytical output |
| CI | ✅ | GitHub Actions: lint + pytest (eval skipped when OPENAI_API_KEY=dummy) |
| README | ✅ | All 9 brief questions answered; real agent run log (1.27x ROAS, ₹4,642/month) |

## Key decisions

- **Connectors:** Shopify, Meta Ads, Shiprocket — rationale in README §2 (rejected alternatives: Google Ads, Klaviyo, QuickBooks)
- **Schema:** raw (immutable) + universal (entities+events+links) + provenance as first-class table
- **Citation:** server-side validation, not prompt-only — every number resolved against provenance; bare numbers replaced with `*(uncited)*` in the returned text
- **Model routing:** HeuristicRouter (8 signals) + FrugalGPT cascade; gpt-4o-mini default, gpt-4o on complexity or citation failure
- **Agent:** Margin Watch — proposes courier switch, ad pause (fires at ROAS 1.27x < 2.0 threshold), price raise; never executes (NOT_SENT: True)
- **Currency:** INR throughout (assumption documented in README)
- **Auth:** Shiprocket SHIPROCKET_TOKEN from .env; Shopify private app; Meta long-lived token
- **Idempotency:** MD5 event IDs keyed on (merchant_id, entity_id, event_type, occurred_at)
- **Ingest payload preservation:** on re-ingest conflict, payload is preserved (not overwritten); only `fetched_at` and `run_id` are updated — protects provenance round-trip from partial API responses
- **Per-row provenance:** `metrics/catalog.py` uses `.get()` not `.pop()` when flattening provenance IDs (v0.1.3 fix) so each result row retains its own per-row provenance list; the agent cites only the source rows that produced each specific order/proposal, not the global bundle
- **RLS two-path architecture (v0.1.3):** SQLAlchemy path connects as `d2c_app` (`NOSUPERUSER NOBYPASSRLS`) with `NullPool` + `after_begin` listener replaying `app.current_merchant` GUC; DuckDB analytical path uses the superuser (`DATABASE_URL_ANALYTICS`) and enforces merchant isolation via per-query temp views with `WHERE merchant_id = '<merchant_id>'`. Tradeoff is documented in README §3.
- **rto_rate time-window correctness (v0.1.5):** time filter changed from `en.first_seen` (normalize-run timestamp, always ~now) to `ev.occurred_at` (actual RTO date set during normalization), so 7d/14d/30d/90d windows return meaningfully different values on seed data instead of collapsing to "all RTOs are in every window."
- **contribution_margin includes refunds (v0.1.5):** `_upsert_refund` now resolves `order_number` from the linked order entity and stores it in refund entity attributes; the CM CTE joins on `order_number`, so refunds no longer fall into a NULL group and disappear from per-order CM.

## Adversarial hardening (7 rounds)

Fixes applied through iterative adversarial testing (Opus adversary → Opus plan → Sonnet impl → Opus review):

- **Comma-formatted numbers** split across commas (30,412 → "30" + "412") — fixed in `bare_number_re`
- **Time-period words** ("14 days", "30 days") stripped as bare numbers — fixed in `_timeref_re`
- **Year numbers** in claims stripped — fixed in `_is_yearlike` with context-aware exemption
- **Calendar-period queries** (Q1, lifetime, today, MTD, YTD) answered with wrong rolling-window numbers — system prompt now refuses with explicit guidance
- **Compare tool delta/pct_change** had shared provenance ID — split into `delta_provenance_id` and `pct_change_provenance_id`
- **Refunds metric** missing — added to catalog (`SUM(-ev.amount)` where `event_type='refund'`)
- **ROAS metric** missing — added to catalog (two-CTE pattern, combined provenance)
- **Orders metric** missing — added to catalog (`COUNT(DISTINCT entity_id)` on `order_revenue` events)
- **SKU/product grain** queries — system prompt explicitly refuses; no SKU-level events in schema
- **`list_entities` count confusion** — renamed `count` → `returned`; prompt says it's a sample not a total
- **DuckDB JSONB syntax** — `attributes->>'key'` crashed; fixed to `json_extract_string(attributes, '$.order_number')`
- **NULL campaign group** — `revenue + group_by=campaign` returned NULL-named row; now raises descriptive error
- **Rule 10** — specific entity lookup must use `sql` with `json_extract_string`, not `list_entities`
- **Rule 11** — derived arithmetic (averages, percentages, per-day rates) prohibited in prose
- **RTO ≠ refund rate** — explicit WARNING added to system prompt
- **Impressions/clicks** columns exposed from `ad_spend` metric in system prompt
- **Date string stripping** — ISO dates, written dates, week labels protected in `excluded_spans`
- **Proper-noun years** ("Diwali Sale 2024") — two-consecutive-capitalized-word heuristic in `_is_yearlike`
- **Compare tool semantics** — system prompt: `compare(7d, 14d)` is overlapping trailing windows, NOT WoW
- **DuckDB SQL gotchas** — `list()`/`array_agg()` not `json_agg()`; qualify `attributes` with table alias
- **`write_note` SQL** — `CAST(:note AS jsonb)` fixes SQLAlchemy parameter collision; rowcount check added

## v0.1.5 full-codebase audit (5-agent parallel review)

A second round of hardening run via the `/harden` slash command (`.claude/commands/harden.md`): 5 Opus audit agents in parallel across non-overlapping slices, triage, Sonnet fix agents, then 3 Opus reviewers. Fixes applied:

- **`contribution_margin` dropping refunds** — `_upsert_refund` now resolves and stores `order_number` from the linked order entity (was NULL → refunds fell out of the CM CTE's order_number join).
- **`rto_rate` time-window filter** — switched from `en.first_seen` to `ev.occurred_at` so 7d vs 90d queries return different values on seed data.
- **`BaseAgent` exception path** — `db.rollback()` added before the second `db.commit()` so a failed `_execute` cannot half-commit partial state.
- **Shopify `_paginate`** — missing `id` field now raises `ValueError` to prevent silent data corruption (phantom entities keyed to "unknown" would collide on upsert).
- **Shiprocket `_pull_shipments`** — hardcoded `100` replaced with `params["per_page"]` in the stop condition; changing `per_page` no longer silently mis-paginates.
- **Shiprocket `auth_status`** — dead `== 200` check removed (raise_for_status already raised on non-200); returns `True` directly.
- **Meta connector** — `datetime.utcnow()` → `datetime.now(timezone.utc)` (deprecated in 3.12).
- **`identity.py`** — dead OR branch removed (`e_order.attributes->>'id'` was never set anywhere in the normalizer).
- **`.env.example`** — DATABASE_URL port 5432 → 5434 to match `docker-compose.yml`.
- **Makefile `seed` target** — added `pip install -e . -q` so `make seed` works on a fresh clone without first running `make bootstrap`.
- **README** — false-assertion claim about DEV_MODE corrected to the actual setup instruction.
- **`docs/wont_fix.md`** — 4 new entries added (Meta/Shiprocket cursor stalling, stale vendor API versions, Indian lakh-format regex gap, true 36-turn LLM budget vs the simplified "12-turn max" in README), and the prior duplicate Indian-format entry was consolidated into the more precise lakh-format entry. Final numbering is 1–12, sequential.
- **Tooling** — `/harden` slash command codified in `.claude/commands/harden.md`.

## v0.1.6 hardening (adversarial loop round 2)

- **Seed idempotency** — `seed_demo_merchant.py` and `seed_second_merchant.py` now use `_upsert_raw()` (check-before-insert on `(merchant_id, source_record_id)`) instead of `db.merge(..., id=uuid.uuid4())`, which caused `IntegrityError` on the unique constraint on a second `make seed`.
- **`.env.example` `API_KEYS_RAW`** — corrected from `API_KEYS` to `API_KEYS_RAW` to match the pydantic-settings field name in `app/config.py`; `ALLOWED_ORIGINS` changed to JSON array format to prevent `JSONDecodeError` at import.
- **Makefile DB wait** — replaced `sleep 3` with a `pg_isready` poll in the `bootstrap` target so a fresh Postgres 16 image doesn't race on first boot.
- **`requests` dependency** — added to `pyproject.toml` (was imported by `app/ui/streamlit_app.py` but absent from declared dependencies).
- **`bench_ingest.py` sys.path shim** — added `sys.path.insert(0, ...)` matching the pattern in the other seed scripts so the script runs without `pip install -e .`.
- **README env var + test count** — `API_KEYS` corrected to `API_KEYS_RAW`; test count updated to "186 test functions; tests requiring DATABASE_URL or OPENAI_API_KEY skip automatically" (avoids false-precision parametrized count).
- **`wont_fix.md`** — updated with 8 new entries (13–20): `compare` synthetic provenance IDs, `rto_rate` all-time denominator, `NOW()` time-bounded README figures, products/customers without normalizers, `d2c_app` role owning tables, CI RLS running as superuser, temporal-prefix comma-number validator bypass, chat history prompt injection.

## v0.1.6 additional fixes (round-2 hardening)

- **shiprocket `_paginate_orders` null payload** — same `data.get("data") or {}` fix applied to `_paginate_orders` (was fixed in `_pull_shipments` in round 1 but missed sibling method).
- **meta_to_universal defensive null** — `p.get("actions") or []` / `p.get("action_values") or []` guard against explicit `null` from Meta API.
- **validator duplicate-ref dedup** — `seen_refs: set` prevents repeated `_try_resolve` DB calls and duplicate issues when the same cite ref appears multiple times in a response.
- **Stale tests fixed** — `test_record_id_insight` updated to include required `campaign_id`; `test_negative_margin_emits_raise_price` updated to assert `entity_key == "order:..."` (variant-level branch removed).
- **`wont_fix.md` #17 and #18 corrected** — #17 reworded to accurately scope the table-ownership concern to the no-`.env` case; #18 corrected to note `d2c_app` IS created in CI but tests still connect as superuser.
- **README/STATUS test count** — corrected to 291 total; 52 DB-gated, 21 eval-gated, offline remainder (double-count fixed).

## v0.1.7 wont_fix audit (entries 21–27)

Seven new known-limitation entries added to `docs/wont_fix.md`. Code fixes for these issues are deferred to v0.2 or later.

- **#21** — Streamlit `merchant_id` sidebar input is silently ignored (Pydantic drops extra fields; `/runs` uses `X-API-Key` header, not query param).
- **#22** — Multi-turn history is stripped of `<cite>` tags before storage (API returns validator-cleaned answer; model has no cite context on follow-up turns).
- **#23** — `raise_price` proposals target completed historical orders via the wrong Shopify endpoint and use historical loss, not forward-looking impact, as the ₹ figure.
- **#24** — Shiprocket DESC offset pagination can silently skip records when concurrent writes shift page boundaries during a fetch.
- **#25** — `refunded_order_count` counts refund events (`ev.entity_id` = refund entity), not distinct orders; an order with two partial refunds counts as 2.
- **#26** — `_collect_tool_numbers` adds JSONB identifier strings (order numbers, Shopify IDs, postal codes) to `tool_value_set`, allowing identifier-matching citations to pass value verification.
- **#27** — `tool_value_set` empty when the model answers without tool calls; the validator skips numeric value verification entirely, letting fabricated numbers through if the provenance ID resolves.

## v0.1.8 adversarial hardening (round 3)

Multi-round parallel Opus audit → Sonnet fix → Opus review loop. Fixes applied:

- **`meta_to_universal.py` null-value crashes** — `a.get("value", "0")` returned `None` when key existed with JSON null value; changed to `a.get("value") or "0"` in all three locations (spend, purchase_count, purchase_value).
- **Seed purchase count wrong** — `actions[].value` was writing `purchase_value / 5000` (revenue-derived float) instead of the simulated `purchases` integer count; corrected to match what the Meta normalizer expects (integer count in `actions`, revenue float in `action_values`).
- **Seed product ID non-determinism** — product/variant IDs used Python `hash(sku)` which is randomized by `PYTHONHASHSEED`; changed to `hashlib.md5` for stable IDs on re-seed.
- **Margin Watch courier savings overstated (single-courier case)** — when only one courier passed the minimum-shipments threshold, `best=None` and formula collapsed to `total_RTOs × unit_cost` (total, not differential); added `if best is None: return` guard; removed now-dead `if best else` branches.
- **Margin Watch test assertions stale** — `expected_inr_impact` updated from 1800.0 to 1612.5 (differential formula: 25 × (0.48−0.05) × 150); adset-pause assertion updated from `body.status == "PAUSED"` to `"PAUSED" in note`.
- **DuckDB `POSTGRES_QUERY` bypass** — `postgres_query('pg', ...)` could execute raw SQL against the attached Postgres database, bypassing merchant-scoped temp views; added `POSTGRES_QUERY` and `POSTGRES_EXECUTE` to `_FORBIDDEN_TOKENS`.
- **DuckDB SQL comment bypass** — `pg/**/.entities` bypassed the `pg.` schema-access check; added `_strip_sql_comments()` applied to all checks before tokenization.
- **DuckDB replacement-scan bypass** — `SELECT * FROM 'data.csv'` not blocked by token filter (no function name); added `SET disabled_filesystems='LocalFileSystem'` to the connection setup.
- **DuckDB path-literal regex false positives** — third regex alternative for filename extensions caused false-positive blocks on legitimate string values; removed, relying on `disabled_filesystems` for that vector.
- **Validator zero-citation false failure** — `if num != 0` filter in value-check loop caused `<cite>0</cite>` to always fail even when 0.0 was in `tool_value_set`; removed the filter.
- **`ingest_cursors` RLS** — table had no RLS policy; added `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + `CREATE POLICY merchant_isolation` matching the pattern in `create_tables.py`.
- **README/STATUS accuracy fixes** — "token-bucket" → "sliding-window" rate limiter; accuracy claim qualified as "~63% on last known run (target ≥70%)"; ₹5,310/month → ₹4,233/month (differential courier formula); golden-questions table noted as representative sample.
- **`wont_fix.md` entries #28–29 added** — multi-number unverified cite double-annotation (cosmetic); `ingest_cursors` RLS `WITH CHECK` missing (write-side unfiltered, accepted as consistent with codebase pattern).
- **9 new tests** — single-courier early-return; SQL comment bypass (block + line); path-literal (absolute + dot-relative); zero-cite pass/fail; multi-number cite any-match.

## v0.1.9 adversarial hardening (round 4)

- **DuckDB `POSTGRES_SCAN` bypass** — `postgres_scan('pg', ...)` opened a new Postgres connection bypassing merchant temp views; added `POSTGRES_SCAN`, `POSTGRES_SCAN_PUSHDOWN`, `SET`, `RESET`, `USE` to `_FORBIDDEN_TOKENS`.
- **DuckDB `pg_catalog`/`information_schema` bypass** — schema regex only blocked bare `pg.`; `pg_catalog.pg_settings` slipped through; extended regex to cover `pg_catalog.` and `information_schema.`.
- **DuckDB non-shadowed tables** — `agent_runs`, `ingest_cursors`, `ingest_jobs` were not in the merchant-view shadow list; added `WHERE FALSE` stub views to prevent cross-merchant enumeration.
- **Shiprocket order→shipment conflation** — `("shiprocket", "order")` mapped to `RawShiprocketShipment`; order payloads (missing AWB, courier, freight) were normalized as phantom shipment entities; removed `"orders"` from Shiprocket `RESOURCES` and `RAW_MODEL_MAP` (shipments already capture the needed data).
- **Shiprocket `freight` key mismatch** — normalizer read `charges.freight` but fixture/API uses `charges.freight_charges`; corrected key in `shiprocket_to_universal.py`.
- **Meta Ads resource false claim** — STATUS/README said "campaigns, adsets, ads, insights"; adsets/ads are not separate resources (only appear as insight fields); corrected to "campaigns, insights (daily)".
- **`pandas` missing from pyproject.toml** — hard runtime dep via `duckdb_view.py:fetchdf()`; added `pandas>=2.0`; removed dead deps `sqlmodel`, `alembic`, `rq` (never imported).
- **`.env.example` missing `DATABASE_URL_ANALYTICS`** — DuckDB silently fell back to port 5432 default without it; added with correct local value.
- **Makefile dev deps** — `pip install -e .` → `pip install -e ".[dev]"` in bootstrap so `make test` works on fresh clone.
- **Eval false claim** — `test_negative_cm_order_1063` accepted "no orders" as a valid answer; fixed to require mention of 1063 or a negative figure.
- **Eval RLS timeout mis-attribution** — tests failed with "RLS isolation broken" on LLM timeouts; added `pytest.skip` guard when answers contain no numbers.
- **Margin Watch reasoning string** — parentheses missing in `(worst_rate − best_rate) × cost × shipments` display; `total_ships` multiplier also absent; fixed.
- **`_get_raw` missing customers table** — `raw_shopify_customers` absent from chat tools lookup list; added.
- **`wont_fix.md` entries #30–35 added** — Meta first-match attribution window, validator any-vs-all tradeoff, revenue refund-date bucketing, `set_merchant` silent zero rows, `filters` param silently ignored, single-digit numbers bypass bare-number scan.

## v0.1.10 adversarial hardening (round 5)

- **Citation any→all fix** — `validate_and_clean` used `any()` when checking multi-number cite values; a fabricated number in `<cite>₹31,814 (was ₹99,999 forecast)</cite>` would pass if 31,814 resolved. Changed outer quantifier to `all()` so every number inside a cite must appear in `tool_value_set`.
- **DuckDB `postgres_attach`/`mysql_attach`/`sqlite_attach` SSRF** — these functions open a new TCP connection, bypassing `disabled_filesystems`; added to `_FORBIDDEN_TOKENS`.
- **DuckDB `EXPLAIN` leaks physical paths** — `EXPLAIN SELECT * FROM entities` reveals pg attachment path and table names; added to `_FORBIDDEN_TOKENS`.
- **DuckDB bare `pg_class`/`pg_tables`/`pg_settings`/`pg_database`/`pg_namespace`/`pg_views`** — DuckDB exposes pg catalog views under bare names; added to `_FORBIDDEN_TOKENS`.
- **Margin Watch per-order provenance leak** — `prov_ids = row.get("provenance_ids") or current.provenance_ids` fell back to the full result set's provenance when a row had `[]`; fixed to `or []` so a proposal never cites unrelated rows.
- **Shiprocket "unknown" shipment ID data corruption** — missing `id`/`shipment_id` collapsed to `natural_key="shiprocket:shipment:unknown"`, causing upsert overwrites; now raises `ValueError`.
- **README connector resource lists** — Shopify list was missing `customers`; Shiprocket "RTO events" was stale (derived, not a connector resource); "SKUs" corrected to "orders" (agent works at order grain); connector file paths corrected in AI-tools table.
- **wont_fix.md entries #36–39 added** — `_collect_tool_numbers` numeric ID pollution, `get_raw` payload polluting `tool_value_set`, `meta_to_universal` UTC timezone assumption, `margin_watch` overstated adset pause impact.
- **Test count** — updated to 291 total.

## v0.1.11 adversarial hardening (round 6)

- **`/runs` + `/runs/{id}` RLS gap** — endpoints queried `agent_runs` without calling `set_merchant`; with `d2c_app` role (NOSUPERUSER NOBYPASSRLS), the GUC was unset and RLS returned 0 rows silently. Added `set_merchant(db, merchant_id)` to both endpoints.
- **`rto_rate` group_by date/week/month SQL error** — `GROUP_BY_EXPRESSIONS` references `ev.occurred_at` but `rto_rate`'s agg CTE has no `ev` alias in scope; query failed with `column ev.occurred_at does not exist`. Added explicit `ValueError` guard matching the existing `roas` pattern.
- **Shopify `void` transactions counted as refunds** — `kind="void"` is a pre-capture authorization void, not a refund; over-counted refunds → under-counted revenue. Fixed to `kind == "refund"` only.

## v0.1.12 final stress test

- **DuckDB sandbox comment-context bypass (CRITICAL)** — `_strip_sql_comments` matched `/*..*/` across string-literal boundaries, allowing a forbidden token between two string literals to be stripped from the validator while DuckDB executed it (SSRF, cross-merchant read). Fix: reject any query containing `--`, `/*`, `*/`, or `\r` before comment-stripping. The chat surface has no legitimate need for SQL comments.
- **DuckDB HTTPFileSystem/S3 not disabled** — `disabled_filesystems='LocalFileSystem'` did not block `read_csv('https://...')`; extended to `LocalFileSystem,HTTPFileSystem,S3FileSystem`.
- **DuckDB additional forbidden tokens** — added file-read functions (`READ_ARROW`, `READ_AVRO`, `READ_XLSX`, `READ_EXCEL`, `ICEBERG_SCAN`, `DELTA_SCAN`, `READ_ICEBERG`, parquet metadata functions) and pg catalog bare names (`PG_PROC`, `PG_ATTRIBUTE`, `PG_TYPE`, `PG_CONSTRAINT`, `PG_INDEX`, `PG_INDEXES`, `PG_AUTHID`, `PG_ROLES`, `PG_USER`, `PG_SHADOW`).
- **Agent failure status reverts to "running"** — `agent_run.status = "failed"` was set before `db.rollback()`, which expired and reverted the attribute. Moved status assignment to after rollback so the commit writes it.
- **Shopify `total_shipping_price_set: null` crash** — `None.get("shop_money")` raised AttributeError when key existed with null value. Fixed with `or {}`.
- **Shiprocket `charges: null` crash** — same pattern on `charges` key. Fixed with `or {}`.
- **Meta Ads non-numeric `value` crash** — `int(float(...))` on unexpected string values. Added `_safe_int`/`_safe_float` helpers that default to 0 on parse failure.
- **README router accuracy claim unsupported** — "50 hand-labeled queries / 92% correct" had no dataset in repo; removed. Now says "21 unit tests covering all 8 signals."
- **README latency/cost not hedged** — P50/P95 and $/turn now labeled "(observed during development)" and "(estimated)."
- **README round count 7 vs 6** — changed to "6-round adversarial loop" to match STATUS.md.
- **Seed BASE_DATE stale** — bumped from 2026-05-13 to 2026-05-17 in both seed scripts.
- **.env.example missing DEV_MODE** — added.
- **Test count** — updated to 312.
- **Eval ranges corrected** — live DB measures ₹37,053 (30d), ₹11,491 (7d), ₹31,465 (ad_spend 30d), ₹14,527 (ad_spend 14d), 33 orders (30d); golden-question ranges and ground-truth comments updated to match; `test_negative_cm_order_1063` tightened to require order ID "1063" (not just the word "negative").
- **`.env.example` DATABASE_URL** — changed to `d2c_app:d2c_app` (app role) so fresh-clone users don't accidentally run as superuser and bypass RLS.
- **Connector resource leak fixed** — `run_connector` now uses try/finally to guarantee `connector.close()` on exception.
- **`_pull_refunds` exception scope** — narrowed from bare `except Exception` to `except httpx.HTTPStatusError` (re-raise non-404); 401/403 (revoked token, missing scope) now propagate instead of being silently swallowed.
- **4 new wont_fix entries** (#40–43): compare rate-metric summation, DuckDB memory limit, CAC one-sided provenance, Meta campaigns cursor.

## v0.1.13 adversarial hardening (round 3 of 5)

- **Shiprocket `_pull_shipments` KeyError** — `shipment['id']` raised `KeyError` on payloads where the primary key is `shipment_id`; changed to `shipment.get('id') or shipment.get('shipment_id')` with explicit `ValueError` when both are absent.
- **Ingest per-resource isolation** — single exception in any resource loop aborted all subsequent resources for the connector; wrapped each resource's inner loop in `try/except` with `db.rollback()` so sibling resources continue on partial failure.
- **Margin Watch dead fallback** — `order_id = row.get("order_number") or row.get("order_id")` — the `order_id` fallback was never populated by the `contribution_margin` query; removed dead branch.
- **Margin Watch courier provenance leak** — `provenance_ids=result.provenance_ids[:5]` cited provenance for ALL couriers on the worst-courier proposal; fixed to `worst.get("provenance_ids", [])[:5]` so the proposal only cites rows for the flagged courier.
- **`loop.py` unreachable return** — `return _timeout_result(...)` after the outer retry loop was never reached (the last attempt always returns inside the loop); removed.
- **Eval loose assertions tightened** — `_has_number(r"\d+")` on "total orders" → `_number_in_range(10, 500)` (demo has 80 orders, any single digit would pass before); "negative CM last month" regex `\d+\s*order` matched "0 orders" denial → `\b\d{4,}\b` requires a 4-digit order ID.
- **README "attributed revenue"** — agent log in README still said "attributed revenue"; updated to "all-channel revenue" to match `margin_watch.py`.
- **Test count** — updated 312 → 325 in README.
- **STATUS eval tier claim** — "citation coverage ≥80%" replaced with accurate thresholds (non-adversarial 100%, adversarial ≥66%, accuracy ≥60%) to match `test_eval_suite.py` assertions.
- **2 new wont_fix entries** (#46–47): ingest cursor advances on data never written; `_post` dead method on `BaseConnector`.

## Known limitations

- **Single-digit numbers bypass citation check:** the bare-number regex requires ≥2 digits (`\d{2,}`); "3 orders" would not be caught if uncited. Low practical impact (single counts are rarely the sole datapoint in an answer) but the 100% citation claim has this caveat.
- **`contribution_margin` excludes ad cost:** CM = revenue − shipping − RTO only. Ad cost attribution per order requires UTM/click-id joining that the schema does not model. Orders profitable on logistics costs but unprofitable on blended CAC will not be flagged by Margin Watch. The `_propose_adset_pause` (ROAS threshold) partially compensates at the campaign level.
- **Shiprocket token never auto-refreshes:** token expires ~10 days; ingest will fail with 401 until manually rotated in `.env`. Acceptable for demo; production needs a refresh-token flow.
- **No per-API-call `updated_at` comparison in ingest:** on conflict, the original payload is preserved unconditionally. If an order is genuinely updated at source, the warehouse will not reflect the correction until the raw record is manually purged.

