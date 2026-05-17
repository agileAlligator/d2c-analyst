"""Offline connector tests for ShiprocketConnector.

Uses pytest-httpx to intercept httpx calls; no real credentials needed.
Exercises: correct RawRecord parsing, pagination (page-size stop condition), retry on 429.
"""
import json
from pathlib import Path

import pytest

from app.connectors.shiprocket.connector import ShiprocketConnector

_FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures/shiprocket/shipments.json").read_text()
)

# ---- fixture / helpers -------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    monkeypatch.setattr("app.connectors.shiprocket.connector.settings.shiprocket_token", "test_token")


def _make_connector() -> ShiprocketConnector:
    return ShiprocketConnector()


# ---- parsing -----------------------------------------------------------------

def test_shipments_parse_source_record_id(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("shipments"))
    assert records[0].source_record_id == "shipment:1001"
    assert records[1].source_record_id == "shipment:1002"


def test_shipments_parse_resource_type(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("shipments"))
    assert all(r.resource_type == "shipment" for r in records)


def test_shipments_parse_payload_fields(httpx_mock):
    httpx_mock.add_response(json=_FIXTURE)
    records = list(_make_connector().pull("shipments"))
    delivered = records[0].payload
    rto = records[1].payload
    assert delivered["courier_name"] == "Delhivery"
    assert delivered["status"] == "DELIVERED"
    assert rto["status"] == "RTO"
    assert rto["courier_name"] == "BlueDart"


# ---- pagination: Shiprocket stops when page returns < per_page items ---------

def test_pagination_stops_when_partial_page(httpx_mock):
    # 2 records < per_page (100) → connector stops after first call
    httpx_mock.add_response(json=_FIXTURE)
    list(_make_connector().pull("shipments"))
    assert len(httpx_mock.get_requests()) == 1


def test_pagination_fetches_next_page_when_full(httpx_mock):
    # Simulate page 1 returning 100 items (full) → connector fetches page 2
    full_page_data = {"data": [{"id": i} for i in range(100)]}
    empty_page_data = {"data": []}
    httpx_mock.add_response(json=full_page_data)
    httpx_mock.add_response(json=empty_page_data)

    records = list(_make_connector().pull("shipments"))
    assert len(records) == 100
    assert len(httpx_mock.get_requests()) == 2


# ---- retry on 429 ------------------------------------------------------------

def test_retry_on_429(httpx_mock):
    httpx_mock.add_response(status_code=429, headers={"Retry-After": "0"})
    httpx_mock.add_response(json=_FIXTURE)

    records = list(_make_connector().pull("shipments"))
    assert len(records) == 2
    assert len(httpx_mock.get_requests()) == 2


def test_unknown_resource_raises(httpx_mock):
    with pytest.raises(ValueError, match="Unknown Shiprocket resource"):
        list(_make_connector().pull("banana"))


def test_orders_resource_removed(httpx_mock):
    """'orders' was removed from RESOURCES — pull() must reject it."""
    from app.connectors.shiprocket.connector import RESOURCES
    assert "orders" not in RESOURCES
    with pytest.raises(ValueError, match="Unknown Shiprocket resource"):
        list(_make_connector().pull("orders"))
