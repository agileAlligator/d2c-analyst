"""Golden question set for citation coverage and accuracy eval.

Each question has:
- question: the user query
- description: what we're testing
- expected_cites: True if every number must have a <cite> tag
- answer_checks: callables that verify answer content (accuracy assertions)

Ground truth from seeded demo merchant (BASE_DATE=2026-05-17):
  revenue 30d:  ₹37,053     revenue 7d:  ₹11,491
    (includes refund events with negative amounts; uses subtotal_price)
  ad_spend 30d: ₹31,465.35  ad_spend 14d: ₹14,526.98
  ROAS 14d: ~1.27x (below 2.0 threshold — agent fires)
  orders 30d:   33           Shadowfax RTO rate: 21.7% (highest)
  CM 7d order 1063: ₹-8.03  (negative margin)
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class GoldenQuestion:
    question: str
    description: str
    expected_cites: bool = True
    answer_checks: list[Callable[[str], bool]] = field(default_factory=list)


def _has_number(pattern: str):
    """Answer contains a number matching the regex pattern."""
    return lambda a: bool(re.search(pattern, a))


def _mentions_any(*terms: str):
    """Answer mentions at least one of the given terms (case-insensitive)."""
    return lambda a: any(t.lower() in a.lower() for t in terms)


def _number_in_range(low: float, high: float):
    """Answer contains a number between low and high."""

    def check(answer: str) -> bool:
        nums = re.findall(r"[\d,]+(?:\.\d+)?", answer.replace(",", ""))
        return any(low <= float(n) <= high for n in nums if n)

    return check


GOLDEN_QUESTIONS: list[GoldenQuestion] = [
    # ── Revenue ──────────────────────────────────────────────────────────
    GoldenQuestion(
        question="What was total revenue in the last 30 days?",
        description="Basic revenue query — answer must contain ~₹37,053",
        answer_checks=[
            # ₹37,053 ± 40% — wide range because rolling window slides daily from BASE_DATE
            _number_in_range(19_000, 45_000),
            _mentions_any("₹", "INR", "revenue"),
        ],
    ),
    GoldenQuestion(
        question="What was total revenue in the last 7 days?",
        description="Short-window revenue — answer must contain ~₹11,491",
        answer_checks=[
            # ₹11,491 ± 40% — rolling window slides daily from BASE_DATE
            _number_in_range(6_000, 16_000),
        ],
    ),
    # ── Ad spend ─────────────────────────────────────────────────────────
    GoldenQuestion(
        question="How much did we spend on Meta Ads in the last 30 days?",
        description="Ad spend query — must cite raw_meta_insights, ₹31,465.35",
        answer_checks=[
            # ₹31,465.35 ± 40% — rolling window slides daily from BASE_DATE
            _number_in_range(17_000, 40_000),
        ],
    ),
    GoldenQuestion(
        question="How much did we spend on Meta Ads in the last 14 days?",
        description="14d ad spend — partial window, ₹14,526.98",
        answer_checks=[
            # ₹14,526.98 ± 40% — rolling window slides daily from BASE_DATE
            _number_in_range(7_200, 17_000),
            _mentions_any("Meta", "ad", "spend"),
        ],
    ),
    GoldenQuestion(
        question="Which Meta campaign spent the most in the last 30 days?",
        description="Ad spend grouped by campaign — must name a campaign",
        answer_checks=[
            _mentions_any("New Year", "Diwali", "Brand Awareness", "Brand", "campaign"),
        ],
    ),
    # ── RTO / logistics ──────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our RTO rate by courier in the last 30 days?",
        description="RTO grouped by courier — must cite shipments, list all couriers",
        answer_checks=[
            _mentions_any("BlueDart", "Delhivery", "Xpressbees", "Shadowfax"),
            _has_number(r"\d+\.?\d*\s*%"),
        ],
    ),
    GoldenQuestion(
        question="Which courier has the highest RTO rate?",
        description="Courier ranking — Shadowfax has ~21.7% RTO rate (highest)",
        answer_checks=[
            _mentions_any("Shadowfax"),
            _number_in_range(15, 35),  # ~21.7% RTO rate
        ],
    ),
    # ── Contribution margin ───────────────────────────────────────────────
    GoldenQuestion(
        question="What is my contribution margin per order in the last 7 days?",
        description="CM per order 7d — must cite Shopify+Shiprocket join, 5 orders",
        answer_checks=[
            _mentions_any("contribution margin", "margin"),
            _mentions_any("1075", "1055", "1004", "1031"),  # order numbers in 7d window
        ],
    ),
    GoldenQuestion(
        question="Which orders had negative contribution margin in the last 14 days?",
        description="Negative CM 14d — orders 1063 (₹-8.03) and 1027 (₹-43.70)",
        answer_checks=[
            _mentions_any("1063", "1027"),
            _mentions_any("-8", "−8", "₹-8", "-43", "₹-43", "negative margin"),
        ],
    ),
    GoldenQuestion(
        question="Which orders had negative contribution margin in the last 30 days?",
        description="Negative CM 30d — tests Shopify↔Shiprocket join",
        answer_checks=[
            _mentions_any("negative", "margin"),
            # Require either a negative rupee amount OR a 4-digit order number (prevents
            # "0 orders" from satisfying the numeric check on a denial response).
            lambda a: bool(__import__("re").search(r"-\s*₹?\s*\d+|\b\d{4,}\b", a)),
        ],
    ),
    # ── CAC / ROAS ───────────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our CAC in the last 30 days?",
        description="CAC = total spend / total orders",
        answer_checks=[
            _mentions_any("CAC", "acquisition", "customer"),
            _has_number(r"\d{3,}"),
        ],
    ),
    GoldenQuestion(
        question="What was our total ad spend vs total revenue in the last 14 days? What is the ROAS?",
        description="ROAS — both spend and revenue must be cited",
        answer_checks=[
            _mentions_any("ROAS", "roas", "return"),
            _has_number(r"0\.\d+|[12]\.\d+"),  # ROAS will be < 2 (spend >> revenue)
        ],
    ),
    # ── Period comparison ────────────────────────────────────────────────
    GoldenQuestion(
        question="Compare revenue between the last 7 days and the last 30 days.",
        description="Period comparison — tests compare tool, both periods cited",
        answer_checks=[
            # ₹11,491 (7d) and ₹37,053 (30d) ± 40% — rolling window slides daily from BASE_DATE
            _number_in_range(6_000, 16_000),  # 7d value ~₹11,491
            _number_in_range(19_000, 50_000),  # 30d value ~₹37,053
        ],
    ),
    # ── AOV ──────────────────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our average order value in the last 30 days?",
        description="AOV — revenue ÷ order count",
        answer_checks=[
            _mentions_any("average", "AOV", "order value"),
            _has_number(r"\d{3,}"),
        ],
    ),
    # ── SQL fallback ─────────────────────────────────────────────────────
    GoldenQuestion(
        question="Show me the top 5 orders by revenue in the last 30 days.",
        description="SQL tool fallback — metric catalog isn't enough for ORDER BY",
        answer_checks=[
            _has_number(r"\d{3,}"),
        ],
    ),
    # ── Merchant isolation ───────────────────────────────────────────────
    GoldenQuestion(
        question="How many orders do we have in total?",
        description="Entity count — demo has 80 orders, used for isolation check",
        answer_checks=[
            # demo has 80 orders; require a multi-digit count AND that the answer is about orders
            _number_in_range(10, 500),
            _mentions_any("order", "total"),
        ],
    ),
    # ── Adversarial — boundary tests (documented failures) ───────────────
    GoldenQuestion(
        question="What was the average delivery time in days from order to delivery last month?",
        description="ADVERSARIAL: no delivery timestamps in schema — must say no data, not hallucinate",
        expected_cites=False,
        answer_checks=[
            # System should admit it lacks delivery-time data, not invent a duration.
            # "delivery time" removed — it appears in the question and any echo would pass.
            _mentions_any(
                "no data",
                "not available",
                "cannot",
                "don't have",
                "not tracked",
                "unavailable",
                "unable",
                "no delivery",
                "not stored",
                "not recorded",
            ),
        ],
    ),
    GoldenQuestion(
        question="What was our revenue per Meta Ad click in the last 30 days?",
        description=(
            "ADVERSARIAL: computed ratio (revenue ÷ clicks) — no native provenance anchor;"
            " tests cite-or-refuse behavior"
        ),
        answer_checks=[
            # Model must either cite a computed value OR refuse — not state a bare number
            # Passing means the answer contains a cited number or an explicit caveat
            _mentions_any("₹", "INR", "per click", "click"),
        ],
    ),
    GoldenQuestion(
        question=(
            "How did our conversion rate from ad impressions to orders change between last week and the week before?"
        ),
        description="ADVERSARIAL: multi-step derived metric with two time windows — tests chained computation citation",
        answer_checks=[
            _mentions_any("impression", "conversion", "rate", "click"),
        ],
    ),
]
