"""Engel & Völkers (engelvoelkers.com).

A nationwide chain on one Next.js site for the whole country, which breaks
every assumption the generic scraper makes:

  * the sitemap carries no listings at all - only company pages;
  * the catalogue is server-rendered on index pages such as
    /be/nl/eigendom/res/koop/huis/vlaams-gewest, newest first, 35 per page
    and with no server-side pagination (``?page=2`` returns page one);
  * a detail page ("expose") prints the town but never a street or postcode,
    and its "similar homes" strip is indistinguishable from the property in
    the page text - the generic reader took a neighbour's title and the plot
    size as living area.

Everything the page knows sits in the ``__NEXT_DATA__`` blob instead:
price, type, bedrooms, surfaces, the Google place names and the agency's
own map point, which it says it moved by up to ``fuzzyMoveByMeter``. That
point is kept with ``geo = "approx"`` so the page draws it as approximate.

The index pages to read are the agency's ``index_urls`` (region per property
type, plus the town pages worth a closer look). Newest first means the 35
per page are always the latest, so a daily run never misses a new listing
as long as fewer than 35 appear per type between runs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..extract import detect_status
from ..geo import postcodes_for_place
from ..models import Agency, Listing
from ..net import Fetcher
from .base import GenericScraper

log = logging.getLogger(__name__)

EXPOSE_PATH = re.compile(r"/[a-z]{2}/nl/exposes/[0-9a-f]{8}-[0-9a-f-]{27}/?$")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
OG_TITLE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.IGNORECASE)
IMAGE_HOST = "https://uploadcare.engelvoelkers.com/"

PROPERTY_TYPES = {
    "house": "house",
    "apartment": "apartment",
    "land": "land",
    "plot": "land",
    "commercial": "commercial",
    "office": "commercial",
    "retail": "commercial",
    "industrial": "commercial",
    "investment": "commercial",
    "parking": "parking",
    "garage": "parking",
}


class EngelVoelkersScraper(GenericScraper):
    name = "engelvoelkers"

    def is_listing_url(self, url: str) -> bool:
        return EXPOSE_PATH.search(urlparse(url).path.lower()) is not None

    # ------------------------------------------------------------ discover

    def discover(self, agency: Agency, fetcher: Fetcher) -> list[str]:
        """Expose links from the configured index pages. No sitemap: the
        site's own lists nothing but company pages there."""
        pages = list(agency.index_urls) or [agency.url]
        found: list[str] = []
        for page in pages:
            html = fetcher.get(page)
            if not html:
                log.debug("%s: index page unreadable: %s", agency.name, page)
                continue
            soup = BeautifulSoup(html, "lxml")
            before = len(found)
            for a in soup.find_all("a", href=True):
                url = urljoin(page, str(a["href"])).split("#")[0]
                if self._same_site(agency, url) and self.is_listing_url(url):
                    found.append(url)
            log.debug("%s: %d exposes on %s", agency.name, len(found) - before, page)
        return list(dict.fromkeys(self.canonical(u) for u in found))

    # --------------------------------------------------------------- parse

    def parse(self, url: str, html: str, source: str) -> Listing | None:
        record = listing_record(html)
        if record is None:
            return None
        return listing_from_record(url, record, source, html)

    def status(self, html: str, url: str = "") -> str:
        record = listing_record(html)
        known = status_from_record(record) if record else None
        return known or detect_status(html, url)


def listing_record(html: str) -> dict[str, Any] | None:
    """The listing object from the page's ``__NEXT_DATA__``, or None."""
    m = NEXT_DATA.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    queries = (((data.get("props") or {}).get("pageProps") or {})
               .get("dehydratedState") or {}).get("queries") or []
    for query in queries:
        key = query.get("queryKey") or []
        if not key or key[0] != "listing":
            continue
        state = ((query.get("state") or {}).get("data") or {})
        record = state.get("listing")
        if isinstance(record, dict):
            return record
    return None


def status_from_record(record: dict[str, Any]) -> str | None:
    """``statusNormalized`` as the store spells it; None when the value is
    unfamiliar, so the page text decides as it does for every other site."""
    raw = str(record.get("statusNormalized") or "").upper()
    if raw == "ACTIVE":
        return "available"
    if "SOLD" in raw or "RENTED" in raw:
        return "sold"
    if "RESERV" in raw or "OPTION" in raw:
        return "option"
    return None


def _single(block: Any) -> float | None:
    """A ``{"min": x, "max": x}`` block as one value, None for a range: a
    project with units from 300 to 600 m² has no single living area."""
    if not isinstance(block, dict):
        return None
    low, high = block.get("min"), block.get("max")
    if low is None or high is None or low != high:
        return None
    try:
        return float(low)
    except (TypeError, ValueError):
        return None


def _whole(value: float | None) -> int | None:
    return None if value is None else int(round(value))


def _place_names(record: dict[str, Any]) -> tuple[str, str]:
    """(municipality, sub-municipality) as Google names them: "Merelbeke-Melle"
    and "Lemberge". Either may be empty."""
    locality = sub = ""
    for component in record.get("addressComponents") or []:
        if not isinstance(component, dict):
            continue
        kind, text = str(component.get("placeType") or ""), str(component.get("text") or "").strip()
        if kind == "locality":
            locality = text
        elif kind.startswith("sublocality") and not sub:
            sub = text
    return locality, sub


def _unique_postcode(name: str) -> str:
    codes = postcodes_for_place(name)
    return codes[0] if len(codes) == 1 else ""


def _image(record: dict[str, Any], html: str) -> str:
    images = record.get("propertyImages") or record.get("uploadCareImages") or []
    main = next((i for i in images if isinstance(i, dict) and i.get("main")), None)
    first = main or next((i for i in images if isinstance(i, dict)), None)
    if first and first.get("id"):
        return f"{IMAGE_HOST}{first['id']}/"
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', html, re.IGNORECASE)
    return m.group(1) if m else ""


def listing_from_record(url: str, record: dict[str, Any], source: str, html: str = "") -> Listing | None:
    """A Listing from the page's own data, or None for what is not a Belgian
    property for sale and still available."""
    if str(record.get("countryAlpha2") or "BE").upper() != "BE":
        return None
    marketing = str(record.get("propertyMarketingType") or record.get("marketingType") or "sale")
    if marketing.lower() != "sale":
        return None
    status = status_from_record(record)
    if status not in (None, "available"):
        return None

    profile = record.get("profile") or {}
    listing = Listing(url=url, source=source)
    listing.title = _clean(profile.get("title")) or _og_title(html)
    listing.description = _clean(profile.get("description"))
    listing.image_url = _image(record, html)

    if not record.get("hasPriceOnRequest"):
        price = _single((record.get("price") or {}).get("salesPrice"))
        listing.price = _whole(price)

    kind = str(record.get("propertyType") or "").lower()
    sub_kind = str(record.get("propertySubType") or "").lower()
    listing.property_type = PROPERTY_TYPES.get(kind) or PROPERTY_TYPES.get(sub_kind) or "unknown"

    listing.bedrooms = _whole(_single(record.get("bedrooms")))
    area = record.get("area") or {}
    listing.living_area_m2 = _whole(_single(area.get("livingSurface")))
    listing.plot_area_m2 = _whole(_single(area.get("plotSurface")))

    locality, sub = _place_names(record)
    listing.city = locality or sub
    # The sub-municipality names the postcode more often than the merged
    # municipality does: "Lemberge" is 9820, "Merelbeke-Melle" is two codes.
    listing.postal_code = _unique_postcode(sub) or _unique_postcode(locality)

    lat, lon = record.get("displayLat"), record.get("displayLng")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        if 49.4 < lat < 51.6 and 2.4 < lon < 6.5:
            listing.lat, listing.lon, listing.geo = float(lat), float(lon), "approx"

    if listing.price is None and listing.bedrooms is None and listing.living_area_m2 is None \
            and listing.plot_area_m2 is None:
        return None
    return listing


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _og_title(html: str) -> str:
    m = OG_TITLE.search(html or "")
    return _clean(m.group(1)) if m else ""
