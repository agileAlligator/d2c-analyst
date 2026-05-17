"""Connector tests using recorded fixtures."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestShopifyConnector:
    def test_pagination_parses_next_link(self):
        from app.connectors.shopify.connector import ShopifyConnector

        link = '<https://store.myshopify.com/admin/api/2024-01/orders.json?page_info=abc>; rel="next"'
        result = ShopifyConnector._next_link(link)
        assert result == "https://store.myshopify.com/admin/api/2024-01/orders.json?page_info=abc"

    def test_no_next_link_returns_none(self):
        from app.connectors.shopify.connector import ShopifyConnector

        result = ShopifyConnector._next_link("")
        assert result is None

    def test_pull_orders_from_fixture(self):
        from app.connectors.shopify.connector import ShopifyConnector

        fixture = load_fixture("shopify/orders.json")

        connector = ShopifyConnector.__new__(ShopifyConnector)
        connector._domain = "test.myshopify.com"
        connector._token = "test"
        connector._base = "https://test.myshopify.com/admin/api/2024-01"
        connector._call_times = []

        mock_resp = MagicMock()
        mock_resp.json.return_value = fixture
        mock_resp.headers = {}
        mock_resp.status_code = 200

        with patch.object(connector, "_get", return_value=mock_resp):
            records = list(connector._pull_orders(since=None))

        assert len(records) == len(fixture["orders"])
        assert records[0].resource_type == "order"
        assert records[0].source_record_id.startswith("order:")


class TestMetaAdsConnector:
    def test_record_id_insight(self):
        from app.connectors.meta_ads.connector import MetaAdsConnector

        item = {"campaign_id": "camp_001", "ad_id": "123", "date_start": "2024-01-01"}
        rid = MetaAdsConnector._record_id(item, "insight")
        assert rid == "insight:123:2024-01-01"

    def test_record_id_insight_missing_campaign_raises(self):
        import pytest

        from app.connectors.meta_ads.connector import MetaAdsConnector

        item = {"ad_id": "123", "date_start": "2024-01-01"}
        with pytest.raises(ValueError, match="campaign_id"):
            MetaAdsConnector._record_id(item, "insight")

    def test_record_id_campaign(self):
        from app.connectors.meta_ads.connector import MetaAdsConnector

        item = {"id": "456"}
        rid = MetaAdsConnector._record_id(item, "campaign")
        assert rid == "campaign:456"
