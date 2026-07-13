from __future__ import annotations

import os
from typing import Any

import requests

from .exceptions import QHAPIError, QHAuthError, QHRateLimitError


class QHAPIClient:
    """Client for the QH internal market-data API.

    Credentials are read from environment variables unless passed explicitly:
      QH_API_BASE_URL  (default: https://qh-api.corp.hertshtengroup.com)
      QH_API_USERNAME
      QH_API_PASSWORD

    The one-time username/password must first be obtained by logging in via
    Microsoft SSO at <base_url>/api/auth/ in a browser.
    """

    DEFAULT_BASE_URL = "https://qh-api.corp.hertshtengroup.com"

    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 15.0,
    ):
        self.base_url = (base_url or os.environ.get("QH_API_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.username = username or os.environ.get("QH_API_USERNAME")
        self.password = password or os.environ.get("QH_API_PASSWORD")
        self.timeout = timeout
        self._session = requests.Session()
        self._access_token: str | None = None

    # -- auth --

    def authenticate(self) -> None:
        if not self.username or not self.password:
            raise QHAuthError("QH_API_USERNAME/QH_API_PASSWORD not set")
        resp = self._session.post(
            f"{self.base_url}/api/token/",
            json={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        if not resp.ok:
            raise QHAuthError(
                f"Authentication failed ({resp.status_code})", resp.status_code, self._safe_body(resp)
            )
        data = resp.json()
        access = data.get("access")
        if not access:
            raise QHAuthError("Authentication response missing 'access' token", resp.status_code, data)
        self._access_token = access

    @property
    def access_token(self) -> str | None:
        return self._access_token

    # -- core request --

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        _retried: bool = False,
    ) -> Any:
        if not self._access_token:
            self.authenticate()

        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = self._session.request(
            method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
        )

        if resp.status_code == 401 and not _retried:
            self.authenticate()
            return self.request(method, path, params=params, json_body=json_body, _retried=True)

        if resp.status_code == 429:
            raise QHRateLimitError("Rate limit exceeded", resp.status_code, self._safe_body(resp))

        if not resp.ok:
            raise QHAPIError(f"{method} {path} failed ({resp.status_code})", resp.status_code, self._safe_body(resp))

        return resp.json() if resp.content else None

    @staticmethod
    def _safe_body(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return resp.text

    @staticmethod
    def _join(value) -> str:
        return value if isinstance(value, str) else ",".join(str(v) for v in value)

    # -- v1 endpoints --

    def get_tas(self, products, dates):
        return self.request("GET", "/api/tas/", params={"products": self._join(products), "dates": self._join(dates)})

    def get_ohlc(self, products, time_intervals):
        return self.request(
            "GET", "/api/ohlc/", params={"products": self._join(products), "timeIntervals": self._join(time_intervals)}
        )

    def get_fairvalue(self, products="*"):
        return self.request("GET", "/api/fairvalue/", params={"products": self._join(products)})

    def get_gtc(self, products="*", date: str | None = None, limit: int | None = None, offset: int | None = None):
        params: dict[str, Any] = {"products": self._join(products)}
        if date is not None:
            params["date"] = date
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return self.request("GET", "/api/gtc/", params=params)

    def get_economies_premiums(self, economies="*"):
        return self.request("GET", "/api/economies/premiums/", params={"economies": self._join(economies)})

    # -- v2 endpoints --

    def get_ohlc_v2(
        self,
        instruments,
        interval: str,
        count: int | None = None,
        start: int | None = None,
        end: int | None = None,
        extra_fields=None,
    ):
        params: dict[str, Any] = {"instruments": self._join(instruments), "interval": interval}
        if count is not None:
            params["count"] = count
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if extra_fields is not None:
            params["extraFields"] = self._join(extra_fields)
        return self.request("GET", "/api/v2/ohlc/", params=params)

    def get_fairvalue_v2(self, products="*"):
        return self.request("GET", "/api/v2/fairvalue/", params={"products": self._join(products)})

    def get_fairvalue_v2_historical(self, products="*"):
        return self.request("GET", "/api/v2/fairvalue/historical/", params={"products": self._join(products)})

    def get_vap_v2(self, instruments, interval: str, count: int | None = None, end: int | None = None):
        params: dict[str, Any] = {"instruments": self._join(instruments), "interval": interval}
        if count is not None:
            params["count"] = count
        if end is not None:
            params["end"] = end
        return self.request("GET", "/api/v2/vap/", params=params)
