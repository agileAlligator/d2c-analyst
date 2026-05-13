# d2c-analyst

AI analyst + autonomous Margin Watch agent for D2C brands. Chat with your data. Get cited answers. Watch your margins.

On the seed merchant (80 orders, 30-day window), Margin Watch surfaces **~₹5,824/month** in actionable savings: ₹1,650 from a courier switch (Shadowfax→BlueDart), ₹4,122 from pausing underperforming campaigns (ROAS 1.35x, below the 2.0x threshold), and ₹52 from repricing two negative-margin SKUs.

---

## 1. What I built

**Connectors** pull from Shopify (orders, refunds, products), Meta Ads (campaigns, ad sets, insights), and Shiprocket (shipments, RTO events) into immutable `raw_*` tables in Postgres — payloads stored as JSONB with a `source_record_id` that never changes. A **normalizer** transforms those raw rows into a universal `entities + events + links` model with a first-class `provenance` table that maps every derived row back to the raw rows that produced it. A **chat agent** (GPT-4o preferred; GPT-4o / gpt-4o-mini via heuristic router when only an OpenAI key is present; tool-use loop) answers questions over typed metrics and sandboxed SQL; every number in the response is server-side-validated against provenance before the user sees it — uncited numbers are stripped or flagged, not passed through. A scheduled **Margin Watch agent** scans the warehouse, surfaces the top ₹-saving actions (courier switch, ad pause, price raise), and writes a run log with every claim cited back to a source row. It never calls a source API. The whole thing is keyed by `merchant_id` end-to-end — every table, every RLS policy, every tool call — so the path from 1 to 10 000 merchants is row-level isolation + worker sharding, not a rewrite.

```
                 ┌─────────────────────────────────────────┐
                 │            Streamlit UI                 │
                 └──────────────────┬──────────────────────┘
                                    │
                            ┌───────▼────────┐
                            │   Chat Server  │  tool-use loop (12 turns max)
                            │  + Validator   │  citation enforcement (2 retries)
                            │  + Router      │  gpt-4o-mini → gpt-4o cascade
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

## 2. Connectors — which 3, why these 3

| Connector | Why it earns its slot |
|---|---|
| **Shopify** (orders, refunds, products, customers) | Source of truth for revenue, COGS proxy, returns. No D2C analytics question is answerable without it. |
| **Meta Ads** (campaigns, ad sets, daily insights) | Spend dominates the P&L for early D2C. CAC and ROAS require it. Without it the "contribution margin" metric is just revenue minus logistics — missing half the cost. |
| **Shiprocket** | India-D2C reality: RTO (return-to-origin) rates of 20–35% silently destroy margin. Pairing logistics data with revenue + spend is what lets the Margin Watch agent find real rupees — the other two connectors alone cannot. |

Why not Google Ads (overlaps with Meta on the spend axis — diminishing returns for v0), Klaviyo (lifecycle is downstream of having the analytics first), or QuickBooks (accounting lags operational data by weeks).

**Shared abstraction:** `BaseConnector` (`app/connectors/base.py`) with sliding-window rate limiting, tenacity retry (5xx + 429 only — 401/404 surface immediately, not retried), cursor-based pagination, and `(merchant_id, source_record_id)` upsert idempotency. On re-ingest conflicts the payload is **preserved** — only `fetched_at` and `run_id` are updated. This protects provenance round-trips from partial API re-fetches. Adding a fourth connector is a ~250-LOC subclass.

**What we deliberately did not build:**

- **Write-back / action execution** — proposals are serialized with `"NOT_SENT": true`; turning proposals into actions requires a human-approval step that's out of scope for v0.
- **Real-time ingest** — polling every 15 min is sufficient for daily analytics. Webhooks would add operational complexity (SSL endpoint, dedup, backpressure) with no product benefit at this stage.
- **Multi-tenant auth / SSO** — merchant tokens live in `.env` or a secrets manager. A production auth layer (OAuth per merchant, token rotation) is a separate service.
- **Polished UI** — Streamlit is a scaffold. The brief explicitly de-emphasizes frontend; time spent there subtracts from citation and eval quality.
- **A fourth connector** — adding one would imply scope over taste. Three connectors that tell a coherent margin story beat four that compete.

---

## 3. Schema — why this shape

Two layers, never merged:

**Layer A — `raw_*` tables (immutable, append-only).** One table per connector resource. Stores the original JSON payload plus `source_record_id`, `fetched_at`, and `run_id`. This is the citation anchor — the raw payload is frozen at first-ingest time; re-runs update only `fetched_at` and `run_id`, never the payload.

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

## 4. Chat — tool schema and citation

**Six tools:**

| Tool | What it does |
|---|---|
| `query_metric(metric, group_by, time_range)` | Typed, pre-validated SQL for revenue, ad_spend, rto_rate, contribution_margin, cac. Returns rows + provenance IDs. |
| `sql(query)` | SELECT-only DuckDB against a sandboxed Postgres view. Returns rows + provenance bundle. Guard-railed via SQL parser; DuckDB `enable_external_access = false` + token blocklist prevent exfiltration via file-read functions or schema inspection calls. |
| `list_entities(type, filters, limit)` | Discover what's in the warehouse. |
| `get_raw(provenance_id)` | Let the model fact-check itself by reading the source payload. Enables round-trip from answer → citation → original JSON. |
| `compare(metric, period_a, period_b)` | Forces explicit delta with both citations — prevents the model from doing arithmetic without anchors. |
| `write_note(entity_id, text)` | The only write surface. Writes analyst annotations against any entity; never mutates source data. Intentionally scoped: action execution belongs to the agent, not the chat layer. |

The model defaults to `query_metric` (predictable, citation-trivial) and falls to `sql` only when the metric catalog isn't enough. `get_raw` exists so the model can verify a number before stating it.

**Citation enforcement is server-side, not prompt-only.** After every model response:
1. Parse all `<cite ref="ID">value</cite>` tags.
2. Validate each ref against the provenance table. Also cross-check the cited numeric value against the set of numbers the tools actually returned — a model that cites a real provenance ID but inflates the value is caught here.
3. Scan the remainder for bare numbers (≥ 2 digits). Any bare numbers are **replaced with `*(uncited)*`** in the output — they never reach the user unlabelled.
4. If issues: inject a correction prompt and retry (max 2 retries).
5. If still failing: render with an "⚠ unverified" badge rather than silently passing bad output.

**Worked rejection example:**

```
User:       "What was revenue last week?"

Model v1:   "Revenue was 82450 last week."
Validator:  bare number 82450 — no <cite> tag — stripped → replaced with *(uncited)*

Retry:      "The number 82450 was not cited. Re-answer with <cite> tags
             using a provenance ID returned by the tools."

Model v2:   "Revenue was <cite ref="order:5023">₹82,450</cite> last week."
Validator:  order:5023 → resolves in provenance table ✓ → passes
```

The system prompt says explicitly: *"Numbers without a `<cite>` tag will be stripped before the user sees them — so if you want a number to appear, you must cite it."* The validator enforces what the prompt promises.

**Model router** — applies to the OpenAI backend only. When `OPENAI_API_KEY` is present, all queries go to `gpt-4o` and the cascade is not needed (Claude 4.6 handles citation reliably without escalation). When only an OpenAI key is configured, a `HeuristicRouter` (`app/chat/routing/`) classifies every incoming query before the first API call and routes it to `gpt-4o-mini` (cheap, fast) or `gpt-4o` (smart). If the cheap model fails citation validation, a **FrugalGPT-style cascade** retries from scratch with `gpt-4o`.

Eight signals trigger escalation to `gpt-4o`:

| Signal | Example query |
|---|---|
| `length` | Query > 180 chars or > 30 tokens |
| `comparison` | *"Compare revenue last 7d vs 30d"* |
| `derived_metric` | *"What is our ROAS / CAC / contribution margin?"* |
| `causal` | *"Why is ROAS down this week?"* |
| `sql_escape` | *"Show me the top 5 orders by revenue"* |
| `multi_timerange` | Two or more date expressions in the query |
| `deep_turn` | Conversation turn ≥ 3 (late context is usually complex) |
| `negative_margin` | Phrase: "negative margin/contribution/ROAS" — *"Which orders had negative margin?"* |

Default (no signal fires) → `gpt-4o-mini`. Expected mix: ~55% cheap, ~45% smart. Blended cost ~$0.028/turn vs $0.05 all-4o (44% saving). The cascade adds ~5% wasted spend on retries; net saving ~45%.

Why this approach over alternatives: RouteLLM (trained classifier) requires labeled data we don't have at cold start. Commercial meta-routers (Martian, OpenRouter "auto") hide the routing logic. The citation validator is a free, deterministic verifier; using it as the cascade gate is the FrugalGPT pattern (Chen, Zaharia, Zou, Stanford 2023) applied to our specific quality signal.

---

## 5. Agent — what it does, why this one

**Why Margin Watch:** it is the only agent where all three connectors are load-bearing simultaneously — remove any one and the proposals degrade. The courier switch needs Shiprocket RTO data. The ad pause needs Meta spend. The price raise needs Shopify revenue *and* Shiprocket shipping cost to compute the actual margin. That cross-connector dependency is what makes it a real test of the universal model, not a single-source query with an LLM wrapper.

The ad-pause proposal on the seed merchant fires because ROAS is 1.35x — below the 2.0x threshold, and below break-even on a blended basis, but not a number a founder running on vibes would notice. They'd see "ads are running," not "ads are generating ₹1.35 for every ₹1 spent on ₹13,740 of ad spend, and pausing the bottom 30% by spend preserves higher-ROAS campaigns while recovering ~₹4,122." That cross-source calculation — spend from Meta, revenue attribution through discount codes, shipping cost from Shiprocket — is what the agent exists to do. On the seed merchant it surfaces ~₹5,824/month. At 10k merchants that compounds — that's the product.

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
Blended ROAS (14d): 1.35x (spend ₹13,740)

### 1. switch_courier — courier:Shadowfax
**Expected impact:** ₹1,650
**Reasoning:** Courier 'Shadowfax' has an RTO rate of 47.8% (11 returns in 30 days). Switching to 'BlueDart' (RTO rate 31.2%) could save ~₹1,650/month.
**Provenance:** shipment:8043, shipment:8034, shipment:8072, shipment:8075, shipment:8026
**Would-do API call:** {'connector': 'shiprocket', 'action': 'update_courier_preference', 'body': {'preferred_courier': 'BlueDart'}, 'NOT_SENT': True}

### 2. pause_adset — meta:all_campaigns
**Expected impact:** ₹4,122
**Reasoning:** Blended ROAS is 1.35x over the last 14 days (₹13,740 spend, ₹18,484 attributed revenue). Pausing the bottom 30% of campaigns by spend could save ~₹4,122 while preserving higher-ROAS campaigns.
**Provenance:** insight:camp_003:2026-05-04, insight:camp_003:2026-05-10, insight:camp_002:2026-05-06, insight:camp_002:2026-05-01, insight:camp_001:2026-05-11
**Would-do API call:** {'connector': 'meta_ads', 'endpoint': 'POST /{ad-set-id}', 'body': {'status': 'PAUSED'}, 'NOT_SENT': True}

### 3. raise_price — order:1027
**Expected impact:** ₹44
**Reasoning:** Order 1027 has contribution margin of ₹-43.70 (revenue ₹199.00, shipping ₹151.64, RTO cost ₹91.06). Raising the variant price from ₹199.00 to ₹242.70 (+₹43.70/unit × 1 units) would move this order to breakeven.

### 4. raise_price — order:1063
**Expected impact:** ₹8
**Reasoning:** Order 1063 has contribution margin of ₹-8.03 (revenue ₹199.00, shipping ₹101.13, RTO cost ₹105.90). Raising the variant price from ₹199.00 to ₹207.03 (+₹8.03/unit × 1 units) would move this order to breakeven.
```

---

## 6. Scale — 1 → 10,000 merchants

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
| LLM cost per chat turn | Linear with usage | Already built: heuristic router routes ~55% of queries to gpt-4o-mini; gpt-4o for synthesis only |
| Citation validation latency | Long answers (>500 tokens) | Move out of request path; stream and validate per-sentence with early rejection |
| Agent fan-out | 10k merchants × 6h = ~28k runs/day | Stagger by `hash(merchant_id) % 21600`; run against pre-materialized daily snapshots |
| Identity resolution hot path | As orders/links grow | Pre-compute candidates async; cache high-confidence links; floor at confidence 0.7 |

---

## 7. Eval — where it breaks

Run `make seed && make eval` with `OPENAI_API_KEY` set in `.env`.

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

**Citation enforcement: server-side, all 19 questions. Bare numbers replaced with `*(uncited)*` in output; unresolvable refs replaced with `*(unverified)*`. Accuracy: ~87% (core questions). P50: 2.3s. P95: 5.9s. Blended cost: ~$0.028/turn.**

*By construction, every number that reaches the user is either cited (valid provenance ID) or replaced with `*(uncited)*`/`*(unverified)*` — so the signal is "no bare numbers leaked," not "the model cited everything." All 19 answers returned only cited values; 2 adversarial questions received `⚠` badges on derived ratios with no dedicated provenance anchor.*

`*` = graceful "no data available" response. `⚠` = derived metric with no dedicated provenance anchor; validator retried but issued a ⚠ badge.

**Router accuracy (21 unit tests, 50 hand-labeled queries):**

| Tier | Traffic share | Correct routes | Cascade triggered |
|---|---|---|---|
| `gpt-4o-mini` (cheap) | ~55% | 92% | 8% escalate to 4o |
| `gpt-4o` (smart) | ~45% | 100% | — |

**A known failure — the validator catching a derived metric:**

```
User:     "What was revenue per Meta Ad click last 30 days?"

Model v1: "Revenue per click was ₹7,411 based on ₹37,053 revenue
           and 5 clicks tracked."
Validator: bare number 7411 — no <cite> tag — flagged
           bare number 37053 — no <cite> tag — flagged

Retry:    "Revenue was <cite ref='order:5001'>₹37,053</cite> and
           ad clicks were <cite ref='insight:camp_001:2026-04-15'>5</cite>.
           Computed revenue per click: ₹7,411 — no dedicated provenance anchor."
Validator: order:5001 → resolves ✓; 7411 still bare → ⚠ badge shown to user
```

Working as designed. The correct v1 fix: a `compute()` tool that returns a `computed:` provenance ID for arithmetic results, the same way `compare` already does for period deltas.

**Known failure modes:**

| Failure mode | Cause | Status |
|---|---|---|
| Computed ratios (revenue per click, CTR) | No native provenance anchor for arithmetic across two metrics | Known gap; `compute()` tool planned for v1 |
| Schema gaps (delivery time, organic attribution) | Events store business amounts only, not operational timestamps | By design; documented in system prompt |
| Multi-currency arithmetic | INR assumed throughout | Flagged, deferred |
| Bundles/multipacks | SKU margin allocation across variants | Proportional split, documented |
| UTM-less direct traffic | Meta attribution via discount codes (confidence 0.6) | Logged in `links` table |
| Refund timing windows | Refund in day 31 affects day-30 margin | Window closed at query time; documented |

**Merchant isolation (RLS) is tested directly:** `TestMerchantIsolation` queries demo (80 orders) and demo2 (5 orders) and asserts different revenue. If RLS breaks, this test fails.

---

## Running it

```bash
cp .env.example .env   # add OPENAI_API_KEY (preferred) or OPENAI_API_KEY; connector keys optional
                       # DEV_MODE=true is pre-set in .env.example for keyless local access
make bootstrap         # start db, install, seed demo+demo2, start api+ui
# UI at http://localhost:10002 — chat is live

make agent             # run Margin Watch once, prints proposals with ₹ impact
make eval              # citation + accuracy suite (needs LLM key)
pytest -q              # 94 tests (13 DB-gated, skipped without DATABASE_URL), offline, no API key
```

For production use, set `API_KEYS=your-secret-key:demo` in `.env` and pass `X-API-Key: your-secret-key` in API requests. `DEV_MODE=true` disables key enforcement for local development.

Ports: api `:10001`, ui `:10002`, db `:5434`

---

## 8. Hours spent

~48 hours across 6 build days (sessions of 6–10 hours each):

| Phase | Hours | Notes |
|---|---|---|
| Skeleton, Docker, CI | 3h | pyproject hatchling→setuptools switch cost 30min |
| Connector base + Shopify | 6h | Rate limiter, cursor pagination |
| Meta Ads + Shiprocket | 5h | Shiprocket token auth from `.env`, no OAuth needed |
| Universal schema + normalizers | 8h | Identity resolution, MD5 event IDs for idempotency |
| Typed metrics + SQL sandbox | 4h | DuckDB attach, GROUP BY template bugs |
| Chat tool-use loop + validator | 8h | Citation validator rewrite took most of this; validator now strips bare numbers (not just flags) and cross-checks cited values against tool results; DuckDB sandbox hardened (`enable_external_access=false`, extended token blocklist);  backend added (GPT-4o preferred, OpenAI fallback) |
| Margin Watch agent | 5h | Proposal contract, run log format |
| Scale harness + RLS hardening | 5h | Opus review found 12 bugs; all fixed |
| Eval suite + second merchant | 2h | Golden questions, scoreboard, RLS isolation test |
| Model router | 2h | HeuristicRouter, signals, cascade wiring, 21 tests |

---

## 9. What I'd do with another week

1. **Klaviyo connector** — lifecycle + churn data is the third axis missing from contribution margin. With email open rates + cohort revenue, the Margin Watch agent can distinguish "bad product" from "bad retention."
2. **Write-back actions with human approval** — the proposals exist; the missing piece is a one-click "apply" that constructs the API call, shows a diff, and requires explicit confirmation before sending. Not architecturally hard, just out of scope for v0.
3. **FX support** — a `fx_rates` table (ECB daily, cached) + a `to_inr(amount, currency, date)` function in DuckDB. Currently we assume INR and document the assumption; this makes it correct.
4. **Fuzzy identity resolution** — current Meta↔Shopify link is string-contains on discount code (confidence 0.6). A proper approach: embed product names + campaign names, cosine similarity, store candidates in `link_candidates` for async review.
5. **`compute()` tool** — returns a `computed:` provenance ID for arithmetic results, fixing the derived-metric citation gap (same pattern as the existing `compare` tool).
6. **CI eval gate** — `pytest tests/eval/` runs against a seeded test DB in CI with recorded LLM fixtures (no API cost), so regressions in citation coverage fail the build.
7. **A real frontend** — Streamlit is a scaffold. A Next.js UI with tool-call traces, a run log viewer, and a proposal inbox is the actual product surface.

---

## AI tools — per-module breakdown

Honest per-file breakdown:

| File / module | Human | LLM |
|---|---|---|
| `app/connectors/base.py` | Interface contract (rate limit, retry, cursor, upsert) | Implementation; I caught a bug where 401 was being retried instead of surfaced |
| `app/connectors/shopify.py` | Specified resources to pull and pagination strategy | Generated; human reviewed pagination cursor handling |
| `app/connectors/meta_ads.py` | Specified insight granularity (daily, not lifetime) | Generated from Meta API docs |
| `app/connectors/shiprocket.py` | Specified Bearer token auth pattern | Generated; auth from `.env` not OAuth was my decision |
| `app/warehouse/models.py` | Designed 4-table universal model + provenance contract | Generated SQLAlchemy models |
| `app/normalize/` | Specified event types, MD5 idempotency contract, join logic | Generated normalizers; I caught wrong join field (shopify_order_id vs order_number) |
| `app/warehouse/metrics/catalog.py` | Specified metrics and their semantics | Generated SQL templates; I fixed 3 bugs (GROUP BY 1=1, ARRAY_AGG NULLs, contribution margin join) |
| `app/chat/validator.py` | Specified server-side enforcement contract | Generated regex parser; I caught a logic error (bare-number scan was skipped when no cite issues present) |
| `app/chat/loop.py` | Specified tool schema, retry logic, routing contract, /OpenAI backend selection | Generated API calls and message threading; I added dual-backend dispatch and `for...else` MAX_TURNS guard |
| `app/chat/routing/` | Designed signals and cascade contract; chose FrugalGPT over RouteLLM (OpenAI path only) | Generated HeuristicRouter and signal regexes; I reviewed all 8 signals |
| `app/agents/margin_watch.py` | Specified 3 proposal types and NOT_SENT contract | Generated proposal logic; I verified ₹ impact calculations |
| `scripts/seed_demo_merchant.py` | Specified ground-truth values (₹37,053 30d revenue, Shadowfax 47.8% RTO) | Generated fixture data |
| `tests/eval/golden_questions.py` | Specified questions, ground-truth assertions, adversarial cases | Generated test bodies; I fixed 2 wrong assertions and added 3 adversarial questions |
| README | Wrote all prose | Drafted structure for some sections |

The division: the LLM fills implementations fast. The judgment — what to build, what the contracts mean, where the bugs are, which failures to document — stayed human throughout.
