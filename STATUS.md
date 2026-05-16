# Project Status

**Last updated:** 2026-05-16
**Phase:** Complete — v0.1.6 (adversarial loop round 2 hardening)

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
- **Shopify `_paginate`** — `item['id']` → `item.get('id', 'unknown')` to prevent `KeyError` on malformed payloads (matches Meta's defensive style).
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
- **README/STATUS test count** — corrected to 186 total; 52 DB-gated, 21 eval-gated, 113 fully offline (double-count fixed).

## Known limitations

- **Single-digit numbers bypass citation check:** the bare-number regex requires ≥2 digits (`\d{2,}`); "3 orders" would not be caught if uncited. Low practical impact (single counts are rarely the sole datapoint in an answer) but the 100% citation claim has this caveat.
- **`contribution_margin` excludes ad cost:** CM = revenue − shipping − RTO only. Ad cost attribution per order requires UTM/click-id joining that the schema does not model. SKUs profitable on logistics costs but unprofitable on blended CAC will not be flagged by Margin Watch. The `_propose_adset_pause` (ROAS threshold) partially compensates at the campaign level.
- **Shiprocket token never auto-refreshes:** token expires ~10 days; ingest will fail with 401 until manually rotated in `.env`. Acceptable for demo; production needs a refresh-token flow.
- **No per-API-call `updated_at` comparison in ingest:** on conflict, the original payload is preserved unconditionally. If an order is genuinely updated at source, the warehouse will not reflect the correction until the raw record is manually purged.

