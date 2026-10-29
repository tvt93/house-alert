"""Per-platform scrapers.

Nearly every Belgian agency runs its listings on one of a handful of shared
platforms rather than bespoke code. Recognising the platform lets us use the
URL shapes it is known to produce instead of guessing, which is why
`house-alert detect` is worth running before the first real scan.

The subclasses below currently only tune *discovery* (which URLs are detail
pages). They deliberately do not claim to speak any platform's private API -
if you find a documented JSON endpoint for one of them, override `discover`
there and the rest of the pipeline needs no changes.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ..models import Agency
from .base import GenericScraper
from .engelvoelkers import EngelVoelkersScraper


class WhiseScraper(GenericScraper):
    name = "whise"
    extra_url_hints = ("/estate/", "/estates/", "/pand-", "/detail/")


class SweepBrightScraper(GenericScraper):
    name = "sweepbright"
    extra_url_hints = ("/properties/", "/property/", "/estates/")


class OmnicasaScraper(GenericScraper):
    """Omnicasa (Joomla) addresses a property as /nl/te-koop?view=detail&id=795:
    the path is the index page, the identity is in the query string."""
    name = "omnicasa"
    extra_url_hints = ("/detail.aspx", "/property-detail", "/nl/detail")

    @staticmethod
    def _detail_id(url: str) -> str | None:
        qs = parse_qs(urlparse(url).query)
        if qs.get("view") == ["detail"] and qs.get("id", [""])[0].isdigit():
            return qs["id"][0]
        return None

    def is_listing_url(self, url: str) -> bool:
        return self._detail_id(url) is not None or super().is_listing_url(url)

    def canonical(self, url: str) -> str:
        detail = self._detail_id(url)
        if detail is None:
            return super().canonical(url)
        return f"{super().canonical(url)}?view=detail&id={detail}"


class SkarabeeScraper(GenericScraper):
    """Skarabee detail pages end in the property number: mvastgoed.be/nl/
    kortrijk-moeskroensesteenweg-14/7542404, demoorvastgoed.be/detail/<slug>/7683307."""
    name = "skarabee"
    extra_url_hints = ("/te-koop/", "/pand/", "/nl/pand")
    _NUMERIC_TAIL = re.compile(r"/[^/]+/\d{6,}/?$")

    def is_listing_url(self, url: str) -> bool:
        if self._NUMERIC_TAIL.search(urlparse(url).path) and not any(
                bad in url.lower() for bad in ("te-huur", "/files/", "/m-services/")):
            return True
        return super().is_listing_url(url)


SCRAPERS: dict[str, type[GenericScraper]] = {
    GenericScraper.name: GenericScraper,
    WhiseScraper.name: WhiseScraper,
    SweepBrightScraper.name: SweepBrightScraper,
    OmnicasaScraper.name: OmnicasaScraper,
    SkarabeeScraper.name: SkarabeeScraper,
    EngelVoelkersScraper.name: EngelVoelkersScraper,
}


def scraper_for(agency: Agency, max_listings: int = 80) -> GenericScraper:
    cls = SCRAPERS.get(agency.platform.lower(), GenericScraper)
    return cls(max_listings=max_listings)


__all__ = ["GenericScraper", "SCRAPERS", "scraper_for"]
