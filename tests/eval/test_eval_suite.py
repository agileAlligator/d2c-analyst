"""Eval suite — citation coverage and accuracy against the seeded demo merchant.

Requires a live Postgres with seed data: make seed && make normalize
Skipped when neither OPENAI_API_KEY nor OPENAI_API_KEY is set (or both are 'dummy').

Run with: pytest tests/eval/ -v -s
"""
import json
import os
import time
from pathlib import Path

import pytest

# Load .env so keys are available when running pytest directly
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
except ImportError:
    pass

_LLM_KEY_CHECK = os.getenv("OPENAI_API_KEY", "dummy")
_OPENAI = os.getenv("OPENAI_API_KEY", "dummy")
SKIP_REASON = "No live LLM API key — set OPENAI_API_KEY or OPENAI_API_KEY to run eval"
SKIP = (
    (_LLM_KEY_CHECK == "dummy" or not _LLM_KEY_CHECK) and
    (_OPENAI == "dummy" or not _OPENAI)
)

# Eval results will be written here for the scoreboard
RESULTS_FILE = Path(__file__).parent / "eval_results.json"


def _requires_live():
    return pytest.mark.skipif(SKIP, reason=SKIP_REASON)


@_requires_live()
class TestCitationCoverage:
    """Run golden questions and measure citation coverage."""

    @pytest.fixture(autouse=True)
    def setup(self):

        from app.chat.validator import CITE_RE
        from app.warehouse.db import SessionLocal
        self.SessionLocal = SessionLocal
        self.CITE_RE = CITE_RE
        self.results = []

    def _run_question(self, question: str, merchant_id: str = "demo") -> dict:
        from app.chat.loop import run_chat
        start = time.time()
        with self.SessionLocal() as db:
            result = run_chat(question, db, merchant_id)
        elapsed = time.time() - start

        answer = result["answer"]
        cited_numbers = self.CITE_RE.findall(answer)
        all_valid = result["all_citations_valid"]

        return {
            "question": question,
            "answer": answer,
            "all_citations_valid": all_valid,
            "issues": result["issues"],
            "cited_count": len(cited_numbers),
            "tool_calls": len(result["tool_calls"]),
            "provenance_ids_count": len(result["provenance_ids"]),
            "latency_s": round(elapsed, 2),
        }

    def test_revenue_citation(self):
        r = self._run_question("What was total revenue in the last 30 days?")
        assert r["all_citations_valid"], f"Citation issues: {r['issues']}\nAnswer: {r['answer']}"
        assert r["cited_count"] >= 1, "Expected at least one cited number in revenue answer"

    def test_ad_spend_citation(self):
        r = self._run_question("How much did we spend on Meta Ads in the last 14 days?")
        assert r["all_citations_valid"], f"Citation issues: {r['issues']}"

    def test_rto_rate_citation(self):
        r = self._run_question("What is our RTO rate by courier?")
        assert r["all_citations_valid"], f"Citation issues: {r['issues']}"

    def test_cac_citation(self):
        r = self._run_question("What is our customer acquisition cost in the last 30 days?")
        assert r["all_citations_valid"], f"Citation issues: {r['issues']}"

    def test_contribution_margin(self):
        r = self._run_question("Which orders had negative contribution margin last month?")
        # May have 0 results if all margins positive — just check citations valid
        assert r["all_citations_valid"], f"Citation issues: {r['issues']}"

    def test_merchant_isolation(self):
        """RLS: demo2 should see only its own data, not demo's."""
        r_demo = self._run_question("What was total revenue in the last 90 days?", merchant_id="demo")
        r_demo2 = self._run_question("What was total revenue in the last 90 days?", merchant_id="demo2")
        # demo has 80 orders, demo2 has 5 — revenue should differ
        # We can't compare exact numbers without seeded data, but answers should differ
        assert r_demo["answer"] != r_demo2["answer"], (
            "demo and demo2 got identical answers — RLS isolation may be broken"
        )


@_requires_live()
class TestFullEvalSuite:
    """Run all 10 golden questions and produce a scoreboard."""

    def test_full_scoreboard(self):
        import time

        from app.chat.loop import run_chat
        from app.chat.validator import CITE_RE
        from app.warehouse.db import SessionLocal
        from tests.eval.golden_questions import GOLDEN_QUESTIONS

        results = []
        total_cited = 0
        total_questions = len(GOLDEN_QUESTIONS)
        valid_count = 0

        for gq in GOLDEN_QUESTIONS:
            start = time.time()
            with SessionLocal() as db:
                result = run_chat(gq.question, db, "demo")
            elapsed = time.time() - start

            answer = result["answer"]
            cited = CITE_RE.findall(answer)
            valid = result["all_citations_valid"]
            if valid:
                valid_count += 1
            total_cited += len(cited)

            row = {
                "question": gq.question[:60],
                "description": gq.description,
                "valid": valid,
                "cited_count": len(cited),
                "issues": result["issues"],
                "latency_s": round(elapsed, 2),
                "tool_calls": len(result["tool_calls"]),
            }
            results.append(row)
            print(f"\n{'✅' if valid else '❌'} {gq.question[:60]}")
            print(f"   Cited: {len(cited)} | Latency: {elapsed:.1f}s | Tools: {len(result['tool_calls'])}")
            if result["issues"]:
                print(f"   Issues: {result['issues']}")

        citation_coverage = valid_count / total_questions * 100
        print(f"\n{'='*60}")
        print(f"SCOREBOARD: {valid_count}/{total_questions} questions fully cited ({citation_coverage:.0f}%)")
        print(f"Total cited numbers: {total_cited}")

        # Write results for the README
        RESULTS_FILE.write_text(json.dumps({
            "citation_coverage_pct": citation_coverage,
            "valid_count": valid_count,
            "total_questions": total_questions,
            "total_cited_numbers": total_cited,
            "results": results,
        }, indent=2))

        # Target: ≥ 80% citation coverage
        assert citation_coverage >= 80, (
            f"Citation coverage {citation_coverage:.0f}% below 80% target. Results: {results}"
        )
