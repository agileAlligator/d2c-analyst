"""Shopify Admin REST API connector."""

import logging
from collections.abc import Iterator

from app.config import settings
from app.connectors.base import BaseConnector, ConnectorMeta, RawRecord

logger = logging.getLogger(__name__)

RESOURCES = ["orders", "products", "refunds", "customers"]


class ShopifyConnector(BaseConnector):
    _rate_limit_calls = 2  # Shopify: 2 calls/sec on standard tier
    _rate_limit_period = 1.0

    def __init__(self):
        super().__init__()
        self._domain = settings.shopify_shop_domain
        self._token = settings.shopify_access_token
        self._base = f"https://{self._domain}/admin/api/2024-01"
        self._http.headers.update(
            {
                "X-Shopify-Access-Token": self._token,
                "Content-Type": "application/json",
            }
        )

    def meta(self) -> ConnectorMeta:
        return ConnectorMeta(name="shopify", source="shopify", resources=RESOURCES)

    def auth_status(self) -> bool:
        try:
            self._get(f"{self._base}/shop.json")
            return True
        except Exception:
            return False

    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]:
        if resource == "orders":
            yield from self._pull_orders(since)
        elif resource == "products":
            yield from self._pull_products(since)
        elif resource == "refunds":
            yield from self._pull_refunds(since)
        elif resource == "customers":
            yield from self._pull_customers(since)
        else:
            raise ValueError(f"Unknown Shopify resource: {resource}")

    def _pull_orders(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"limit": 250, "status": "any"}
        if since:
            params["updated_at_min"] = since
        yield from self._paginate("orders", params, "orders")

    def _pull_products(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"limit": 250}
        if since:
            params["updated_at_min"] = since
        yield from self._paginate("products", params, "products")

    def _pull_refunds(self, since: str | None) -> Iterator[RawRecord]:
        # Refunds are fetched per-order; we iterate recent orders instead
        # In the real ingestion loop, refunds are pulled while iterating orders
        # For standalone pull, fetch orders then their refunds
        for order in self._pull_orders(since):
            order_id = order.payload.get("id")
            try:
                resp = self._get(f"{self._base}/orders/{order_id}/refunds.json")
                for refund in resp.json().get("refunds", []):
                    yield RawRecord(
                        source_record_id=f"refund:{refund['id']}",
                        payload=refund,
                        resource_type="refund",
                    )
            except Exception as e:
                logger.warning("Failed to fetch refunds for order %s: %s", order_id, e)

    def _pull_customers(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"limit": 250}
        if since:
            params["updated_at_min"] = since
        yield from self._paginate("customers", params, "customers")

    def _paginate(self, endpoint: str, params: dict, key: str) -> Iterator[RawRecord]:
        url = f"{self._base}/{endpoint}.json"
        while url:
            resp = self._get(url, params=params if "?" not in url else None)
            data = resp.json()
            items = data.get(key, [])
            logger.debug("Shopify %s: fetched %d records", endpoint, len(items))
            for item in items:
                if "id" not in item:
                    raise ValueError(f"Shopify {key[:-1]} record missing 'id': {item}")
                yield RawRecord(
                    source_record_id=f"{key[:-1]}:{item['id']}",
                    payload=item,
                    resource_type=key[:-1],
                )
            # Shopify cursor-based pagination via Link header
            link_header = resp.headers.get("Link", "")
            url = self._next_link(link_header)
            params = {}  # URL already has params encoded

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                return part.split(";")[0].strip().strip("<>")
        return None
