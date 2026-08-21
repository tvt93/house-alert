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

from ..models import Agency
from .base import GenericScraper


class WhiseScraper(GenericScraper):
    name = "whise"
    extra_url_hints = ("/estate/", "/estates/", "/pand-", "/detail/")


class SweepBrightScraper(GenericScraper):
    name = "sweepbright"
    extra_url_hints = ("/properties/", "/property/", "/estates/")


class OmnicasaScraper(GenericScraper):
    name = "omnicasa"
    extra_url_hints = ("/detail.aspx", "/property-detail", "/nl/detail")


class SkarabeeScraper(GenericScraper):
    name = "skarabee"
    extra_url_hints = ("/te-koop/", "/pand/", "/nl/pand")


SCRAPERS: dict[str, type[GenericScraper]] = {
    GenericScraper.name: GenericScraper,
    WhiseScraper.name: WhiseScraper,
    SweepBrightScraper.name: SweepBrightScraper,
    OmnicasaScraper.name: OmnicasaScraper,
    SkarabeeScraper.name: SkarabeeScraper,
}


def scraper_for(agency: Agency, max_listings: int = 80) -> GenericScraper:
    cls = SCRAPERS.get(agency.platform.lower(), GenericScraper)
    return cls(max_listings=max_listings)


__all__ = ["GenericScraper", "SCRAPERS", "scraper_for"]
