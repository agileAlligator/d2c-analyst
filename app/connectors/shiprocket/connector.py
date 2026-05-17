"""Shiprocket API connector (Bearer token auth)."""
import logging
from collections.abc import Iterator

from app.config import settings
from app.connectors.base import BaseConnector, ConnectorMeta, RawRecord

logger = logging.getLogger(__name__)

RESOURCES = ["shipments"]
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
            resp = self._get(f"{BASE}/shipments", params={"per_page": 1})
            return True
        except Exception:
            return False

    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]:
        if resource == "shipments":
            yield from self._pull_shipments(since)
        else:
            raise ValueError(f"Unknown Shiprocket resource: {resource}")

    def _pull_shipments(self, since: str | None) -> Iterator[RawRecord]:
        params: dict = {"per_page": 100, "page": 1}
        if since:
            params["from"] = since[:10]
        page = 1
        while True:
            params["page"] = page
            resp = self._get(f"{BASE}/shipments", params=params)
            data = resp.json()
            items = data.get("data") or []
            if not items:
                break
            logger.debug("Shiprocket shipments page %d: %d records", page, len(items))
            for shipment in items:
                yield RawRecord(
                    source_record_id=f"shipment:{shipment['id']}",
                    payload=shipment,
                    resource_type="shipment",
                )
            if len(items) < params["per_page"]:
                break
            page += 1
