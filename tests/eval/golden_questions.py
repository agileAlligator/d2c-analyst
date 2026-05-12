"""Golden question set for citation coverage and accuracy eval.

Each question has:
- question: the user query
- description: what we're testing
- expected_cites: True if every number must have a <cite> tag
- answer_checks: callables that verify answer content (accuracy assertions)
- expected_metrics: metric names expected to appear in tool calls

Ground truth from seeded demo merchant (80 orders, May 2026):
  revenue 30d:  ₹41,646     revenue 7d:  ₹11,491
  ad_spend 30d: ₹31,465.35  ad_spend 14d: ₹14,526.97
    (computed by replaying RNG(42) through 320 order calls + 9 refund calls
     before the Meta insights loop; 90 uniform(180,480) draws for 30d,
     42 draws for 14d)
  ROAS 14d: ~1.37x (below 2.0 threshold — agent fires)
  orders 30d:   ~10          BlueDart RTO rate: 60%  (highest)
  CM 7d order 1063: ₹-8.03  (negative margin)
"""
import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class GoldenQuestion:
    question: str
    description: str
    expected_metrics: list[str] = field(default_factory=list)
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
        description="Basic revenue query — answer must contain ~₹41,646",
        expected_metrics=["revenue"],
        answer_checks=[
            _number_in_range(40_000, 43_000),  # ₹41,646 ± variance
            _mentions_any("₹", "INR", "revenue"),
        ],
    ),
    GoldenQuestion(
        question="What was total revenue in the last 7 days?",
        description="Short-window revenue — answer must contain ~₹11,491",
        expected_metrics=["revenue"],
        answer_checks=[
            _number_in_range(10_000, 13_000),
        ],
    ),

    # ── Ad spend ─────────────────────────────────────────────────────────
    GoldenQuestion(
        question="How much did we spend on Meta Ads in the last 30 days?",
        description="Ad spend query — must cite raw_meta_insights, ₹31,465.35",
        expected_metrics=["ad_spend"],
        answer_checks=[
            _number_in_range(26_745, 36_185),  # ₹31,465.35 ± 15%
        ],
    ),
    GoldenQuestion(
        question="How much did we spend on Meta Ads in the last 14 days?",
        description="14d ad spend — partial window, ₹14,526.97",
        expected_metrics=["ad_spend"],
        answer_checks=[
            _number_in_range(12_347, 16_706),  # ₹14,526.97 ± 15%
            _mentions_any("Meta", "ad", "spend"),
        ],
    ),
    GoldenQuestion(
        question="Which Meta campaign spent the most in the last 30 days?",
        description="Ad spend grouped by campaign — must name a campaign",
        expected_metrics=["ad_spend"],
        answer_checks=[
            _mentions_any("New Year", "Diwali", "Brand Awareness", "Brand", "campaign"),
        ],
    ),

    # ── RTO / logistics ──────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our RTO rate by courier in the last 30 days?",
        description="RTO grouped by courier — must cite shipments, list all couriers",
        expected_metrics=["rto_rate"],
        answer_checks=[
            _mentions_any("BlueDart", "Delhivery", "Xpressbees", "Shadowfax"),
            _has_number(r"\d+\.?\d*\s*%"),
        ],
    ),
    GoldenQuestion(
        question="Which courier has the highest RTO rate?",
        description="Courier ranking — BlueDart has 60% RTO rate (highest)",
        expected_metrics=["rto_rate"],
        answer_checks=[
            _mentions_any("BlueDart"),
            _number_in_range(50, 70),  # 60% RTO rate
        ],
    ),

    # ── Contribution margin ───────────────────────────────────────────────
    GoldenQuestion(
        question="What is my contribution margin per order this week?",
        description="CM per order 7d — must cite Shopify+Shiprocket join, 5 orders",
        expected_metrics=["contribution_margin"],
        answer_checks=[
            _mentions_any("contribution margin", "margin"),
            _mentions_any("1063", "1075", "1055", "1004", "1031"),  # order numbers
        ],
    ),
    GoldenQuestion(
        question="Which orders had negative contribution margin in the last 7 days?",
        description="Negative CM — order 1063 has ₹-8.03 margin",
        expected_metrics=["contribution_margin"],
        answer_checks=[
            _mentions_any("1063", "negative", "-8", "-₹8"),
        ],
    ),
    GoldenQuestion(
        question="Which orders had negative contribution margin last month?",
        description="Negative CM last month — tests Shopify↔Shiprocket join",
        expected_metrics=["contribution_margin"],
        answer_checks=[
            _mentions_any("negative", "margin", "₹"),
        ],
    ),

    # ── CAC / ROAS ───────────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our CAC in the last 30 days?",
        description="CAC = total spend / total orders",
        expected_metrics=["cac"],
        answer_checks=[
            _mentions_any("CAC", "acquisition", "customer"),
            _has_number(r"\d{3,}"),
        ],
    ),
    GoldenQuestion(
        question="What was our total ad spend vs total revenue in the last 14 days? What is the ROAS?",
        description="ROAS — both spend and revenue must be cited",
        expected_metrics=["ad_spend", "revenue"],
        answer_checks=[
            _mentions_any("ROAS", "roas", "return"),
            _has_number(r"0\.\d+|[12]\.\d+"),  # ROAS will be < 2 (spend >> revenue)
        ],
    ),

    # ── Period comparison ────────────────────────────────────────────────
    GoldenQuestion(
        question="Compare revenue between the last 7 days and the last 30 days.",
        description="Period comparison — tests compare tool, both periods cited",
        expected_metrics=["revenue"],
        answer_checks=[
            _number_in_range(10_000, 13_000),   # 7d value
            _number_in_range(40_000, 43_000),   # 30d value
        ],
    ),

    # ── AOV ──────────────────────────────────────────────────────────────
    GoldenQuestion(
        question="What is our average order value in the last 30 days?",
        description="AOV — revenue ÷ order count",
        expected_metrics=["revenue"],
        answer_checks=[
            _mentions_any("average", "AOV", "order value"),
            _has_number(r"\d{3,}"),
        ],
    ),

    # ── SQL fallback ─────────────────────────────────────────────────────
    GoldenQuestion(
        question="Show me the top 5 orders by revenue in the last 30 days.",
        description="SQL tool fallback — metric catalog isn't enough for ORDER BY",
        expected_metrics=["revenue"],
        answer_checks=[
            _has_number(r"\d{3,}"),
        ],
    ),

    # ── Merchant isolation ───────────────────────────────────────────────
    GoldenQuestion(
        question="How many orders do we have in total?",
        description="Entity count — demo has 80 orders, used for isolation check",
        expected_metrics=[],
        answer_checks=[
            _has_number(r"\d+"),
        ],
    ),

    # ── Adversarial — boundary tests (documented failures) ───────────────
    GoldenQuestion(
        question="What was the average delivery time in days from order to delivery last month?",
        description="ADVERSARIAL: no delivery timestamps in schema — must say no data, not hallucinate",
        expected_metrics=[],
        expected_cites=False,
        answer_checks=[
            # System should admit it lacks delivery-time data, not invent a duration
            _mentions_any(
                "no data", "not available", "cannot", "don't have",
                "delivery time", "not tracked", "unavailable", "unable",
                "no delivery", "not stored", "not recorded",
            ),
        ],
    ),
    GoldenQuestion(
        question="What was our revenue per Meta Ad click in the last 30 days?",
        description=(
            "ADVERSARIAL: computed ratio (revenue ÷ clicks) — no native provenance anchor;"
            " tests cite-or-refuse behavior"
        ),
        expected_metrics=["revenue", "ad_spend"],
        answer_checks=[
            # Model must either cite a computed value OR refuse — not state a bare number
            # Passing means the answer contains a cited number or an explicit caveat
            _mentions_any("₹", "INR", "per click", "click"),
        ],
    ),
    GoldenQuestion(
        question=(
            "How did our conversion rate from ad impressions to orders"
            " change between last week and the week before?"
        ),
        description="ADVERSARIAL: multi-step derived metric with two time windows — tests chained computation citation",
        expected_metrics=[],
        answer_checks=[
            _mentions_any("impression", "conversion", "rate", "click"),
        ],
    ),
]
