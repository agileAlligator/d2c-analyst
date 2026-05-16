Run the adversarial hardening loop on this codebase. Repeat until all Opus reviewers say CLEAN.

## Step 1 — Map the codebase

Before spawning any agents, read the top-level directory structure and identify the major slices. A typical split for a Python/backend project:

- Source modules (the main application code)
- Tests (unit, integration, eval)
- Infrastructure (Docker, CI, Makefile, config files)
- Documentation (README, docs/, changelogs)

Adjust slices based on what's actually here. Aim for 4–6 non-overlapping groups where each can be audited independently.

## Step 2 — Audit (Opus agents in parallel, one per slice)

Spawn one Opus agent per slice simultaneously. Each agent gets a prompt like:

> "Deep audit of [slice paths]. You are looking for: correctness bugs, silent failures, broken contracts, dead code, spec mismatches, false claims in docs. NOT style nits. Report file:line for each actual issue. If clean, say CLEAN."

Good things to check per slice type:
- **Source code**: logic bugs, silent data loss, unbounded loops, hardcoded values that should be config, deprecated APIs, error paths that don't clean up state, dead code
- **Tests**: assertions that only check "doesn't crash", tests that don't exercise the code they claim to cover, missing coverage for critical invariants (idempotency, isolation, auth)
- **Infra/config**: broken fresh-clone bootstrap, wrong values in example files, CI that skips important checks, docker-compose/env inconsistencies
- **Docs**: claims that contradict the code, missing limitations, stale numbers, broken instructions

## Step 3 — Triage

Classify every finding:
- **Critical** — wrong output, broken bootstrap, correctness bug visible in the demo
- **Medium** — real bug, lower blast radius
- **Low** — dead code, cosmetic, latent risk
- **Wont-Fix** — real limitation but intentionally deferred; document rather than fix

Check `docs/wont_fix.md` (or equivalent) before classifying — don't re-fix things already accepted as deferred.

## Step 4 — Fix (Sonnet agents in parallel)

Group fixes by non-overlapping file sets. Spawn one Sonnet agent per group. Each agent's prompt must include:
- The specific file:line and what is wrong
- What the correct behaviour should be
- The rule: read every file before editing; never hardcode values that can be derived dynamically; if a fix is genuinely too hard, add it to the wont-fix list instead

Add Wont-Fix items to `docs/wont_fix.md` (create it if absent) with: what the issue is, why it's deferred, and what a production fix would look like.

## Step 5 — Review (3 Opus agents in parallel)

Spawn 3 Opus reviewers covering:
1. Changed source files — are the fixes correct and complete?
2. Changed tests — are the assertions meaningful?
3. Changed docs/README — are all claims still accurate?

Each reviewer: "Report file:line for any remaining issues. If your slice is clean, say CLEAN."

## Step 6 — Loop or commit

- Any reviewer found real issues → go to Step 4 with those specific findings
- All 3 say CLEAN → commit all staged changes with a descriptive message, push to origin, update the project's status/changelog file

## Rules (apply throughout)

- Fix issues in priority order: Critical → Medium → Low
- Never skip a fix by hardcoding around it
- Never fix something already in the wont-fix list
- Every Sonnet fix agent must read files before editing
- STATUS.md (or equivalent project status file) is always the last file updated
- Stop the loop only when Opus reviewers collectively find nothing
