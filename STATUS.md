# Project Status

**Last updated:** 2026-05-13
**Phase:** Complete — v0.1.0

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
| Citation validator | ✅ | server-side, always scans bare numbers ≥100, 2 retries, unverified badge fallback |
| Margin Watch agent | ✅ | courier switch, ad pause (ROAS 1.35x below 2.0 threshold), price raise proposals; NOT_SENT enforced |
| Streamlit UI | ✅ | chat + tool call trace + routing badge (⚡/🧠) |
| FastAPI | ✅ | /chat, /runs, /health; RoutingInfo in ChatResponse |
| Seed data | ✅ | demo: 80 orders, 30d Meta (ROAS 1.35x), 80 shipments; demo2: 5 orders for RLS isolation |
| RLS hardening | ✅ | SET LOCAL GUC, no IS NULL escape, called at ingest + tools + agent |
| Eval suite | ✅ | 19 golden questions (incl. 3 adversarial), citation coverage ≥80%, accuracy ≥70% |
| CI | ✅ | GitHub Actions: lint + pytest (eval skipped when OPENAI_API_KEY=dummy) |
| README | ✅ | All 9 brief questions answered; real agent run log (1.35x ROAS, ₹4,860/month) |

## Key decisions

- **Connectors:** Shopify, Meta Ads, Shiprocket — see MASTER_PLAN.md §2.1
- **Schema:** raw (immutable) + universal (entities+events+links) + provenance as first-class table
- **Citation:** server-side validation, not prompt-only — every number resolved against provenance; bare numbers replaced with `*(uncited)*` in the returned text
- **Model routing:** HeuristicRouter (8 signals) + FrugalGPT cascade; gpt-4o-mini default, gpt-4o on complexity or citation failure
- **Agent:** Margin Watch — proposes courier switch, ad pause (fires at ROAS 1.35x < 2.0 threshold), price raise; never executes (NOT_SENT: True)
- **Currency:** INR throughout (assumption documented in README)
- **Auth:** Shiprocket SHIPROCKET_TOKEN from .env; Shopify private app; Meta long-lived token
- **Idempotency:** MD5 event IDs keyed on (merchant_id, entity_id, event_type, occurred_at)
- **Ingest payload preservation:** on re-ingest conflict, payload is preserved (not overwritten); only `fetched_at` and `run_id` are updated — protects provenance round-trip from partial API responses

## Known limitations

- **Single-digit numbers bypass citation check:** the bare-number regex requires ≥2 digits (`\d{2,}`); "3 orders" would not be caught if uncited. Low practical impact (single counts are rarely the sole datapoint in an answer) but the 100% citation claim has this caveat.
- **`contribution_margin` excludes ad cost:** CM = revenue − shipping − RTO only. Ad cost attribution per order requires UTM/click-id joining that the schema does not model. SKUs profitable on logistics costs but unprofitable on blended CAC will not be flagged by Margin Watch. The `_propose_adset_pause` (ROAS threshold) partially compensates at the campaign level.
- **Shiprocket token never auto-refreshes:** token expires ~10 days; ingest will fail with 401 until manually rotated in `.env`. Acceptable for demo; production needs a refresh-token flow.
- **No per-API-call `updated_at` comparison in ingest:** on conflict, the original payload is preserved unconditionally. If an order is genuinely updated at source, the warehouse will not reflect the correction until the raw record is manually purged.

