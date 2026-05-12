# Project Status

**Last updated:** 2026-05-12
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
| Chat tool-use loop | ✅ | 6 tools, gpt-4o, 12-turn max, json serialization fixed |
| Citation validator | ✅ | server-side, always scans bare numbers ≥100, 2 retries, unverified badge fallback |
| Margin Watch agent | ✅ | courier switch, ad pause, price raise proposals; NOT_SENT enforced |
| Streamlit UI | ✅ | chat + tool call trace |
| FastAPI | ✅ | /chat, /runs, /health |
| Seed data | ✅ | demo: 80 orders, 30 days Meta, 80 shipments; demo2: 5 orders for RLS isolation |
| RLS hardening | ✅ | SET LOCAL GUC, no IS NULL escape, called at ingest + tools + agent |
| Eval suite | ✅ | 10 golden questions, citation coverage, merchant isolation test; target ≥80% |
| CI | ✅ | GitHub Actions: lint + pytest |
| README | ✅ | All 9 brief questions answered |

## Key decisions

- **Connectors:** Shopify, Meta Ads, Shiprocket — see MASTER_PLAN.md §2.1
- **Schema:** raw (immutable) + universal (entities+events+links) + provenance as first-class table
- **Citation:** server-side validation, not prompt-only — every number resolved against provenance
- **Agent:** Margin Watch — proposes courier switch, ad pause, price raise; never executes (NOT_SENT: True)
- **Currency:** INR throughout (assumption documented in README)
- **Auth:** Shiprocket SHIPROCKET_TOKEN from .env; Shopify private app; Meta long-lived token
- **Idempotency:** MD5 event IDs keyed on (merchant_id, entity_id, event_type, occurred_at)

## Bugs fixed in Opus review (Phase 7)

1. GROUP BY 1=1 invalid SQL in ungrouped metric queries → empty string
2. Contribution margin join on wrong field (shopify_order_id ≠ channel_order_id) → order_number
3. ARRAY_AGG includes NULL provenance IDs from LEFT JOIN → FILTER WHERE IS NOT NULL
4. RLS never enforced (set_merchant not called) → added to ingest, tools, agent
5. Event duplication on re-normalization → deterministic MD5 event IDs + on_conflict_do_update
6. Shiprocket constraint name mismatch → explicit (model_cls, constraint_name) tuples in RAW_MODEL_MAP
7. Citation validator too lenient (skipped bare-number scan) → always scans
8. Tool result as Python repr (str(result)) → json.dumps(result, default=str)
9. Compare tool wrong value column → METRIC_VALUE_COL dict
10. Retry on 4xx (401/404) → retry_if_exception with status code check
11. Meta↔Shopify N×M noise → only link when campaign_id in discount code
12. RLS IS NULL escape → removed, kept empty-string path for migration only
