"""Golden question set for citation coverage and accuracy eval.

Each question has:
- question: the user query
- expected_metrics: metric names that MUST be called
- expected_cites: True if every number must have a <cite> tag
- answer_checks: callables that verify the answer content
- description: what we're testing
"""
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class GoldenQuestion:
    question: str
    expected_metrics: list[str]
    description: str
    expected_cites: bool = True
    answer_checks: list[Callable[[str], bool]] = field(default_factory=list)


GOLDEN_QUESTIONS: list[GoldenQuestion] = [
    GoldenQuestion(
        question="What was total revenue in the last 30 days?",
        expected_metrics=["revenue"],
        description="Basic revenue query — must cite a raw_shopify_orders record",
        answer_checks=[
            lambda a: "₹" in a or "INR" in a or any(c.isdigit() for c in a),
        ],
    ),
    GoldenQuestion(
        question="How much did we spend on Meta Ads in the last 14 days?",
        expected_metrics=["ad_spend"],
        description="Ad spend query — must cite raw_meta_insights",
    ),
    GoldenQuestion(
        question="What is our RTO rate by courier in the last 30 days?",
        expected_metrics=["rto_rate"],
        description="RTO rate grouped by courier — must cite raw_shiprocket_shipments",
    ),
    GoldenQuestion(
        question="What is our CAC (customer acquisition cost) in the last 30 days?",
        expected_metrics=["cac"],
        description="CAC = ad spend / orders — must cite both spend and revenue sources",
    ),
    GoldenQuestion(
        question="Which orders had negative contribution margin last month?",
        expected_metrics=["contribution_margin"],
        description="Contribution margin — tests the Shopify↔Shiprocket join",
    ),
    GoldenQuestion(
        question="Compare revenue between the last 7 days and the last 30 days.",
        expected_metrics=["revenue"],
        description="Period comparison — tests the compare tool and dual citations",
    ),
    GoldenQuestion(
        question="What is our average order value in the last 30 days?",
        expected_metrics=["revenue"],
        description="AOV from revenue query",
    ),
    GoldenQuestion(
        question="Which courier has the highest RTO rate?",
        expected_metrics=["rto_rate"],
        description="Courier ranking — answer must name a courier with cited rate",
        answer_checks=[
            lambda a: any(c in a for c in ["Delhivery", "BlueDart", "Xpressbees", "Shadowfax"]),
        ],
    ),
    GoldenQuestion(
        question="What was our total ad spend vs total revenue in the last 14 days? What is the ROAS?",
        expected_metrics=["ad_spend", "revenue"],
        description="Multi-metric ROAS calculation — both numbers must be cited",
    ),
    GoldenQuestion(
        question="Show me the top 5 orders by revenue in the last 30 days.",
        expected_metrics=["revenue"],
        description="SQL tool fallback — may use sql tool when metric catalog isn't enough",
    ),
]
