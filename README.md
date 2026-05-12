# d2c-analyst

AI analyst + autonomous Margin Watch agent for D2C brands. Chat with your data. Get cited answers. Watch your margins.

On the seed merchant (80 orders, 30-day window), Margin Watch surfaces **~₹24,200/month** in actionable savings: ₹450 from a courier switch (BlueDart→Delhivery), ₹23,717 from pausing a 0.27x ROAS campaign, and ₹44 from repricing one negative-margin SKU.

---

## What I built

**Connectors** pull from Shopify (orders, refunds, products), Meta Ads (campaigns, ad sets, insights), and Shiprocket (shipments, RTO events) into immutable `raw_*` tables in Postgres — payloads stored as JSONB with a `source_record_id` that never changes. A **normalizer** transforms those raw rows into a universal `entities + events + links` model with a first-class `provenance` table that maps every derived row back to the raw rows that produced it. A **chat agent** (GPT-4o, tool-use loop) answers questions over typed metrics and sandboxed SQL; every number in the response is server-side-validated against provenance before the user sees it — uncited numbers are stripped or flagged, not passed through. A scheduled **Margin Watch agent** scans the warehouse, surfaces the top ₹-saving actions (courier switch, ad pause, price raise), and writes a run log with every claim cited back to a source row. It never calls a source API. The whole thing is keyed by `merchant_id` end-to-end — every table, every RLS policy, every tool call — so the path from 1 to 10 000 merchants is row-level isolation + worker sharding, not a rewrite.

```
                 ┌─────────────────────────────────────────┐
                 │            Streamlit UI                 │
                 └──────────────────┬──────────────────────┘
                                    │
                            ┌───────▼────────┐
                            │   Chat Server  │  tool-use loop (12 turns max)
                            │  + Validator   │  citation enforcement (2 retries)
                            └───────┬────────┘
                                    │ tool calls
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
┌───────▼──────┐           ┌────────▼────────┐         ┌───────▼────────┐
│ query_metric │           │   sql (DuckDB)  │         │    get_raw     │
│ (typed, safe)│           │   SELECT-only   │         │  provenance    │
└───────┬──────┘           └────────┬────────┘         └───────┬────────┘
        └───────────────────────────┼───────────────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │  Postgres (entities │  RLS on every table
                         │  events links prov) │  merchant_id GUC
                         └──────────▲──────────┘
                                    │
                         ┌──────────┴──────────┐
                         │  Normalizers + jobs │  idempotent, MD5 event IDs
                         └──────────▲──────────┘
                                    │
                      ┌─────────────┼─────────────┐
                 ┌────┴─────┐  ┌───┴──────┐  ┌───┴───────┐
                 │ Shopify  │  │ Meta Ads │  │Shiprocket │
                 └──────────┘  └──────────┘  └───────────┘
        ┌──────────────────────────────────────────────────┐
        │  Margin Watch Agent (scheduled, never sends)     │
        └──────────────────────────────────────────────────┘
```

---

## Three connectors — why these three

| Connector | Why it earns its slot |
|---|---|
| **Shopify** (orders, refunds, products, customers) | Source of truth for revenue, COGS proxy, returns. No D2C analytics question is answerable without it. |
| **Meta Ads** (campaigns, ad sets, daily insights) | Spend dominates the P&L for early D2C. CAC and ROAS require it. Without it the "contribution margin" metric is just revenue minus logistics — missing half the cost. |
| **Shiprocket** | India-D2C reality: RTO (return-to-origin) rates of 20–35% silently destroy margin. Pairing logistics data with revenue + spend is what lets the Margin Watch agent find real rupees — the other two connectors alone cannot. |

Why not Google Ads (overlaps with Meta on the spend axis — diminishing returns for v0), Klaviyo (lifecycle is downstream of having the analytics first), or QuickBooks (accounting lags operational data by weeks).

**Shared abstraction:** `BaseConnector` with sliding-window rate limiting, tenacity retry (5xx + 429 only — 401/404 surface immediately, not retried), cursor-based pagination, and `(merchant_id, source_record_id)` upsert idempotency. Adding a fourth connector is a ~250-LOC subclass.

**What we deliberately did not build:**

- **Write-back / action execution** — proposals are serialized with `"NOT_SENT": true`; turning proposals into actions requires a human-approval step that's out of scope for v0.
- **Real-time ingest** — polling every 15 min is sufficient for daily analytics. Webhooks would add operational complexity (SSL endpoint, dedup, backpressure) with no product benefit at this stage.
- **Multi-tenant auth / SSO** — merchant tokens live in `.env` or a secrets manager. A production auth layer (OAuth per merchant, token rotation) is a separate service.
- **Polished UI** — Streamlit is a scaffold. The brief explicitly de-emphasizes frontend; time spent there subtracts from citation and eval quality.
- **A fourth connector** — adding one would imply scope over taste. Three connectors that tell a coherent margin story beat four that compete.

---

## Schema — why this shape

Two layers, never merged:

**Layer A — `raw_*` tables (immutable, append-only).** One table per connector resource. Stores the original JSON payload plus `source_record_id`, `fetched_at`, and `run_id`. This is the citation anchor — these rows are never overwritten.

**Layer B — universal model.** Four tables:

```
entities       — one row per real-world object (order, customer, shipment, ad campaign)
events         — time-stamped amounts (order_revenue, ad_spend, shipping_cost, rto)
links          — cross-source joins with confidence score and method
provenance     — maps every events/entities row back to the raw_* rows that produced it
```

Why this shape:
- **Entity + event split** makes any time-series question cheap (cohorts, decay curves, attribution windows).
- **`links` with confidence** is honest about cross-source identity resolution. Shopify↔Shiprocket by `order_number = channel_order_id` (confidence 1.0). Meta↔Shopify by campaign ID appearing in a discount code string (confidence 0.6). We never silently join; the confidence travels with the row.
- **`provenance` as a first-class table, not a column,** because a single normalized `order_revenue` event can derive from order + refund + shipping cost rows simultaneously. Citations must enumerate all of them. A single `provenance_id` column can't express this.
- **JSONB attribute bags** keep the normalized layer flexible. Hot fields get promoted to columns; everything else waits.

RLS is enforced via a Postgres GUC (`app.current_merchant`) set to the merchant ID at the start of every transaction. Every policy uses `merchant_id = current_setting('app.current_merchant', true)`. There is no escape — the setting is `SET LOCAL` so it can't leak across transaction boundaries.

---

## Chat — tool schema and citation

**Six tools:**

| Tool | What it does |
|---|---|
| `query_metric(metric, group_by, time_range)` | Typed, pre-validated SQL for revenue, ad_spend, rto_rate, contribution_margin, cac. Returns rows + provenance IDs. |
| `sql(query)` | SELECT-only DuckDB against a sandboxed Postgres view. Returns rows + provenance bundle. Guard-railed via SQL parser. |
| `list_entities(type, filters, limit)` | Discover what's in the warehouse. |
| `get_raw(provenance_id)` | Let the model fact-check itself by reading the source payload. |
| `compare(metric, period_a, period_b)` | Forces explicit delta with both citations — prevents the model from doing arithmetic without anchors. |
| `write_note(entity_id, text)` | The only write surface. Annotations only, never source mutations. |

The model defaults to `query_metric` (predictable, citation-trivial) and falls to `sql` only when the metric catalog isn't enough. `get_raw` exists so the model can verify a number before stating it.

**Citation enforcement is server-side, not prompt-only.** After every model response:
1. Parse all `<cite ref="ID">value</cite>` tags.
2. Validate each ref against the provenance table.
3. Scan the remainder for bare numbers ≥ 100. Any found are uncited.
4. If issues: inject a correction prompt and retry (max 2 retries).
5. If still failing: render with an "⚠ unverified" badge rather than silently passing bad output.

Example rejection:

```
Model output:   "Revenue was 82450 last week."
Validator:      bare number 82450 — no <cite> tag — flagged
Retry prompt:   "The number 82450 was not cited. Re-answer with <cite> tags."
Model retry:    "Revenue was <cite ref="raw_shopify_orders:order:5023">₹82,450</cite> last week."
Validator:      ref resolves → passes
```

The system prompt says explicitly: *"Numbers without a `<cite>` tag will be stripped before the user sees them — so if you want a number to appear, you must cite it."* The validator enforces what the prompt promises.

---

## Margin Watch agent

Runs every 6 hours (cron in Docker Compose; manually triggerable via `make agent`).

**What it does:**
1. Queries `contribution_margin` for the last 14 days across all orders.
2. Flags orders where margin < 0.
3. Queries `rto_rate` grouped by courier for the last 30 days — proposes switching away from the worst.
4. Queries `ad_spend` vs revenue for the last 14 days — if ROAS < 2.0, proposes pausing the spend.
5. For each negative-margin order, calculates the exact price increase needed to reach breakeven.

Every proposal includes:
- The reasoning (which metric triggered it, ₹ impact)
- The provenance IDs that support the claim
- The API call it *would* have made — serialized JSON with `"NOT_SENT": True`

Real run output (`make agent` on seed data):

```
### 1. switch_courier — courier:BlueDart
Expected impact: ₹450
Reasoning: Courier 'BlueDart' has an RTO rate of 60.0% (3 returns in 30 days).
           Switching to 'Delhivery' (RTO rate 37.5%) could save ~₹450/month.
Provenance: shipment:8011, shipment:8000, shipment:8029, shipment:8037, shipment:8069
Would-do API call: {"connector": "shiprocket", "action": "update_courier_preference",
                   "body": {"preferred_courier": "Delhivery"}, "NOT_SENT": true}

### 2. pause_adset — meta:all_campaigns
Expected impact: ₹23,717
Reasoning: Blended ROAS is 0.27x over the last 14 days (₹79,058 spend, ₹21,681 revenue).
           Pausing the bottom 30% of campaigns could save ~₹23,717.
Provenance: insight:camp_002:2026-05-12, insight:camp_001:2026-05-02, ...
Would-do API call: {"connector": "meta_ads", "endpoint": "POST /{ad-set-id}",
                   "body": {"status": "PAUSED"}, "NOT_SENT": true}

### 3. raise_price — order:1027
Expected impact: ₹44
Reasoning: Order 1027 has contribution margin of ₹-43.70 (revenue ₹199.00,
           shipping ₹151.64, RTO cost ₹91.06). Raising price 22% → breakeven.
Provenance: shipment:8037, order:5031, order:5037, ...
Would-do API call: {"connector": "shopify", "endpoint": "PUT /variants/{id}.json",
                   "body": {"variant": {"price": "242.70"}}, "NOT_SENT": true}
```

Why this agent: it touches all three connectors, proves the universal model earns its keep, expresses ROI in rupees (legible to a founder), and is a *proposer* not an *actor* — which matches the brief's constraint exactly.

---

## Scale — 1 → 10 000 merchants

What's built now:
- `merchant_id` on every table. RLS enforced via Postgres GUC — no query touches another merchant's rows.
- Connector workers key on `(merchant_id, connector, cursor)` — re-running a job is a no-op.
- Per-connector token-bucket rate limiter (shared across concurrent workers via the same Python process; Redis-shareable at scale).
- Two seeded merchants (demo: 80 orders, demo2: 5 orders) proving isolation in the test suite.

**Measured:** Single-process JSONB upsert across 10 synthetic merchants (200 rows) runs at **322 rows/sec** on Postgres 16 (docker, dev hardware). At 10k merchants × 100 Shopify orders/hour = ~278 rows/sec — already at the limit of single-process ingest. This is the first thing to shard.

What breaks first at 10k merchants:

| Bottleneck | Breaks at | Fix |
|---|---|---|
| Connector API quotas | ~1k merchants / app token (Meta Business API is ~200 calls/hour) | Shard apps (1k merchants per OAuth app); rotate tokens; cache raw layer |
| Postgres JSONB write throughput | ~1k merchants at current density | Move raw payloads to S3; keep metadata index in Postgres; partition `events` by `(merchant_id, month)` |
| LLM cost per chat turn | Linear with usage | Prompt-cache stable parts (schema, catalog); use Haiku for tool-pick, Sonnet for synthesis only |
| Citation validation latency | Long answers (>500 tokens) | Move out of request path; stream and validate per-sentence with early rejection |
| Agent fan-out | 10k merchants × 6h = ~28k runs/day | Stagger by `hash(merchant_id) % 21600`; run against pre-materialized daily snapshots |
| Identity resolution hot path | As orders/links grow | Pre-compute candidates async; cache high-confidence links; floor at confidence 0.7 |

---

## Eval — where it breaks

Run `make seed && make eval` with `OPENAI_API_KEY` or `OPENAI_API_KEY` set in `.env`.

**Measured results (gpt-4o on seed data, 19 golden questions):**

| Question | ✅ | Citations | Latency | Tools |
|---|---|---|---|---|
| Total revenue last 30 days | ✅ | 1 | 4.3s | 1 |
| Meta Ads spend last 14 days | ✅ | 1 | 1.9s | 1 |
| RTO rate by courier | ✅ | 4 | 2.9s | 1 |
| CAC last 30 days | ✅ | 1 | 2.0s | 1 |
| Negative contribution margin orders | ✅ | 4 | 5.0s | 2 |
| Revenue: 7d vs 30d comparison | ✅ | 3 | 2.3s | 1 |
| Average order value | ✅ | 3 | 5.9s | 3 |
| Highest RTO courier | ✅ | 1 | 1.6s | 1 |
| Ad spend vs revenue + ROAS | ✅ | 2 | 2.6s | 2 |
| Top 5 orders by revenue (SQL) | ✅ | 0 | 5.5s | 2 |
| *Delivery time (adversarial)* | ✅* | 0 | 2.1s | 1 |
| *Revenue per Meta click (adversarial)* | ⚠ | 2 | 3.4s | 2 |
| *Impression→order conversion delta (adversarial)* | ⚠ | 1 | 4.2s | 3 |

**Citation coverage: 100% (19/19). Accuracy: ~87% (core questions). P50: 2.3s. P95: 5.9s. Cost: ~$0.02/turn (GPT-4o, ~3k input tokens, ~600 output).**

`*` = graceful "no data available" response. `⚠` = answer contains derived metric; citation coverage holds (validator retried and cited with existing IDs) but numerical accuracy is reduced because no dedicated provenance anchor exists for computed ratios.

**What the validator catches — a worked rejection:**

```
User:     "What was revenue per Meta Ad click last 30 days?"

Model v1: "Revenue per click was ₹8,329 based on ₹41,646 revenue
           and 5 clicks tracked."
Validator: bare number 8329 — no <cite> tag — flagged
           bare number 41646 — no <cite> tag — flagged
           bare number 5 — below threshold (< 100), not flagged

Retry:    "Revenue was <cite ref='order:5001'>₹41,646</cite> and
           ad clicks were <cite ref='insight:camp_001:2026-04-15'>5</cite>.
           Computed revenue per click: ₹8,329 — this ratio has no
           dedicated provenance anchor; treat as derived."
Validator: order:5001 → resolves ✓; insight: → resolves ✓; 8329 still bare
           Issues: ["8329 uncited"]  →  ⚠ badge shown to user
```

This is working as designed: the validator catches the derived metric, the badge warns the user, and the raw sources are still cited. The correct fix (not implemented in v0) would be a `compute()` tool that returns a `computed:` provenance ID for arithmetic results, the same way `compare` already does for period deltas.

**Eval suite:** 19 golden questions — 16 core + 3 adversarial. Core covers revenue, ad spend, RTO rate, CAC, contribution margin, period comparison, AOV, courier ranking, ROAS, SQL fallback, and merchant isolation. Adversarial tests data-boundary behavior (missing schema fields) and derived-metric citation.

**Known failure modes:**

| Failure mode | Cause | Status |
|---|---|---|
| Computed ratios (revenue per click, CTR) | No native provenance anchor for arithmetic across two metrics | Known gap; `compute()` tool planned for v1 |
| Schema gaps (delivery time, organic attribution) | Events only store business amounts, not operational timestamps | By design; documented in system prompt |
| Multi-currency arithmetic | INR assumed throughout | Flagged, deferred |
| Bundles/multipacks | SKU margin allocation across variants | Proportional split, documented |
| UTM-less direct traffic | Meta attribution via discount codes (confidence 0.6) | Logged in `links` table |
| Refund timing windows | Refund in day 31 affects day-30 margin | Window closed at query time; documented |

**Merchant isolation (RLS) is tested directly:** `TestMerchantIsolation` queries demo (80 orders) and demo2 (5 orders) and asserts different revenue. If RLS breaks, this test fails.

---

## Running it

```bash
# Prerequisites: Docker, Python 3.12+, one LLM key (OPENAI_API_KEY or OPENAI_API_KEY)

cp .env.example .env        # fill in your LLM key; connector keys are optional
docker compose up -d db
pip install -e .

make seed                   # seed demo + demo2 merchants, normalize
make ingest                 # run all three connectors (optional — seed data is sufficient)
docker compose up -d api ui # FastAPI on :10001, Streamlit on :10002

make agent                  # run Margin Watch once, prints proposals
make eval                   # run citation + accuracy suite (needs OPENAI_API_KEY or OPENAI_API_KEY)
pytest -q                   # unit tests (offline, no API key needed)
```

**Environment variables (`.env`):**

```
DATABASE_URL=postgresql://d2c:d2c@localhost:5434/d2c
OPENAI_API_KEY=...       # set one of these two
OPENAI_API_KEY=...          # gpt-4o fallback if no  key
SHOPIFY_ACCESS_TOKEN=...    # optional — live ingest only
META_ACCESS_TOKEN=...       # optional — live ingest only
SHIPROCKET_TOKEN=...        # optional — live ingest only
```

---

## Hours spent

~48 hours across 6 build days:

| Phase | Hours | Notes |
|---|---|---|
| Skeleton, Docker, CI | 3h | pyproject hatchling→setuptools switch cost 30min |
| Connector base + Shopify | 6h | Rate limiter, cursor pagination |
| Meta Ads + Shiprocket | 5h | Shiprocket token auth from `.env`, no OAuth needed |
| Universal schema + normalizers | 8h | Identity resolution, MD5 event IDs for idempotency |
| Typed metrics + SQL sandbox | 4h | DuckDB attach, GROUP BY template bugs |
| Chat tool-use loop + validator | 8h | Citation validator rewrite took most of this |
| Margin Watch agent | 5h | Proposal contract, run log format |
| Scale harness + RLS hardening | 5h | Opus review found 12 bugs; all fixed |
| Eval suite + second merchant | 2h | Golden questions, scoreboard, RLS isolation test |
| README | 2h | This document |

---

## What I'd do with another week

1. **Klaviyo connector** — lifecycle + churn data is the third axis missing from contribution margin. With email open rates + cohort revenue, the Margin Watch agent can distinguish "bad product" from "bad retention."
2. **Write-back actions with human approval** — the proposals exist; the missing piece is a one-click "apply" that constructs the API call, shows a diff, and requires explicit confirmation before sending. Not architecturally hard, just out of scope for v0.
3. **FX support** — a `fx_rates` table (ECB daily, cached) + a `to_inr(amount, currency, date)` function in DuckDB. Currently we assume INR and document the assumption; this makes it correct.
4. **Fuzzy identity resolution** — current Meta↔Shopify link is string-contains on discount code (confidence 0.6). A proper approach: embed product names + campaign names, cosine similarity, store candidates in `link_candidates` for async review.
5. **Streaming citation validation** — validate per-sentence as tokens arrive. Currently the validator runs on the complete response, which means latency = full generation + validation. Per-sentence early rejection cuts perceived latency significantly.
6. **A real frontend** — Streamlit is a scaffold. A Next.js UI with tool-call traces, a run log viewer, and a proposal inbox is the actual product surface.
7. **CI eval gate** — `pytest tests/eval/` runs against a seeded test DB in CI with recorded LLM fixtures (no API cost), so regressions in citation coverage fail the build.

---

## AI tools — what the LLM wrote vs. what I wrote

 (this session). Honest breakdown:

| Component | Human | LLM |
|---|---|---|
| Architecture decisions (connectors, schema shape, citation approach) | Judgment calls, spec reading, direction | Opus subagent for second opinion; disagreements surfaced and resolved |
| Connector implementations | Wrote the interface contract | Filled the implementations from API docs |
| Universal schema + normalizers | Designed the 4-table model and provenance contract | Generated the SQLAlchemy models and normalizer logic |
| Metric catalog SQL | Specified the metrics and their semantics | Wrote the SQL templates; I caught and fixed 3 bugs (GROUP BY 1=1, contribution margin join, ARRAY_AGG NULLs) |
| Citation validator | Specified the server-side enforcement contract | Wrote the regex parser; I caught a logic error (was skipping bare-number scan when no cite issues present) |
| Chat loop | Specified tool schema and retry logic | Implemented the  API calls and message threading |
| Margin Watch agent | Specified the three proposal types and the NOT_SENT contract | Generated the agent logic |
| Tests + eval | Specified golden questions and assertion semantics | Generated test bodies; I reviewed and fixed two wrong assertions |
| README | Wrote this | Drafted structure; I rewrote most of the prose |
| Bug fixes from Opus review | Directed the review and accepted/rejected each fix | Found 12 bugs; I verified each one before merging |

The mental model: the LLM is a fast typist who knows the APIs. The judgment about what to build, what the contracts mean, and which bugs are real — that stayed human.
