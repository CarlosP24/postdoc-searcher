"""A polite HTTP client: browser-like UA, timeouts, retries, per-host delay."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 PostdocRadar/1.0"
)


class Http:
    def __init__(self, delay_per_host: float = 1.0, timeout: float = 25.0):
        self.delay = delay_per_host
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self.session = requests.Session()
        retry = Retry(total=2, connect=1, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en;q=0.9,es;q=0.6",
        })
        self.errors: list[str] = []

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    def get(self, url: str, **kw) -> requests.Response | None:
        self._wait(url)
        try:
            resp = self.session.get(url, timeout=(8.0, self.timeout), **kw)
        except requests.RequestException as exc:
            self._error(url, str(exc))
            return None
        if resp.status_code >= 400:
            self._error(url, f"HTTP {resp.status_code}")
            return None
        return resp

    def text(self, url: str, **kw) -> str | None:
        resp = self.get(url, **kw)
        if resp is None:
            return None
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text

    def json(self, url: str, **kw):
        resp = self.get(url, **kw)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            self._error(url, "invalid JSON")
            return None

    def _error(self, url: str, msg: str) -> None:
        log.warning("fetch failed %s: %s", url, msg)
        self.errors.append(f"{url}: {msg}")
