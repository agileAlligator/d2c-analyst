"""Shiprocket API connector (Bearer token auth)."""
import logging
from collections.abc import Iterator

from app.config import settings
from app.connectors.base import BaseConnector, ConnectorMeta, RawRecord

logger = logging.getLogger(__name__)

RESOURCES = ["orders", "shipments"]
BASE = "https://apiv2.shiprocket.in/v1/external"


class ShiprocketConnector(BaseConnector):
    _rate_limit_calls = 60
    _rate_limit_period = 60.0

    def __init__(self):
        super().__init__()
        self._token = settings.shiprocket_token
        self._http.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        })

    def meta(self) -> ConnectorMeta:
        return ConnectorMeta(name="shiprocket", source="shiprocket", resources=RESOURCES)

    def auth_status(self) -> bool:
        try:
            resp = self._get(f"{BASE}/orders", params={"per_page": 1})
            return resp.status_code == 200
        except Exception:
            return False

    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]:
        if resource == "orders":
            yield from self._pull_orders(since)
        elif resource == "shipments":
            yield from self._pull_shipments(since)
        else:
            raise ValueError(f"Unknown Shiprocket resource: {resource}")

    def _pull_orders(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"per_page": 100, "page": 1, "sort": "DESC"}
        if since:
            params["from"] = since[:10]  # YYYY-MM-DD
        yield from self._paginate_orders(params)

    def _paginate_orders(self, params: dict) -> Iterator[RawRecord]:
        page = 1
        while True:
            params["page"] = page
            resp = self._get(f"{BASE}/orders", params=params)
            data = resp.json()
            orders = data.get("data", {})
            items = orders if isinstance(orders, list) else orders.get("data", [])
            if not items:
                break
            logger.debug("Shiprocket orders page %d: %d records", page, len(items))
            for order in items:
                yield RawRecord(
                    source_record_id=f"order:{order['id']}",
                    payload=order,
                    resource_type="order",
                )
            # Stop if we got fewer than page size
            if len(items) < params.get("per_page", 100):
                break
            page += 1

    def _pull_shipments(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"per_page": 100, "page": 1}
        if since:
            params["from"] = since[:10]
        page = 1
        while True:
            params["page"] = page
            resp = self._get(f"{BASE}/shipments", params=params)
            data = resp.json()
            items = data.get("data", [])
            if not items:
                break
            logger.debug("Shiprocket shipments page %d: %d records", page, len(items))
            for shipment in items:
                yield RawRecord(
                    source_record_id=f"shipment:{shipment['id']}",
                    payload=shipment,
                    resource_type="shipment",
                )
            if len(items) < 100:
                break
            page += 1
