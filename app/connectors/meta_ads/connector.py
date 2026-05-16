"""Meta Marketing API connector."""
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

from app.config import settings
from app.connectors.base import BaseConnector, ConnectorMeta, RawRecord

logger = logging.getLogger(__name__)

RESOURCES = ["campaigns", "insights"]
API_VERSION = "v20.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"


class MetaAdsConnector(BaseConnector):
    _rate_limit_calls = 200
    _rate_limit_period = 3600.0  # Meta: 200 calls/hour per user token

    def __init__(self):
        super().__init__()
        self._token = settings.meta_access_token
        self._account_id = settings.meta_ad_account_id  # e.g. "act_123456"

    def _params(self, extra: dict | None = None) -> dict:
        p = {"access_token": self._token}
        if extra:
            p.update(extra)
        return p

    def meta(self) -> ConnectorMeta:
        return ConnectorMeta(name="meta_ads", source="meta", resources=RESOURCES)

    def auth_status(self) -> bool:
        try:
            resp = self._get(f"{BASE}/me", params=self._params())
            return "id" in resp.json()
        except Exception:
            return False

    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]:
        if resource == "campaigns":
            yield from self._pull_campaigns()
        elif resource == "insights":
            yield from self._pull_insights(since)
        else:
            raise ValueError(f"Unknown Meta resource: {resource}")

    def _pull_campaigns(self) -> Iterator[RawRecord]:
        fields = "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time"
        yield from self._paginate(
            f"{BASE}/{self._account_id}/campaigns",
            {"fields": fields, "limit": 100},
            "campaign",
        )

    def _pull_insights(self, since: str | None) -> Iterator[RawRecord]:
        # Default: last 30 days if no cursor
        if since:
            since_dt = datetime.fromisoformat(since)
        else:
            since_dt = datetime.utcnow() - timedelta(days=30)

        date_start = since_dt.strftime("%Y-%m-%d")
        date_stop = datetime.utcnow().strftime("%Y-%m-%d")

        fields = (
            "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
            "spend,impressions,clicks,reach,cpc,cpm,ctr,actions,action_values,"
            "date_start,date_stop"
        )
        params = {
            "fields": fields,
            "level": "ad",
            "time_range": f'{{"since":"{date_start}","until":"{date_stop}"}}',
            "time_increment": 1,  # daily breakdown
            "limit": 100,
        }
        yield from self._paginate(
            f"{BASE}/{self._account_id}/insights",
            params,
            "insight",
        )

    def _paginate(self, url: str, params: dict, record_type: str) -> Iterator[RawRecord]:
        params = {**params, **self._params()}
        while url:
            resp = self._get(url, params=params)
            data = resp.json()
            items = data.get("data", [])
            logger.debug("Meta %s: fetched %d records", record_type, len(items))
            for item in items:
                record_id = self._record_id(item, record_type)
                yield RawRecord(
                    source_record_id=record_id,
                    payload=item,
                    resource_type=record_type,
                )
            paging = data.get("paging", {})
            url = paging.get("next")
            params = {}

    @staticmethod
    def _record_id(item: dict, record_type: str) -> str:
        if record_type == "insight":
            # insights are per-ad per-day
            ad_id = item.get("ad_id", "unknown")
            date = item.get("date_start", "")
            return f"insight:{ad_id}:{date}"
        return f"{record_type}:{item.get('id', 'unknown')}"
