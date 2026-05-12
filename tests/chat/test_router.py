"""Unit tests for HeuristicRouter — offline, no API calls."""
import pytest

from app.chat.routing import HeuristicRouter, RoutingDecision
from app.chat.routing.router import CHEAP_MODEL, SMART_MODEL


@pytest.fixture
def router():
    return HeuristicRouter()


# ── Default (simple queries → cheap) ─────────────────────────────────────────

def test_simple_revenue_query_routes_cheap(router):
    d = router.route("What was total revenue last 30 days?", [], 0)
    assert d.tier == "cheap"
    assert d.model == CHEAP_MODEL
    assert d.reason == "default"


def test_simple_ad_spend_routes_cheap(router):
    d = router.route("How much did we spend on Meta Ads?", [], 0)
    assert d.tier == "cheap"


def test_single_metric_lookup_routes_cheap(router):
    # CAC is a derived metric — correctly escalates to smart
    d = router.route("What is our CAC?", [], 0)
    assert d.tier == "smart"
    assert "derived_metric" in d.reason


# ── Comparison signal ─────────────────────────────────────────────────────────

def test_compare_keyword_routes_smart(router):
    d = router.route("Compare revenue between last 7 days and last 30 days.", [], 0)
    assert d.tier == "smart"
    assert "comparison" in d.reason


def test_vs_keyword_routes_smart(router):
    d = router.route("Ad spend vs revenue this week", [], 0)
    assert d.tier == "smart"


def test_delta_keyword_routes_smart(router):
    d = router.route("What is the delta in ROAS between campaigns?", [], 0)
    assert d.tier == "smart"


# ── Derived metric signal ─────────────────────────────────────────────────────

def test_roas_routes_smart(router):
    d = router.route("What is our ROAS last 14 days?", [], 0)
    assert d.tier == "smart"
    assert "derived_metric" in d.reason


def test_contribution_margin_routes_smart(router):
    d = router.route("What is my contribution margin per order this week?", [], 0)
    assert d.tier == "smart"


def test_cac_in_full_sentence_routes_smart(router):
    d = router.route("What is our CAC and payback period?", [], 0)
    assert d.tier == "smart"


# ── Causal signal ─────────────────────────────────────────────────────────────

def test_why_routes_smart(router):
    d = router.route("Why is our ROAS down this week?", [], 0)
    assert d.tier == "smart"


def test_explain_routes_smart(router):
    d = router.route("Explain the breakdown of ad spend by campaign.", [], 0)
    assert d.tier == "smart"


# ── SQL / ranking signal ──────────────────────────────────────────────────────

def test_top_n_routes_smart(router):
    d = router.route("Show me the top 5 orders by revenue.", [], 0)
    assert d.tier == "smart"


def test_highest_routes_smart(router):
    d = router.route("Which courier has the highest RTO rate?", [], 0)
    assert d.tier == "smart"


# ── Multi-timerange signal ────────────────────────────────────────────────────

def test_two_time_ranges_routes_smart(router):
    d = router.route("Revenue last 7 days vs last 30 days.", [], 0)
    assert d.tier == "smart"


def test_single_time_range_does_not_escalate(router):
    d = router.route("Revenue last 30 days.", [], 0)
    assert d.tier == "cheap"


# ── Negative margin signal ────────────────────────────────────────────────────

def test_negative_margin_routes_smart(router):
    d = router.route("Which orders had negative contribution margin?", [], 0)
    assert d.tier == "smart"


# ── Deep turn signal ──────────────────────────────────────────────────────────

def test_deep_turn_routes_smart(router):
    d = router.route("What about last month?", [], turn=3)
    assert d.tier == "smart"
    assert "deep_turn" in d.reason


def test_early_turn_does_not_escalate(router):
    d = router.route("What about last month?", [], turn=1)
    assert d.tier == "cheap"


# ── Length signal ─────────────────────────────────────────────────────────────

def test_long_query_routes_smart(router):
    long_q = "What " + "is " * 35 + "revenue?"
    d = router.route(long_q, [], 0)
    assert d.tier == "smart"
    assert "length" in d.reason


# ── Escalation ────────────────────────────────────────────────────────────────

def test_escalate_upgrades_tier(router):
    cheap = router.route("Revenue last 30 days?", [], 0)
    assert cheap.tier == "cheap"

    escalated = router.escalate(cheap, "citation_fail")
    assert escalated.tier == "smart"
    assert escalated.model == SMART_MODEL
    assert escalated.escalated is True
    assert "cascade" in escalated.reason


def test_escalate_preserves_signals(router):
    cheap = router.route("Revenue?", [], 0)
    escalated = router.escalate(cheap, "citation_fail")
    assert escalated.signals == cheap.signals
