Run the adversarial hardening loop on this codebase until Opus reviewers say CLEAN.

**Loop structure:**

## Step 1 — Audit (5 Opus agents in parallel)

Spawn 5 Opus subagents simultaneously, each covering a non-overlapping slice:

1. **Connectors + ingest** — `app/connectors/`, `app/ingest/`, `tests/connectors/`, `tests/fixtures/`, `tests/test_ingest_idempotent.py`. Check: base-class contract, pagination correctness, retry bounds, idempotency, cursor advancement, fixture/connector field alignment.

2. **Chat + citation validator** — `app/chat/`, `tests/chat/`, `tests/api/`. Check: bare-number regex correctness (comma-formatted, year-like, date-period exemptions), retry/cascade wiring, tool-result JSON serialization, system prompt contradictions, model router signal accuracy.

3. **Warehouse + metrics + agents** — `app/warehouse/`, `app/agents/`, `app/normalize/`, `app/identity/`, `scripts/seed_*.py`. Check: RLS enforcement (NullPool + after_begin + set_merchant on every path), per-row provenance (.get not .pop), DuckDB sandbox isolation, metric catalog completeness and GROUP BY correctness, normalizer MD5 event-ID idempotency, agent thresholds from config (not hardcoded), NOT_SENT enforced on all proposals, seed determinism (BASE_DATE anchor, rand_date not datetime.now).

4. **Test suite** — `tests/` (excluding `tests/eval/`). Check: assertions verify actual content not just "doesn't crash", semantic correctness of each assertion, RLS isolation test exists, citation validator catches uncited numbers, idempotency tests test the runner (not just Postgres upserts directly), fixture/connector field alignment.

5. **Infra + docs** — `docker-compose.yml`, `Makefile`, `.github/workflows/`, `pyproject.toml`, `app/config.py`, `README.md`, `STATUS.md`, `docs/`, `.env.example`. Check: bootstrap works on fresh clone (`docker compose up && make seed && make agent`), CI runs lint + pytest + skips eval on dummy key, all README claims are true, docs/ numbering sequential, wont_fix.md covers known gaps.

Each agent prompt must say: "Report file:line for each actual bug, silent failure, broken contract, or spec mismatch. Do NOT flag style. If your slice is clean, say CLEAN."

## Step 2 — Triage

Classify every finding:
- **Critical** — wrong output, broken fresh-clone path, correctness bug affecting demo
- **Medium** — real bug, lower blast radius
- **Low** — cosmetic, dead code, latent risk
- **Wont-Fix** — correct in principle but deferred (idempotent at demo scale, requires live API, etc.)

## Step 3 — Fix (Sonnet agents in parallel)

Group Critical + Medium + Low fixes by non-overlapping file sets. Spawn one Sonnet agent per group. Each agent must read every file before editing. Rules:
- Never hardcode values that can be derived dynamically (CLAUDE.md §HARDCODING)
- If a fix is genuinely too hard, add it to `docs/wont_fix.md` instead
- Add Wont-Fix items to `docs/wont_fix.md` with a reason

## Step 4 — Review (3 Opus agents in parallel)

Spawn 3 Opus code reviewers:
1. Review changed source files — verify fixes are correct and complete
2. Review changed tests — verify assertions are meaningful
3. Review README + docs — verify all claims remain accurate

Each reviewer: "Report file:line for any remaining issues. If your slice is clean, say CLEAN."

## Step 5 — Loop or commit

- If any reviewer found real issues → go to Step 3 with those specific findings
- If all 3 say CLEAN → commit all changes with a descriptive message, push to origin, update STATUS.md phase

**Hard rules throughout:**
- Do not fix issues already in `docs/wont_fix.md`
- Do not produce style-only commits
- STATUS.md is always the last file updated
