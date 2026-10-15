"""The scan pipeline: discover -> skip known -> fetch -> extract.

Concurrency is per agency, never per page within an agency: the Fetcher
throttles by host, so eight agencies can be in flight at once while each
individual site still sees roughly one request per second.
"""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .extract import extract_listing
from .models import Agency, Listing, listing_id
from .net import Fetcher
from .platforms import scraper_for
from .store import SeenStore

log = logging.getLogger(__name__)


@dataclass
class AgencyResult:
    agency: Agency
    discovered: int = 0
    fetched: int = 0
    listings: list[Listing] = field(default_factory=list)
    unreachable: bool = False
    # URLs we fetched but couldn't parse into a listing. Recorded as seen so
    # they are not re-fetched tomorrow.
    dead_urls: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    per_agency: list[AgencyResult] = field(default_factory=list)

    @property
    def listings(self) -> list[Listing]:
        return [l for r in self.per_agency for l in r.listings]

    @property
    def discovered(self) -> int:
        return sum(r.discovered for r in self.per_agency)

    @property
    def fetched(self) -> int:
        return sum(r.fetched for r in self.per_agency)

    @property
    def unreachable(self) -> list[Agency]:
        return [r.agency for r in self.per_agency if r.unreachable]

    @property
    def all_seen_urls(self) -> list[str]:
        """Every URL this run resolved, parsed or not."""
        urls = [l.url for l in self.listings]
        urls += [u for r in self.per_agency for u in r.dead_urls]
        return urls


def scan_agency(
    agency: Agency,
    fetcher: Fetcher,
    store: SeenStore,
    max_listings: int,
    discover_only: bool = False,
) -> AgencyResult:
    result = AgencyResult(agency=agency)
    scraper = scraper_for(agency, max_listings=max_listings)

    urls = scraper.discover(agency, fetcher)
    result.discovered = len(urls)
    if not urls:
        result.unreachable = True
        log.info("%s: no listing URLs discovered", agency.name)
        return result

    fresh = [u for u in urls if listing_id(u) not in store.entries]
    log.info("%s: %d discovered, %d new", agency.name, len(urls), len(fresh))

    if discover_only:
        result.dead_urls = fresh
        return result

    for url in fresh:
        html = fetcher.get(url)
        result.fetched += 1
        if html is None:
            result.dead_urls.append(url)
            continue
        listing = extract_listing(url, html, source=agency.name, region=agency.region)
        if listing is None:
            result.dead_urls.append(url)
            continue
        result.listings.append(listing)

    strip_shared_street(result.listings)
    return result


def strip_shared_street(listings: list[Listing]) -> int:
    """Blank a street that most of an agency's listings share.

    One house has one address; if the same street and number turns up on
    nearly every page of a site it is the office, printed somewhere the chrome
    detector did not look. Returns how many listings were cleared.
    """
    counts = Counter(l.street.lower() for l in listings if l.street)
    cleared = 0
    for street, n in counts.items():
        if n >= 3 and n >= 0.4 * len(listings):
            for l in listings:
                if l.street.lower() == street:
                    l.street = ""
                    cleared += 1
    return cleared


def scan(
    agencies: list[Agency],
    store: SeenStore,
    fetcher: Fetcher,
    max_listings: int = 80,
    workers: int = 8,
    discover_only: bool = False,
) -> ScanResult:
    result = ScanResult()

    def work(agency: Agency) -> AgencyResult:
        try:
            return scan_agency(agency, fetcher, store, max_listings, discover_only)
        except Exception as exc:  # one broken site must not end the run
            log.warning("%s: scan failed: %s", agency.name, exc)
            return AgencyResult(agency=agency, unreachable=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        result.per_agency = list(pool.map(work, agencies))

    return result
