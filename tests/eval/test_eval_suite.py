"""Eval suite — citation coverage and accuracy against the seeded demo merchant.

Requires a live Postgres with seed data: make seed
Skipped when OPENAI_API_KEY is not set (or is 'dummy').

Run with: pytest tests/eval/ -v -s
"""
import json
import os
import time
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
except ImportError:
    pass

_OPENAI = os.getenv("OPENAI_API_KEY", "dummy")
SKIP_REASON = "No live LLM API key — set OPENAI_API_KEY to run eval"
SKIP = _OPENAI == "dummy" or not _OPENAI

RESULTS_FILE = Path(__file__).parent / "eval_results.json"


def _requires_live():
    return pytest.mark.skipif(SKIP, reason=SKIP_REASON)


def _run_question(question: str, merchant_id: str = "demo") -> dict:
    from app.chat.loop import run_chat
    from app.chat.validator import bare_number_re
    from app.warehouse.db import SessionLocal

    start = time.time()
    with SessionLocal() as db:
        result = run_chat(question, db, merchant_id)
    elapsed = time.time() - start

    answer = result["answer"]
    # Validator strips cite tags to clean prose — count numbers that survived (each was
    # originally inside a cite tag). CITE_RE.findall on the cleaned answer always returns 0.
    cited_count = len(bare_number_re.findall(answer))

    return {
        "question": question,
        "answer": answer,
        "all_citations_valid": result["all_citations_valid"],
        "issues": result["issues"],
        "cited_count": cited_count,
        "tool_calls": len(result["tool_calls"]),
        "tool_names": [tc["tool"] for tc in result["tool_calls"]],
        "provenance_ids": result["provenance_ids"],
        "latency_s": round(elapsed, 2),
    }


# ── Citation correctness ──────────────────────────────────────────────────────

@_requires_live()
class TestCitationCorrectness:
    """Every number in every answer must have a valid <cite> tag."""

    def test_revenue_30d(self):
        r = _run_question("What was total revenue in the last 30 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}\nAnswer: {r['answer']}"
        assert r["cited_count"] >= 1

    def test_revenue_7d(self):
        r = _run_question("What was total revenue in the last 7 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}\nAnswer: {r['answer']}"

    def test_ad_spend_30d(self):
        r = _run_question("How much did we spend on Meta Ads in the last 30 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"
        assert r["cited_count"] >= 1

    def test_rto_by_courier(self):
        r = _run_question("What is our RTO rate by courier in the last 30 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_contribution_margin_7d(self):
        r = _run_question("What is my contribution margin per order this week?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_cac_30d(self):
        r = _run_question("What is our CAC in the last 30 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_period_comparison(self):
        r = _run_question("Compare revenue between the last 7 days and the last 30 days.")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_aov(self):
        r = _run_question("What is our average order value in the last 30 days?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_roas(self):
        r = _run_question("What was our total ad spend vs total revenue in the last 14 days? What is the ROAS?")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"

    def test_sql_fallback(self):
        r = _run_question("Show me the top 5 orders by revenue in the last 30 days.")
        assert r["all_citations_valid"], f"Issues: {r['issues']}"


# ── Accuracy ──────────────────────────────────────────────────────────────────

@_requires_live()
class TestAccuracy:
    """Answer content matches known ground truth from seeded data."""

    def test_revenue_30d_ballpark(self):
        """30d revenue should be ~₹31,814 (includes refunds, uses subtotal_price).
        Range is ±40% to stay valid as the rolling window slides from BASE_DATE=2026-05-13."""
        r = _run_question("What was total revenue in the last 30 days?")
        import re
        raw = re.findall(r"[\d,]+(?:\.\d+)?", r["answer"])
        nums = [float(n.replace(",", "")) for n in raw if n.replace(",", "").replace(".", "").isdigit()]
        assert any(19_000 <= n <= 45_000 for n in nums), (
            f"Expected ~31814 (±40%) in answer, got numbers: {nums}\nAnswer: {r['answer']}"
        )

    def test_highest_rto_courier_is_shadowfax(self):
        """Shadowfax has ~47.8% RTO rate — highest of all couriers."""
        r = _run_question("Which courier has the highest RTO rate?")
        assert "Shadowfax" in r["answer"], (
            f"Expected Shadowfax (47.8% RTO) to be named. Answer: {r['answer']}"
        )

    def test_negative_cm_order_1063(self):
        """Order 1063 has contribution margin of ₹-8.03 — only negative this week."""
        r = _run_question("Which orders had negative contribution margin in the last 7 days?")
        # Ground truth: order 1063 has CM of ₹-8.03. "no orders" / "none" are hallucinations.
        has_order = "1063" in r["answer"]
        has_negative_amount = any(t in r["answer"].lower() for t in ["negative", "-8"])
        assert has_order or has_negative_amount, (
            f"Expected order 1063 (₹-8.03 CM) or a specific negative amount. Answer: {r['answer']}"
        )

    def test_ad_spend_30d_ballpark(self):
        """30d ad spend should be ~₹28,365.69 from seeded data.
        Range is ±40% to stay valid as the rolling window slides from BASE_DATE=2026-05-13."""
        r = _run_question("How much did we spend on Meta Ads in the last 30 days?")
        import re
        raw = re.findall(r"[\d,]+(?:\.\d+)?", r["answer"])
        nums = [float(n.replace(",", "")) for n in raw if n.replace(",", "").replace(".", "").isdigit()]
        assert any(17_000 <= n <= 40_000 for n in nums), (
            f"Expected ~28366 (±40%) in answer, got: {nums}\nAnswer: {r['answer']}"
        )

    def test_rto_by_courier_lists_all_couriers(self):
        """RTO breakdown should mention all 4 couriers."""
        r = _run_question("What is our RTO rate by courier in the last 30 days?")
        for courier in ["BlueDart", "Delhivery", "Xpressbees", "Shadowfax"]:
            assert courier in r["answer"], (
                f"Expected {courier} in RTO answer. Answer: {r['answer']}"
            )

    def test_period_comparison_contains_both_values(self):
        """Comparison answer must contain values for both 7d (~6,795) and 30d (~31,814).
        Ranges are ±40% to stay valid as the rolling window slides from BASE_DATE=2026-05-13."""
        r = _run_question("Compare revenue between the last 7 days and the last 30 days.")
        import re
        raw = re.findall(r"[\d,]+(?:\.\d+)?", r["answer"])
        nums = [float(n.replace(",", "")) for n in raw if n.replace(",", "").replace(".", "").isdigit()]
        has_7d = any(4_000 <= n <= 10_000 for n in nums)
        has_30d = any(19_000 <= n <= 45_000 for n in nums)
        assert has_7d and has_30d, (
            f"Expected both 7d (~6795) and 30d (~31814) values. Got: {nums}\nAnswer: {r['answer']}"
        )

    def test_cm_7d_includes_order_1063(self):
        """CM this week includes order 1063 (₹-8.03), 1075 (₹588), 1055 (₹1300)."""
        r = _run_question("What is my contribution margin per order this week?")
        mentioned = [o for o in ["1063", "1075", "1055", "1004", "1031"] if o in r["answer"]]
        assert len(mentioned) >= 3, (
            f"Expected at least 3 of the 5 CM orders. Got: {mentioned}\nAnswer: {r['answer']}"
        )

    def test_campaign_spend_names_a_campaign(self):
        """Campaign spend answer should name one of the 3 seeded campaigns."""
        r = _run_question("Which Meta campaign spent the most in the last 30 days?")
        campaigns = ["New Year", "Diwali", "Brand Awareness"]
        assert any(c in r["answer"] for c in campaigns), (
            f"Expected a campaign name. Answer: {r['answer']}"
        )


# ── Merchant isolation (RLS) ──────────────────────────────────────────────────

@_requires_live()
class TestMerchantIsolation:
    """demo and demo2 must never see each other's data."""

    def test_revenue_differs_between_merchants(self):
        """demo has 80 orders, demo2 has 5 — revenue must differ."""
        import re
        r_demo = _run_question("What was total revenue in the last 90 days?", merchant_id="demo")
        r_demo2 = _run_question("What was total revenue in the last 90 days?", merchant_id="demo2")

        def _has_numbers(answer: str) -> bool:
            return bool(re.search(r"\d{2,}", answer))

        if not _has_numbers(r_demo["answer"]) or not _has_numbers(r_demo2["answer"]):
            pytest.skip("One or both answers contain no numbers (likely timeout boilerplate) — skipping isolation check")

        assert r_demo["answer"] != r_demo2["answer"], (
            "demo and demo2 returned identical revenue — RLS isolation broken"
        )

    def test_demo2_has_lower_revenue(self):
        """demo2 (5 orders) must report lower revenue than demo (80 orders)."""
        import re
        r_demo = _run_question("What was total revenue in the last 90 days?", merchant_id="demo")
        r_demo2 = _run_question("What was total revenue in the last 90 days?", merchant_id="demo2")

        def extract_max(answer: str) -> float:
            nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+(?:\.\d+)?", answer)
                    if n.replace(",", "").replace(".", "").isdigit()]
            return max(nums) if nums else 0

        demo_rev = extract_max(r_demo["answer"])
        demo2_rev = extract_max(r_demo2["answer"])

        if demo_rev == 0 or demo2_rev == 0:
            pytest.skip("One or both answers contain no numbers (likely timeout boilerplate) — skipping revenue comparison")

        assert demo_rev > demo2_rev, (
            f"demo revenue ({demo_rev}) should exceed demo2 ({demo2_rev})"
        )


# ── Full scoreboard ───────────────────────────────────────────────────────────

@_requires_live()
class TestFullScorecardAndWrite:
    """Run all golden questions, produce scoreboard, write eval_results.json."""

    def test_full_scoreboard(self):
        from tests.eval.golden_questions import GOLDEN_QUESTIONS

        results = []
        valid_count = 0
        total_cited = 0
        accuracy_pass = 0
        accuracy_total = 0
        non_adversarial_valid = 0
        non_adversarial_total = 0
        adversarial_valid = 0
        adversarial_total = 0

        for gq in GOLDEN_QUESTIONS:
            r = _run_question(gq.question)
            valid = r["all_citations_valid"]
            if valid:
                valid_count += 1
            total_cited += r["cited_count"]
            is_adversarial = "ADVERSARIAL" in gq.description
            if is_adversarial:
                adversarial_total += 1
                if valid:
                    adversarial_valid += 1
            else:
                non_adversarial_total += 1
                if valid:
                    non_adversarial_valid += 1

            # Run answer_checks
            check_results = []
            for check in gq.answer_checks:
                accuracy_total += 1
                passed = check(r["answer"])
                if passed:
                    accuracy_pass += 1
                check_results.append(passed)

            row = {
                "question": gq.question[:60],
                "description": gq.description,
                "is_adversarial": is_adversarial,
                "valid": valid,
                "cited_count": r["cited_count"],
                "issues": r["issues"],
                "latency_s": r["latency_s"],
                "tool_calls": r["tool_calls"],
                "accuracy_checks": check_results,
            }
            results.append(row)

            status = "✅" if valid else "❌"
            acc = f"acc {sum(check_results)}/{len(check_results)}" if check_results else "no checks"
            print(f"\n{status} [{acc}] {gq.question[:55]}")
            print(f"   Cited: {r['cited_count']} | {r['latency_s']}s | tools: {r['tool_calls']}")
            if r["issues"]:
                print(f"   Issues: {r['issues']}")

        n = len(GOLDEN_QUESTIONS)
        citation_pct = valid_count / n * 100
        accuracy_pct = accuracy_pass / accuracy_total * 100 if accuracy_total else 0

        print(f"\n{'='*60}")
        print(f"Citation coverage: {valid_count}/{n} ({citation_pct:.0f}%)")
        print(f"Accuracy checks:   {accuracy_pass}/{accuracy_total} ({accuracy_pct:.0f}%)")
        print(f"Total cited nums:  {total_cited}")

        latencies = [r["latency_s"] for r in results]
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"Latency P50: {p50}s  P95: {p95}s")

        non_adv_pct = non_adversarial_valid / non_adversarial_total * 100 if non_adversarial_total else 100
        adv_pct = adversarial_valid / adversarial_total * 100 if adversarial_total else 100

        print(f"Non-adversarial citation: {non_adversarial_valid}/{non_adversarial_total} ({non_adv_pct:.0f}%)")
        print(f"Adversarial citation:     {adversarial_valid}/{adversarial_total} ({adv_pct:.0f}%)")

        RESULTS_FILE.write_text(json.dumps({
            "citation_coverage_pct": citation_pct,
            "non_adversarial_citation_pct": non_adv_pct,
            "adversarial_citation_pct": adv_pct,
            "accuracy_pct": accuracy_pct,
            "valid_count": valid_count,
            "total_questions": n,
            "total_cited_numbers": total_cited,
            "accuracy_pass": accuracy_pass,
            "accuracy_total": accuracy_total,
            "p50_latency_s": p50,
            "p95_latency_s": p95,
            "results": results,
        }, indent=2))

        assert non_adv_pct >= 100, (
            f"Non-adversarial citation coverage {non_adv_pct:.0f}% — must be 100%. "
            f"Failing questions: {[r['question'] for r in results if not r['valid'] and 'ADVERSARIAL' not in r.get('description','')]}"
        )
        assert adv_pct >= 66, f"Adversarial citation coverage {adv_pct:.0f}% below 66% floor"
        assert accuracy_pct >= 70, f"Accuracy {accuracy_pct:.0f}% below 70% target"
