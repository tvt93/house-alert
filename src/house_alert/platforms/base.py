"""Listing discovery.

Discovery answers one question per agency: which URLs on this site are
for-sale detail pages? Two strategies, tried in order:

  sitemap  - robots.txt Sitemap: lines plus the usual sitemap paths, index
             files expanded one level. Cheap, complete, and what most of these
             platforms publish.
  crawl    - fetch the homepage, follow its own "te koop"/"aanbod" links one
             level, and scrape same-host anchors. Always runs alongside the
             sitemap: several sites ship a stale one (immo-lys.be lists ids
             that 404) or none with listings in it at all. A handful of
             conventional index paths is tried only when both came up empty.

Neither strategy parses detail pages; that's extract.py's job.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..extract import looks_like_listing_url
from ..models import Agency
from ..net import Fetcher

log = logging.getLogger(__name__)

SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap/sitemap-index.xml",
)

INDEX_PATHS = (
    "/te-koop",
    "/nl/te-koop",
    "/aanbod",
    "/nl/aanbod",
    "/panden",
    "/woningen",
    "/eigendommen",
    "/properties",
    "/vastgoed",
    "/immo",
    "/nl/panden",
)

MAX_SITEMAPS = 30
MAX_INDEX_PAGES = 8      # homepage links followed per crawl

# A same-site link that leads to a listing index rather than a listing.
INDEX_LINK = re.compile(
    r"te[-_]?koop|aanbod|/kopen|for-sale|/panden|/woningen|/properties|/eigendommen",
    re.IGNORECASE)

# Locale segments that agency sitemaps duplicate: several of these sites emit
# "https://host/nl//nl/te-koop/123", which 404s. Seen in the wild on Immotion.
LOCALE_SEGMENTS = ("nl", "fr", "en", "de", "nl-be", "fr-be", "en-gb")


def normalize_url(url: str) -> str:
    """Repair the malformed URLs agency sitemaps hand out.

    Collapses repeated slashes and a doubled locale segment, and drops the
    query string and fragment so the same page is never fetched twice under
    two spellings.
    """
    parsed = urlparse(url)
    path = re.sub(r"/{2,}", "/", parsed.path)
    for locale in LOCALE_SEGMENTS:
        path = re.sub(rf"^/{locale}/{locale}(/|$)", rf"/{locale}\1", path,
                      flags=re.IGNORECASE)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class GenericScraper:
    """Sitemap-first discovery. The default for every agency."""

    name = "auto"
    # Extra path fragments that mark a detail page on this platform.
    extra_url_hints: tuple[str, ...] = ()

    def __init__(self, max_listings: int = 80) -> None:
        self.max_listings = max_listings

    # ------------------------------------------------------------------ api

    def discover(self, agency: Agency, fetcher: Fetcher) -> list[str]:
        """Every candidate detail URL, uncapped: the per-run cap belongs to
        the scan, applied to URLs not seen before."""
        crawled = self._from_crawl(agency, fetcher)
        mapped = self._from_sitemaps(agency, fetcher)
        if not crawled and not mapped:
            crawled = self._from_crawl(agency, fetcher, probe_defaults=True)
        # Index pages show what is on the market today; a sitemap carries the
        # whole history. New pages are capped per run, so today's go first.
        return list(dict.fromkeys(self.canonical(u) for u in crawled + mapped))

    def canonical(self, url: str) -> str:
        """One spelling per page. Platforms whose identity lives in the query
        string override this; everyone else drops it."""
        return normalize_url(url)

    def is_listing_url(self, url: str) -> bool:
        if any(hint in url.lower() for hint in self.extra_url_hints):
            return True
        return looks_like_listing_url(url)

    # -------------------------------------------------------------- sitemap

    def _from_sitemaps(self, agency: Agency, fetcher: Fetcher) -> list[str]:
        candidates = list(fetcher.sitemaps_from_robots(agency.url))
        candidates += [agency.url + path for path in SITEMAP_PATHS]

        seen_sitemaps: set[str] = set()
        queue = list(dict.fromkeys(candidates))
        found: list[str] = []

        while queue and len(seen_sitemaps) < MAX_SITEMAPS:
            sitemap_url = queue.pop(0)
            if sitemap_url in seen_sitemaps:
                continue
            seen_sitemaps.add(sitemap_url)

            body = fetcher.get(sitemap_url)
            if not body or "<" not in body:
                continue

            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, re.IGNORECASE)
            is_index = "<sitemapindex" in body.lower()

            for loc in locs:
                if is_index or loc.lower().endswith((".xml", ".xml.gz")):
                    # Only chase sitemaps that could plausibly hold listings.
                    if self._sitemap_worth_following(loc):
                        queue.append(loc)
                elif self.is_listing_url(loc) and self._same_site(agency, loc):
                    found.append(loc)

        return found

    @staticmethod
    def _sitemap_worth_following(url: str) -> bool:
        low = url.lower()
        skip = ("author", "category", "tag", "image", "video", "news", "blog",
                "post", "comment")
        return not any(s in low for s in skip)

    # ---------------------------------------------------------------- crawl

    def _from_crawl(self, agency: Agency, fetcher: Fetcher,
                    probe_defaults: bool = False) -> list[str]:
        found: list[str] = []
        queue = [agency.url]
        if probe_defaults:
            queue += [agency.url + p for p in INDEX_PATHS]
        visited: set[str] = set()
        followed = 0

        while queue:
            page = queue.pop(0)
            if page in visited:
                continue
            visited.add(page)
            html = fetcher.get(page)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                url = urljoin(page, str(a["href"])).split("#")[0]
                if not self._same_site(agency, url):
                    continue
                if self.is_listing_url(url):
                    found.append(url)
                elif (page == agency.url and followed < MAX_INDEX_PAGES
                      and INDEX_LINK.search(urlparse(url).path)
                      and normalize_url(url) not in visited):
                    # The site's own "te koop" page: oximo.be/nl/immo-te-koop,
                    # cannoodt.be/aanbod - names no default list would guess.
                    queue.append(normalize_url(url))
                    followed += 1
            if len(found) >= self.max_listings:
                break

        return found

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _same_site(agency: Agency, url: str) -> bool:
        want = urlparse(agency.url).netloc.removeprefix("www.")
        got = urlparse(url).netloc.removeprefix("www.")
        return got == want
