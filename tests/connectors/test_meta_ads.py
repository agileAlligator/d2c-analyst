"""Offline connector tests for MetaAdsConnector.

Uses pytest-httpx to intercept httpx calls; no real credentials needed.
Exercises: correct RawRecord parsing, multi-page pagination, retry on 429.
"""
import json
from pathlib import Path

import httpx
import pytest

from app.connectors.meta_ads.connector import MetaAdsConnector

_FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures/meta_ads/insights.json").read_text()
)

# ---- fixture / helpers -------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    monkeypatch.setattr("app.connectors.meta_ads.connector.settings.meta_access_token", "test_token")
    monkeypatch.setattr("app.connectors.meta_ads.connector.settings.meta_ad_account_id", "act_123")


def _make_connector() -> MetaAdsConnector:
    return MetaAdsConnector()


# ---- parsing -----------------------------------------------------------------

def test_insights_parse_source_record_id(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("insights"))
    assert len(records) == 2
    assert records[0].source_record_id == "insight:23851000000021:2024-01-15"
    assert records[1].source_record_id == "insight:23851000000022:2024-01-15"


def test_insights_parse_resource_type(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("insights"))
    assert all(r.resource_type == "insight" for r in records)


def test_insights_parse_payload_fields(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("insights"))
    first = records[0].payload
    assert first["spend"] == "2340.00"
    assert first["campaign_name"] == "Diwali Sale 2024"
    assert first["impressions"] == "45000"


# ---- pagination --------------------------------------------------------------

def test_pagination_stops_when_no_next(httpx_mock):
    # Fixture has no "next" in paging → single request only
    httpx_mock.add_response(json=_FIXTURE)
    list(_make_connector().pull("insights"))
    assert len(httpx_mock.get_requests()) == 1


def test_pagination_follows_next_url(httpx_mock):
    page1 = {
        **_FIXTURE,
        "paging": {
            "cursors": {"before": "a", "after": "b"},
            "next": "https://graph.facebook.com/page2?token=test_token",
        },
    }
    page2 = _FIXTURE  # no next → stop
    httpx_mock.add_response(json=page1)
    httpx_mock.add_response(json=page2)

    records = list(_make_connector().pull("insights"))
    assert len(records) == 4  # 2 per page
    assert len(httpx_mock.get_requests()) == 2


# ---- retry on 429 ------------------------------------------------------------

def test_retry_on_429(httpx_mock):
    httpx_mock.add_response(status_code=429, headers={"Retry-After": "0"})
    httpx_mock.add_response(json=_FIXTURE)

    records = list(_make_connector().pull("insights"))
    assert len(records) == 2
    assert len(httpx_mock.get_requests()) == 2


# ---- other resources ---------------------------------------------------------

def test_campaigns_parse(httpx_mock):
    campaign_fixture = {
        "data": [
            {
                "id": "23851000000001",
                "name": "Diwali Sale 2024",
                "status": "ACTIVE",
                "objective": "CONVERSIONS",
                "daily_budget": "500000",
                "lifetime_budget": "0",
                "start_time": "2024-10-01T00:00:00+0000",
                "stop_time": None,
            }
        ],
        "paging": {"cursors": {}},
    }
    httpx_mock.add_response(json=campaign_fixture)
    records = list(_make_connector().pull("campaigns"))
    assert len(records) == 1
    assert records[0].source_record_id == "campaign:23851000000001"
    assert records[0].resource_type == "campaign"
    assert records[0].payload["name"] == "Diwali Sale 2024"


def test_unknown_resource_raises(httpx_mock):
    with pytest.raises(ValueError, match="Unknown Meta resource"):
        list(_make_connector().pull("banana"))
