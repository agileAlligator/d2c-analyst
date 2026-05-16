# Project Status

**Last updated:** 2026-05-16
**Phase:** Complete — v0.1.4 (connector cleanup + stale-value purge)

## What's built

| Component | Status | Notes |
|---|---|---|
| Repo skeleton | ✅ | pyproject, Docker Compose, Makefile, CI |
| Shopify connector | ✅ | orders, products, refunds, customers — cursor + retry |
| Meta Ads connector | ✅ | campaigns, adsets, ads, insights (daily) |
| Shiprocket connector | ✅ | orders, shipments — Bearer token from .env |
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
| Margin Watch agent | ✅ | courier switch, ad pause (ROAS 1.45x below 2.0 threshold), price raise proposals; NOT_SENT enforced |
| Streamlit UI | ✅ | chat + tool call trace + routing badge (⚡/🧠) |
| FastAPI | ✅ | /chat, /runs, /health; RoutingInfo in ChatResponse |
| Seed data | ✅ | demo: 80 orders, 30d Meta (ROAS 1.45x), 80 shipments; demo2: 5 orders for RLS isolation |
| RLS hardening | ✅ | d2c_app role (NOSUPERUSER NOBYPASSRLS); NullPool + after_begin listener; GUC enforced end-to-end |
| Eval suite | ✅ | 19 golden questions (incl. 3 adversarial), citation coverage ≥80%, accuracy ≥70% |
| Adversarial hardening | ✅ | 7-round loop; 0 Slytherin points in final round |
| Connector fixture tests | ✅ | Meta Ads + Shiprocket fixture JSON + 16 offline tests |
| Bench script | ✅ | scripts/bench_ingest.py; 200 rows at ~335 rows/sec; make bench |
| Seed determinism | ✅ | BASE_DATE=2026-05-13 anchor; re-seeds produce identical analytical output |
| CI | ✅ | GitHub Actions: lint + pytest (eval skipped when OPENAI_API_KEY=dummy) |
| README | ✅ | All 9 brief questions answered; real agent run log (1.45x ROAS, ₹5,310/month) |

## Key decisions

- **Connectors:** Shopify, Meta Ads, Shiprocket — rationale in README §2 (rejected alternatives: Google Ads, Klaviyo, QuickBooks)
- **Schema:** raw (immutable) + universal (entities+events+links) + provenance as first-class table
- **Citation:** server-side validation, not prompt-only — every number resolved against provenance; bare numbers replaced with `*(uncited)*` in the returned text
- **Model routing:** HeuristicRouter (8 signals) + FrugalGPT cascade; gpt-4o-mini default, gpt-4o on complexity or citation failure
- **Agent:** Margin Watch — proposes courier switch, ad pause (fires at ROAS 1.45x < 2.0 threshold), price raise; never executes (NOT_SENT: True)
- **Currency:** INR throughout (assumption documented in README)
- **Auth:** Shiprocket SHIPROCKET_TOKEN from .env; Shopify private app; Meta long-lived token
- **Idempotency:** MD5 event IDs keyed on (merchant_id, entity_id, event_type, occurred_at)
- **Ingest payload preservation:** on re-ingest conflict, payload is preserved (not overwritten); only `fetched_at` and `run_id` are updated — protects provenance round-trip from partial API responses
- **Per-row provenance:** `metrics/catalog.py` uses `.get()` not `.pop()` when flattening provenance IDs (v0.1.3 fix) so each result row retains its own per-row provenance list; the agent cites only the source rows that produced each specific order/proposal, not the global bundle
- **RLS two-path architecture (v0.1.3):** SQLAlchemy path connects as `d2c_app` (`NOSUPERUSER NOBYPASSRLS`) with `NullPool` + `after_begin` listener replaying `app.current_merchant` GUC; DuckDB analytical path uses the superuser (`DATABASE_URL_ANALYTICS`) and enforces merchant isolation via per-query temp views with `WHERE merchant_id = '<merchant_id>'`. Tradeoff is documented in README §3.

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

## Known limitations

- **Single-digit numbers bypass citation check:** the bare-number regex requires ≥2 digits (`\d{2,}`); "3 orders" would not be caught if uncited. Low practical impact (single counts are rarely the sole datapoint in an answer) but the 100% citation claim has this caveat.
- **`contribution_margin` excludes ad cost:** CM = revenue − shipping − RTO only. Ad cost attribution per order requires UTM/click-id joining that the schema does not model. SKUs profitable on logistics costs but unprofitable on blended CAC will not be flagged by Margin Watch. The `_propose_adset_pause` (ROAS threshold) partially compensates at the campaign level.
- **Shiprocket token never auto-refreshes:** token expires ~10 days; ingest will fail with 401 until manually rotated in `.env`. Acceptable for demo; production needs a refresh-token flow.
- **No per-API-call `updated_at` comparison in ingest:** on conflict, the original payload is preserved unconditionally. If an order is genuinely updated at source, the warehouse will not reflect the correction until the raw record is manually purged.

