"""Connector Protocol and base implementation with retry, rate-limiting, and cursor management."""

import logging
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@dataclass
class RawRecord:
    source_record_id: str
    payload: dict[str, Any]
    resource_type: str  # e.g. "order", "insight", "shipment"


@dataclass
class ConnectorMeta:
    name: str
    source: str
    resources: list[str]


class ConnectorProtocol(Protocol):
    def meta(self) -> ConnectorMeta: ...
    def auth_status(self) -> bool: ...
    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]: ...


class BaseConnector(ABC):
    """Shared retry, rate-limiting, and HTTP logic for all connectors."""

    # Subclasses override these
    _rate_limit_calls: int = 40
    _rate_limit_period: float = 1.0  # seconds

    def __init__(self):
        self._call_times: list[float] = []
        self._http = httpx.Client(timeout=30.0)

    def _throttle(self):
        now = time.monotonic()
        self._call_times = [t for t in self._call_times if now - t < self._rate_limit_period]
        if len(self._call_times) >= self._rate_limit_calls:
            sleep_for = self._rate_limit_period - (now - self._call_times[0])
            if sleep_for > 0:
                logger.debug("Rate limit: sleeping %.2fs", sleep_for)
                time.sleep(sleep_for)
        self._call_times.append(time.monotonic())

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(
            lambda e: (
                isinstance(e, httpx.TimeoutException)
                or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (429, 500, 502, 503, 504))
            )
        ),
        reraise=True,
    )
    def _get(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        resp = self._http.get(url, **kwargs)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 5))
            safe_url = re.sub(r"access_token=[^&]+", "access_token=***", url)
            logger.warning("429 from %s, sleeping %ss", safe_url, retry_after)
            time.sleep(retry_after)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(
            lambda e: (
                isinstance(e, httpx.TimeoutException)
                or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (429, 500, 502, 503, 504))
            )
        ),
        reraise=True,
    )
    def _post(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        resp = self._http.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    @abstractmethod
    def meta(self) -> ConnectorMeta: ...

    @abstractmethod
    def auth_status(self) -> bool: ...

    @abstractmethod
    def pull(self, resource: str, since: str | None = None) -> Iterator[RawRecord]: ...

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def new_run_id() -> str:
    return str(uuid.uuid4())
