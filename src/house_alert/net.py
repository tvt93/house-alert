"""Polite HTTP fetching.

These are small agency sites, not a CDN. Everything here is deliberately
gentle: one request at a time per host, a real User-Agent that says who we are,
robots.txt respected, and failures swallowed into None so one broken site never
takes down the whole run.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "house-alert/0.2 (personal listing watcher; +https://github.com/tvt93/house-alert; "
    "one polite request per second; contact via repo owner)"
)

DEFAULT_TIMEOUT = 20.0


@dataclass
class FetchStats:
    requests: int = 0
    failures: int = 0
    blocked_by_robots: int = 0


class Fetcher:
    def __init__(
        self,
        delay: float = 1.0,
        timeout: float = DEFAULT_TIMEOUT,
        respect_robots: bool = True,
        max_bytes: int = 4_000_000,
    ) -> None:
        self.delay = delay
        self.respect_robots = respect_robots
        self.max_bytes = max_bytes
        self.stats = FetchStats()
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.6",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- robots

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            resp = self._client.get(f"{origin}/robots.txt")
            if resp.status_code >= 400:
                self._robots[origin] = None
                return None
            rp.parse(resp.text.splitlines())
        except httpx.HTTPError:
            self._robots[origin] = None
            return None
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)

    def sitemaps_from_robots(self, origin: str) -> list[str]:
        rp = self._robots_for(origin)
        if rp is None:
            return []
        return list(rp.site_maps() or [])

    # ----------------------------------------------------------------- fetch

    def _throttle(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def get(self, url: str) -> str | None:
        """Fetch a URL as text. Returns None on any failure or disallowance."""
        return self.fetch(url).text

    def fetch(self, url: str) -> "Fetched":
        """Fetch a URL and keep the status code and final URL too, for callers
        that need to tell "gone" (404, redirected to the index) from "down"."""
        if not self.allowed(url):
            self.stats.blocked_by_robots += 1
            log.debug("robots.txt disallows %s", url)
            return Fetched(url, None, None)

        host = urlparse(url).netloc
        self._throttle(host)
        self.stats.requests += 1
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            self.stats.failures += 1
            log.debug("fetch failed %s: %s", url, exc)
            return Fetched(url, None, None)

        final = str(resp.url)
        if resp.status_code >= 400:
            self.stats.failures += 1
            log.debug("fetch %s returned %s", url, resp.status_code)
            return Fetched(url, resp.status_code, None, final)
        if len(resp.content) > self.max_bytes:
            log.debug("skipping oversized response %s", url)
            return Fetched(url, resp.status_code, None, final)
        return Fetched(url, resp.status_code, resp.text, final)


@dataclass
class Fetched:
    url: str
    status_code: int | None          # None: network failure or robots.txt
    text: str | None
    final_url: str = ""              # after redirects

    @property
    def redirected_away(self) -> bool:
        """Did the site bounce us up to an index or the homepage, as sites do
        once a listing is gone? A redirect to a sibling URL (locale change,
        canonical slug) keeps the same depth and does not count."""
        if not self.final_url or self.final_url == self.url:
            return False
        depth = lambda u: len([s for s in urlparse(u).path.split("/") if s])  # noqa: E731
        return depth(self.final_url) < depth(self.url)
