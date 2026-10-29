"""The scan pipeline: discover -> skip known -> fetch -> extract.

Concurrency is per agency, never per page within an agency: the Fetcher
throttles by host, so eight agencies can be in flight at once while each
individual site still sees roughly one request per second.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from .extract import in_belgium
from .geo import postcodes_for_place
from .models import Agency, Listing, listing_id
from .net import Fetcher
from .platforms import GenericScraper, scraper_for
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
    urls = [u for u in urls if in_region_url(agency, u)]

    fresh_by_id: dict[str, str] = {}
    for url in urls:
        key = listing_id(url)
        if key not in store.entries:
            fresh_by_id.setdefault(key, url)   # www.x.be/p and x.be/p: one page
    fresh = list(fresh_by_id.values())
    log.info("%s: %d discovered, %d new", agency.name, len(urls), len(fresh))
    if len(fresh) > max_listings:
        # The cap is on *new* pages per run. Capping discovery itself would
        # refetch the same first 80 sitemap entries forever (immolietaer.be
        # lists 700, oldest first) and never reach the current ones.
        log.info("%s: capping at %d of %d new URLs", agency.name, max_listings, len(fresh))
        fresh = fresh[:max_listings]

    if discover_only:
        result.dead_urls = fresh
        return result

    for url in fresh:
        html = fetcher.get(url)
        result.fetched += 1
        if html is None:
            result.dead_urls.append(url)
            continue
        listing = scraper.parse(url, html, source=agency.name)
        if listing is None or not in_region(agency, listing):
            result.dead_urls.append(url)
            continue
        result.listings.append(listing)

    strip_shared_street(result.listings)
    return result


def recheck_status(
    listings: list[Listing],
    fetcher: Fetcher,
    today: date | None = None,
    workers: int = 8,
    agencies: list[Agency] | None = None,
) -> Counter:
    """Visit stored listings again to see whether they are still for sale.

    The one deliberate exception to "a URL is fetched once": a house that
    was sold yesterday is still on the page unless somebody looks. Listings
    are grouped by host so the per-host throttle keeps each site at one
    request per second while several sites are visited at once. With
    `agencies`, a host's own platform reads the status where the generic
    banner search would not (engelvoelkers.com states it in its data blob).
    """
    today = today or date.today()
    changes: Counter = Counter()
    readers = {urlparse(a.url).netloc.removeprefix("www."): scraper_for(a) for a in agencies or []}

    def visit(group: list[Listing]) -> Counter:
        local: Counter = Counter()
        host = urlparse(group[0].url).netloc.removeprefix("www.")
        reader = readers.get(host) or GenericScraper()
        for listing in group:
            got = fetcher.fetch(listing.url)
            if got.status_code is None:
                local["unreachable"] += 1          # network trouble: try again another day
                continue
            if got.status_code in (404, 410) or got.redirected_away:
                new = "gone"
            elif got.text is None:
                local["unreachable"] += 1
                continue
            else:
                new = reader.status(got.text, listing.url)
            listing.checked = today.isoformat()
            local["checked"] += 1
            if new != listing.status:
                local[f"{listing.status}->{new}"] += 1
                listing.status = new
        return local

    by_host: dict[str, list[Listing]] = {}
    for listing in listings:
        by_host.setdefault(urlparse(listing.url).netloc, []).append(listing)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for local in pool.map(visit, by_host.values()):
            changes.update(local)
    return changes


# "/nl/te-koop/8800-roeselare/huis/..." - chains put the postcode in the URL,
# which saves fetching the other end of the country at all.
URL_POSTCODE = re.compile(r"/([1-9]\d{3})-[a-z]", re.IGNORECASE)


def in_region_url(agency: Agency, url: str) -> bool:
    if not agency.postcodes:
        return True
    m = URL_POSTCODE.search(urlparse(url).path)
    return m is None or any(m.group(1).startswith(p) for p in agency.postcodes)


def in_region(agency: Agency, listing: Listing) -> bool:
    """Agency-level postcode guard, for chains that sell across the country,
    and the country guard: a second home on the Costa Blanca never enters
    the store. A listing without a readable postcode passes: unknown is not
    a mismatch."""
    if in_belgium(listing) is False:
        return False
    if not agency.postcodes:
        return True
    if listing.postal_code:
        return any(listing.postal_code.startswith(p) for p in agency.postcodes)
    # No postcode, but a town: when every postcode that name can mean lies
    # elsewhere, so does the house. "Antwerpen" is 2000-2660 and never 8/9;
    # a name the table does not know stays unknown and passes.
    codes = postcodes_for_place(listing.city)
    return not codes or any(c.startswith(p) for c in codes for p in agency.postcodes)


def strip_shared_street(listings: list[Listing]) -> int:
    """Blank a street that most of an agency's listings share.

    One house has one address; if the same street and number turns up on
    nearly every page of a site it is the office, printed somewhere the chrome
    detector did not look. Returns how many listings were cleared.
    """
    groups: dict[str, list[Listing]] = {}
    for l in listings:
        if l.street:
            groups.setdefault(street_key(l.street), []).append(l)
    cleared = 0
    for members in groups.values():
        n = len(members)
        if n < 3:
            continue
        # A new-build project also puts one street on many listings, but all
        # in one postcode. One street across several towns is the office.
        places = {m.postal_code or m.city.lower() for m in members if m.postal_code or m.city}
        if n >= 0.4 * len(listings) or len(places) >= 2:
            for l in members:
                l.street = ""
                cleared += 1
    return cleared


def street_key(street: str) -> str:
    """'Ja Kantoor Stationsstraat 4 bus 1' and 'Stationsstraat 4 bus 1' are the
    same door: compare on the last name word plus the number."""
    m = re.search(r"(\S+\s+\d.*)$", street)
    return (m.group(1) if m else street).lower()


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
