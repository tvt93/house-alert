"""Found & Baker (found-baker.com).

A nationwide agency on one Drupal site, which the generic scraper reads
wrongly in two ways:

  * only about half the catalogue is filed under /nl/te-koop/<slug>; the rest
    sits at /nl/<slug>, a shape no URL hint can tell from an office or
    journal page, and the sitemap mixes in what was sold years ago. The
    catalogue itself is /nl/te-koop, paged with ``?page=N``, one card per
    property with its status as a class (``c-card--for-sale``,
    ``c-card--option-for-sale``, ``c-card--sold``);
  * a detail page ends in a "related" strip of such cards, with the price
    printed beside the link instead of inside it. Most properties here are
    "Prijs op aanvraag", so the generic reader gave the house the price of
    its dearest neighbour.

Discovery therefore reads the catalogue's for-sale cards, and parsing drops
every card before the generic reader sees the page. What the page states
about the property itself is in its hero block: title, a status label
("In optie"), "<street>, <town>" or the town alone, and the price.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..extract import AMOUNT, PRICE_CEILING, PRICE_FLOOR, _int, detect_status, extract_listing
from ..geo import postcodes_for_place
from ..models import Agency, Listing
from ..net import Fetcher
from .base import GenericScraper

log = logging.getLogger(__name__)

INDEX_PATH = "/nl/te-koop"
MAX_PAGES = 12           # the catalogue is two pages of ~90 today

HERO = "c-publication-hero"
LABEL_OPTION = re.compile(r"optie|option", re.IGNORECASE)
LABEL_SOLD = re.compile(r"verkocht|sold|vendu", re.IGNORECASE)

# The page names a town and never a postcode. The bundled table knows the
# Brussels communes by their French names only, and this site writes Dutch.
BRUSSELS = {
    "brussel": "1000", "laken": "1020", "schaarbeek": "1030", "etterbeek": "1040",
    "elsene": "1050", "sint-gillis": "1060", "anderlecht": "1070",
    "sint-jans-molenbeek": "1080", "koekelberg": "1081", "sint-agatha-berchem": "1082",
    "ganshoren": "1083", "jette": "1090", "evere": "1140", "sint-pieters-woluwe": "1150",
    "oudergem": "1160", "watermaal-bosvoorde": "1170", "ukkel": "1180",
    "sint-lambrechts-woluwe": "1200", "sint-joost-ten-node": "1210",
}


def town_postcode(town: str) -> str:
    """The postcode a town name can only mean, else nothing: never a guess."""
    key = re.sub(r"\s+", " ", town.strip().lower())
    if key in BRUSSELS:
        return BRUSSELS[key]
    codes = postcodes_for_place(town)
    return codes[0] if len(codes) == 1 else ""


def for_sale_links(html: str, page: str) -> tuple[list[str], int]:
    """The detail URLs of the cards marked for sale, and how many cards the
    page holds in all (none means the paging ran out)."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("article.c-card--publication")
    links: list[str] = []
    for card in cards:
        if "c-card--for-sale" not in (card.get("class") or []):
            continue
        a = card.find("a", href=True)
        if a is not None:
            links.append(urljoin(page, str(a["href"])).split("#")[0])
    return links, len(cards)


def without_cards(html: str) -> str:
    """The page minus every property card: what is left is about one house."""
    soup = BeautifulSoup(html, "lxml")
    for node in soup.select("section.c-property-list, article.c-card"):
        node.decompose()
    return str(soup)


def hero_text(soup: BeautifulSoup, part: str) -> str | None:
    """Text of one hero element; None when the page has no such element."""
    node = soup.select_one(f".{HERO}__{part}")
    if node is None:
        return None
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True))


class FoundBakerScraper(GenericScraper):
    name = "foundbaker"

    # ------------------------------------------------------------ discover

    def discover(self, agency: Agency, fetcher: Fetcher) -> list[str]:
        starts = list(agency.index_urls) or [agency.url.rstrip("/") + INDEX_PATH]
        found: list[str] = []
        for start in starts:
            for number in range(MAX_PAGES):
                page = start if number == 0 else f"{start}?page={number}"
                html = fetcher.get(page)
                if not html:
                    break
                links, cards = for_sale_links(html, page)
                new = [u for u in links if self._same_site(agency, u) and u not in found]
                log.debug("%s: %d for sale on %s", agency.name, len(new), page)
                found.extend(new)
                if not cards or (number and not new):
                    break                    # past the last page, or it repeats
        return list(dict.fromkeys(self.canonical(u) for u in found))

    # --------------------------------------------------------------- parse

    def parse(self, url: str, html: str, source: str) -> Listing | None:
        own = without_cards(html)
        soup = BeautifulSoup(own, "lxml")
        if soup.select_one(f".{HERO}") is None:
            return None                      # the catalogue, a project, an office
        listing = extract_listing(url, own, source)
        if listing is None:
            return None

        # The hero is the one place the page prices this house; whatever the
        # text search found elsewhere is not it.
        price = hero_text(soup, "price")
        if price is not None:
            m = re.search(rf"€\s*{AMOUNT}", price)
            amount = _int(m.group(1)) if m else None
            listing.price = amount if amount and PRICE_FLOOR <= amount <= PRICE_CEILING else None

        title = hero_text(soup, "title")
        if title:
            listing.title = title

        # "Bosstraat, Sint-Martens-Latem" or just "Gent".
        address = hero_text(soup, "address")
        if address:
            street, _, town = address.rpartition(",")
            town = town.strip()
            if town and not listing.postal_code:
                listing.city = town
                listing.postal_code = town_postcode(town)
            if not listing.street and street.strip():
                listing.street = street.strip()
        return listing

    def status(self, html: str, url: str = "") -> str:
        own = without_cards(html)
        label = hero_text(BeautifulSoup(own, "lxml"), "label") or ""
        if LABEL_SOLD.search(label):
            return "sold"
        if LABEL_OPTION.search(label):
            return "option"
        return detect_status(own, url)
