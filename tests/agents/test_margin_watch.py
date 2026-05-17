"""Tests for MarginWatchAgent — fully offline (no DB required)."""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.margin_watch import MarginWatchAgent
from app.warehouse.metrics.catalog import MetricResult


def _make_agent() -> MarginWatchAgent:
    """Return a MarginWatchAgent with a dummy DB session and merchant_id."""
    db = MagicMock()
    return MarginWatchAgent(db=db, merchant_id="demo")


def _metric(rows, provenance_ids=None, sql_used="") -> MetricResult:
    return MetricResult(
        rows=rows,
        provenance_ids=provenance_ids or [],
        sql_used=sql_used,
    )


# ---------------------------------------------------------------------------
# Courier switch proposals
# ---------------------------------------------------------------------------


class TestCourierSwitchProposal:
    def test_proposes_switch_when_worst_courier_identified(self):
        rows = [
            {"courier": "Shadowfax", "rto_rate": 0.48, "rto_count": 12, "total_shipments": 25},
            {"courier": "BlueDart", "rto_rate": 0.05, "rto_count": 1, "total_shipments": 20},
        ]
        result = _metric(rows, provenance_ids=["prov-1", "prov-2"])
        agent = _make_agent()

        with patch("app.agents.margin_watch.query_metric", return_value=result):
            agent._propose_courier_switch()

        assert len(agent._proposals) == 1
        p = agent._proposals[0]
        assert p.action_type == "switch_courier"
        assert p.entity_key == "courier:Shadowfax"
        assert p.expected_inr_impact == pytest.approx(1612.5)
        assert p.would_do_api_call["NOT_SENT"] is True
        assert p.would_do_api_call["body"]["preferred_courier"] == "BlueDart"

    def test_skips_couriers_under_5_shipments(self):
        rows = [
            {"courier": "TinyExpress", "rto_rate": 1.0, "rto_count": 3, "total_shipments": 3},
        ]
        agent = _make_agent()
        with patch("app.agents.margin_watch.query_metric", return_value=_metric(rows)):
            agent._propose_courier_switch()
        assert len(agent._proposals) == 0

    def test_no_shipments_silently_returns(self):
        agent = _make_agent()
        with patch("app.agents.margin_watch.query_metric", return_value=_metric([])):
            agent._propose_courier_switch()
        assert len(agent._proposals) == 0

    def test_single_courier_above_threshold_no_proposal(self):
        # Only one courier passes the min-shipments threshold — best=None, so no proposal.
        rows = [
            {"courier": "OnlyOne", "rto_rate": 0.50, "rto_count": 5, "total_shipments": 10},
        ]
        agent = _make_agent()
        with patch("app.agents.margin_watch.query_metric", return_value=_metric(rows)):
            agent._propose_courier_switch()
        assert len(agent._proposals) == 0


# ---------------------------------------------------------------------------
# Ad-set pause proposals
# ---------------------------------------------------------------------------


class TestAdsetPauseProposal:
    def test_proposes_pause_below_2x_roas(self):
        spend_result = _metric(
            [{"campaign": "summer_sale", "ad_spend": 10000.0}],
            provenance_ids=["prov-sp"],
        )
        revenue_result = _metric(
            [{"revenue": 13500.0}],
            provenance_ids=["prov-rv"],
        )
        agent = _make_agent()

        with patch(
            "app.agents.margin_watch.query_metric",
            side_effect=[spend_result, revenue_result],
        ):
            agent._propose_adset_pause()

        assert len(agent._proposals) == 1
        p = agent._proposals[0]
        assert p.action_type == "pause_adset"
        assert p.expected_inr_impact == pytest.approx(3000.0)
        assert "NOT_SENT" in p.would_do_api_call and p.would_do_api_call["NOT_SENT"] is True
        assert "PAUSED" in p.would_do_api_call["note"]

    def test_no_proposal_above_2x_roas(self):
        spend_result = _metric([{"campaign": "hot", "ad_spend": 1000.0}])
        revenue_result = _metric([{"revenue": 3000.0}])
        agent = _make_agent()

        with patch(
            "app.agents.margin_watch.query_metric",
            side_effect=[spend_result, revenue_result],
        ):
            agent._propose_adset_pause()

        assert len(agent._proposals) == 0

    def test_zero_spend_no_proposal(self):
        spend_result = _metric([{"campaign": "empty", "ad_spend": 0.0}])
        revenue_result = _metric([{"revenue": 5000.0}])
        agent = _make_agent()

        with patch(
            "app.agents.margin_watch.query_metric",
            side_effect=[spend_result, revenue_result],
        ):
            agent._propose_adset_pause()

        assert len(agent._proposals) == 0


# ---------------------------------------------------------------------------
# Raise-price proposals  (driven through _execute)
# ---------------------------------------------------------------------------


def _empty_metric():
    return _metric([])


class TestRaisePriceProposal:
    """Drive the raise_price path via agent._execute(), mocking all 4 query_metric calls.

    Call order in _execute:
      1. contribution_margin  (flagged orders)
      2. _propose_courier_switch → rto_rate
      3. _propose_adset_pause  → ad_spend
      4. _propose_adset_pause  → revenue
    """

    def _run(self, cm_rows):
        cm_result = _metric(cm_rows, provenance_ids=["prov-cm"])
        side_effects = [cm_result, _empty_metric(), _empty_metric(), _empty_metric()]
        agent = _make_agent()
        with patch("app.agents.margin_watch.query_metric", side_effect=side_effects):
            agent._execute()
        return agent

    def test_negative_margin_emits_raise_price(self):
        rows = [
            {
                "order_number": "1063",
                "revenue": 500.0,
                "shipping_cost": 300.0,
                "rto_cost": 208.0,
                "contribution_margin": -8.03,
                "line_items": [{"variant_id": "v999", "price": "500.00", "quantity": 1}],
            }
        ]
        agent = self._run(rows)

        raise_price_proposals = [p for p in agent._proposals if p.action_type == "raise_price"]
        assert len(raise_price_proposals) == 1
        p = raise_price_proposals[0]
        # agent always uses order-level entity key (variant-level branch was removed)
        assert p.entity_key.startswith("order:")
        assert p.entity_key == "order:1063"
        assert p.expected_inr_impact == pytest.approx(8.03, rel=1e-3)
        assert p.would_do_api_call["NOT_SENT"] is True

    def test_negative_revenue_row_skipped(self):
        rows = [
            {
                "order_number": "9999",
                "revenue": -100.0,
                "shipping_cost": 0.0,
                "rto_cost": 0.0,
                "contribution_margin": -100.0,
                "line_items": [],
            }
        ]
        agent = self._run(rows)
        raise_price_proposals = [p for p in agent._proposals if p.action_type == "raise_price"]
        assert len(raise_price_proposals) == 0

    def test_null_order_number_skipped(self):
        rows = [
            {
                "order_number": None,
                "revenue": 300.0,
                "shipping_cost": 200.0,
                "rto_cost": 150.0,
                "contribution_margin": -50.0,
                "line_items": [],
            }
        ]
        agent = self._run(rows)
        raise_price_proposals = [p for p in agent._proposals if p.action_type == "raise_price"]
        assert len(raise_price_proposals) == 0

    def test_no_line_items_falls_back_to_order_key(self):
        rows = [
            {
                "order_number": "5050",
                "revenue": 1000.0,
                "shipping_cost": 1200.0,
                "rto_cost": 0.0,
                "contribution_margin": -200.0,
                "line_items": [],
            }
        ]
        agent = self._run(rows)
        raise_price_proposals = [p for p in agent._proposals if p.action_type == "raise_price"]
        assert len(raise_price_proposals) == 1
        assert raise_price_proposals[0].entity_key == "order:5050"
