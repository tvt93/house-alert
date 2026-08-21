"""Field extraction from a listing detail page.

Two passes, cheapest first:

1. schema.org JSON-LD, which most modern estate-agency platforms emit. When
   present it is authoritative.
2. Dutch-language text heuristics over the rendered page, which is what
   actually carries the day on the older Whise/Skarabee themes.

Anything we can't determine stays None. `include_unknown` in criteria.yaml
decides whether an unknown field is disqualifying, so guessing is never
necessary - and a wrong guess is worse than a blank.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import Listing

# --------------------------------------------------------------------- tables

TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # Checked in order; first hit wins, so put the narrow ones first.
    ("commercial", (
        "handelspand", "handelsruimte", "kantoor", "winkelpand", "winkelruimte",
        "bedrijfsvastgoad", "bedrijfsvastgoed", "bedrijfsgebouw", "loods",
        "opbrengsteigendom", "opbrengsteigendommen", "horeca", "praktijkruimte",
    )),
    ("land", (
        "bouwgrond", "bouwgronden", "bouwkavel", "projectgrond", "landbouwgrond",
        "perceel bouwgrond", "grond te koop",
    )),
    ("apartment", (
        "appartement", "apartment", "penthouse", "duplex", "triplex", "studio",
        "gelijkvloers appartement", "assistentiewoning", "flat",
    )),
    ("house", (
        "woning", "huis", "villa", "rijwoning", "rijhuis", "hoeve", "herenhuis",
        "bel-etage", "bel etage", "bungalow", "fermette", "koppelwoning",
        "halfopen bebouwing", "open bebouwing", "gesloten bebouwing",
        "eengezinswoning", "landhuis", "cottage", "house",
    )),
]

# Presence of these means the listing is no longer on the market.
UNAVAILABLE_MARKERS = (
    "verkocht", "verhuurd", "in optie", "onder optie", "onder bod",
    "geen beschikbare", "sold", "rented",
)

# URL path fragments that mark a page as a listing detail page.
LISTING_URL_HINTS = (
    "/te-koop/", "/tekoop/", "/pand/", "/panden/", "/woning/", "/woningen/",
    "/aanbod/", "/eigendom/", "/eigendommen/", "/property/", "/properties/",
    "/estate/", "/vastgoed/", "/immo/", "/for-sale/", "/detail/", "/objecten/",
    "/realisatie/", "/aanbod-", "/te-koop-",
)

# ...and fragments that mean it definitely is not one.
LISTING_URL_ANTI_HINTS = (
    "/te-huur", "/tehuur/", "/for-rent", "/verhuur", "/nieuws", "/blog",
    "/contact", "/over-ons", "/team", "/diensten", "/schatting", "/vacature",
    "/privacy", "/cookie", "/algemene-voorwaarden", "/verkocht", "/references",
    "/nieuwbouw", "/projecten", "/gerealiseerd", "/page/", "/tag/", "/author/",
)

PRICE_FLOOR = 25_000
PRICE_CEILING = 5_000_000

# A Belgian-formatted amount: 395.000 / 395 000 / 395000. Crucially it must not
# match across a separator into the next number on the page, so whitespace and
# dots are only allowed when followed by exactly three digits.
AMOUNT = r"(\d{1,3}(?:[.   ]\d{3})+|\d{4,8})"

# 9000 Gent / 8500 Kortrijk / 9040 Sint-Amandsberg / 9840 De Pinte
POSTAL_CITY = (
    r"\b([1-9]\d{3})\s+"
    r"((?:De|Den|Sint|Groot|Nieuw)\s+)?"
    r"([A-Z][a-zà-ÿ]{2,}(?:-[A-Z][a-zà-ÿ]+)*)"
)

# Capitalised words that follow a four-digit number on agency sites but are not
# places. Mostly labels attached to a construction year, which is numerically
# indistinguishable from a Belgian postcode.
NOT_A_CITY = {
    "vraagprijs", "prijs", "price", "epc", "ref", "referentie", "telefoon",
    "gsm", "email", "belgie", "belgië", "bel", "contact", "kantoor",
    "renovatiejaar", "bouwjaar", "jaar", "gebouwd", "richting", "bouwjaren",
    "oppervlakte", "bewoonbare", "kadastraal", "verkoop", "sinds", "btw",
}

# "Woning te koop in Aalter", "Appartement te huur te Gent"
CITY_IN_TITLE = (
    r"\bte\s+(?:koop|huur)\s+(?:in|te)\s+"
    r"([A-Z][a-zà-ÿ]{2,}(?:[-\s][A-Z][a-zà-ÿ]+)*)"
)
# "Kortrijk - Charmante te renoveren burgerwoning"
CITY_LEADS_TITLE = r"^([A-Z][a-zà-ÿ]{2,}(?:[-\s][A-Z][a-zà-ÿ]+)*)\s+[-–|]\s+"
# ".../te-koop/9140-Temse/..." and ".../woning-te-koop-astene"
CITY_IN_URL_WITH_POSTCODE = r"/([1-9]\d{3})-([A-Za-z][A-Za-z-]{2,30})(?:/|$)"
CITY_IN_URL_SLUG = r"/[\w-]*te-koop-([a-z][a-z-]{2,30})(?:/|$)"
CITY_IN_URL_IN = r"/[\w-]*?-in-([a-z][a-z-]{2,30})(?:/|$)"


# -------------------------------------------------------------------- helpers

def _num(raw: str | None) -> float | None:
    """Parse a Belgian-formatted number: 350.000 -> 350000, 123,5 -> 123.5."""
    if not raw:
        return None
    s = re.sub(r"[^\d.,]", "", raw)
    if not s:
        return None
    # A dot followed by exactly three digits is a thousands separator.
    s = re.sub(r"\.(?=\d{3}\b)", "", s)
    s = s.replace(",", ".")
    # Collapse any remaining stray dots beyond the first.
    parts = s.split(".")
    if len(parts) > 2:
        s = parts[0] + "." + "".join(parts[1:])
    try:
        return float(s)
    except ValueError:
        return None


def _int(raw: str | None) -> int | None:
    v = _num(raw)
    return int(round(v)) if v is not None else None


def _first(text: str, patterns: tuple[str, ...]) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


def looks_like_listing_url(url: str) -> bool:
    """Is this a listing *detail* page?

    A hint alone is not enough: "/te-koop/" is also the index page that lists
    them all, and index pages are full of euro amounts that would otherwise be
    scraped into one nonsense listing. So the hint must be followed by
    something that identifies an individual property.
    """
    path = urlparse(url).path.lower()
    if any(bad in path for bad in LISTING_URL_ANTI_HINTS):
        return False
    for hint in LISTING_URL_HINTS:
        index = path.find(hint)
        if index == -1:
            continue
        if path[index + len(hint):].strip("/"):
            return True
    return False


def guess_property_type(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for ptype, keywords in TYPE_KEYWORDS:
        if any(kw in blob for kw in keywords):
            return ptype
    return "unknown"


# ------------------------------------------------------------------- JSON-LD

def jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _from_jsonld(objects: list[dict[str, Any]], listing: Listing) -> None:
    for obj in objects:
        types = obj.get("@type") or ""
        types = " ".join(types) if isinstance(types, list) else str(types)
        types = types.lower()

        if "offer" in types or "price" in obj:
            price = _int(str(obj.get("price") or obj.get("lowPrice") or ""))
            if price and PRICE_FLOOR <= price <= PRICE_CEILING and listing.price is None:
                listing.price = price

        if any(t in types for t in ("residence", "house", "apartment", "product",
                                    "place", "realestatelisting", "offer")):
            if not listing.title and obj.get("name"):
                listing.title = str(obj["name"]).strip()
            if not listing.description and obj.get("description"):
                listing.description = str(obj["description"]).strip()

            rooms = obj.get("numberOfBedrooms") or obj.get("numberOfRooms")
            if isinstance(rooms, dict):
                rooms = rooms.get("value")
            if rooms is not None and listing.bedrooms is None:
                listing.bedrooms = _int(str(rooms))

            size = obj.get("floorSize")
            if isinstance(size, dict):
                size = size.get("value")
            if size is not None and listing.living_area_m2 is None:
                listing.living_area_m2 = _int(str(size))

            lot = obj.get("lotSize")
            if isinstance(lot, dict):
                lot = lot.get("value")
            if lot is not None and listing.plot_area_m2 is None:
                listing.plot_area_m2 = _int(str(lot))

        # Only trust an address hanging off the property itself. Agency sites
        # also emit an Organization/RealEstateAgent block carrying the office
        # address, which would otherwise be stamped on every listing.
        is_property = any(t in types for t in (
            "residence", "house", "apartment", "place", "realestatelisting",
            "accommodation", "product",
        )) and not any(t in types for t in (
            "organization", "localbusiness", "realestateagent", "website",
            "webpage", "breadcrumblist",
        ))

        addr = obj.get("address") if is_property else None
        if isinstance(addr, dict):
            if not listing.city and addr.get("addressLocality"):
                listing.city = str(addr["addressLocality"]).strip()
            if not listing.postal_code and addr.get("postalCode"):
                listing.postal_code = str(addr["postalCode"]).strip()

        img = obj.get("image")
        if img and not listing.image_url:
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str):
                listing.image_url = img


# ----------------------------------------------------------------- heuristics

def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", property=name) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _from_text(text: str, listing: Listing) -> None:
    if listing.price is None:
        # Prefer a labelled price, fall back to the largest plausible euro
        # figure on the page (agency sites love putting the price in a banner).
        labelled = _int(_first(text, (
            rf"(?:vraagprijs|verkoopprijs|prijs|price)\s*:?\s*(?:€\s*)?{AMOUNT}",
        )))
        candidates = price_candidates(text)
        if labelled is not None and PRICE_FLOOR <= labelled <= PRICE_CEILING:
            listing.price = labelled
        elif candidates:
            listing.price = max(candidates)

    if listing.bedrooms is None:
        raw = _first(text, (
            r"(\d{1,2})\s*slaapkamers?",
            r"slaapkamers?\s*[:\s]\s*(\d{1,2})",
            r"(\d{1,2})\s*bedrooms?",
            r"aantal\s+slaapkamers?\D{0,10}(\d{1,2})",
        ))
        bedrooms = _int(raw)
        listing.bedrooms = bedrooms if bedrooms is not None and bedrooms <= 20 else None

    if listing.living_area_m2 is None:
        raw = _first(text, (
            r"bewoonbare\s+opp\w*\D{0,15}([\d.,]{2,8})",
            r"bewoonbaar\D{0,15}([\d.,]{2,8})\s*m",
            r"woonopp\w*\D{0,15}([\d.,]{2,8})",
            r"([\d.,]{2,8})\s*m²?\s*bewoonbaar",
            r"living\s+area\D{0,15}([\d.,]{2,8})",
        ))
        listing.living_area_m2 = _plausible_area(_int(raw), 15, 2000)

    if listing.plot_area_m2 is None:
        raw = _first(text, (
            r"grondopp\w*\D{0,15}([\d.,]{2,9})",
            r"perceelopp\w*\D{0,15}([\d.,]{2,9})",
            r"opp\w*\s+perceel\D{0,15}([\d.,]{2,9})",
            r"terreinopp\w*\D{0,15}([\d.,]{2,9})",
            r"totale\s+opp\w*\D{0,15}([\d.,]{2,9})",
        ))
        listing.plot_area_m2 = _plausible_area(_int(raw), 20, 200_000)

    if listing.epc_kwh is None:
        raw = _first(text, (
            r"epc\D{0,25}?(\d{1,4})\s*kwh",
            r"(\d{1,4})\s*kwh\s*/\s*m",
            r"e-?peil\D{0,10}(\d{1,4})",
            r"epc\s*[:\s]\s*(\d{1,4})\b",
        ))
        epc = _int(raw)
        listing.epc_kwh = epc if epc is not None and 0 < epc <= 1500 else None


def _locate(listing: Listing, text: str) -> None:
    """Fill in city/postcode, most trustworthy source first.

    Page text is the last resort on purpose: an agency's own address sits in
    the header or footer of every single listing page, so trusting the text
    first would stamp the office's municipality onto the whole portfolio.
    JSON-LD (already applied) and the URL describe the actual property.
    """
    url, title = listing.url, listing.title

    if not (listing.postal_code and listing.city):
        m = re.search(CITY_IN_URL_WITH_POSTCODE, url)
        if m:
            listing.postal_code = listing.postal_code or m.group(1)
            listing.city = listing.city or _titleize(m.group(2))

    if not listing.city:
        for pattern in (CITY_IN_TITLE, CITY_LEADS_TITLE):
            m = re.search(pattern, title)
            if m and m.group(1).lower() not in NOT_A_CITY:
                listing.city = m.group(1).strip()
                break

    if not listing.city:
        for pattern in (CITY_IN_URL_SLUG, CITY_IN_URL_IN):
            m = re.search(pattern, url.lower())
            if m:
                listing.city = _titleize(m.group(1))
                break

    if not listing.postal_code:
        for m in re.finditer(POSTAL_CITY, text):
            city = f"{m.group(2) or ''}{m.group(3)}".strip()
            if city.lower() in NOT_A_CITY:
                continue
            # Only accept a postcode from the page body when it agrees with the
            # city we already trust, or when we have no city at all.
            if listing.city and city.lower() != listing.city.lower():
                continue
            listing.postal_code = m.group(1)
            listing.city = listing.city or city
            break


def _titleize(slug: str) -> str:
    return "-".join(part.capitalize() for part in slug.split("-"))


def strip_priced_links(soup: BeautifulSoup) -> int:
    """Remove <a> subtrees that contain a euro amount.

    This is the single most useful structural fact about agency pages: a price
    wrapped in a link belongs to a *different* property - a card in a grid, a
    "similar properties" strip, a recently-sold teaser. The price of the
    property the page is actually about is never a link to somewhere else.

    Returns the number of subtrees removed.
    """
    removed = 0
    for anchor in soup.find_all("a"):
        if re.search(rf"€\s*{AMOUNT}", anchor.get_text(" ", strip=True)):
            anchor.decompose()
            removed += 1
    return removed


def price_candidates(text: str) -> set[int]:
    return {
        v for v in (_int(m) for m in re.findall(rf"€\s*{AMOUNT}", text))
        if v is not None and PRICE_FLOOR <= v <= PRICE_CEILING
    }


def has_labelled_price(text: str) -> bool:
    return bool(re.search(r"(?:vraagprijs|verkoopprijs)\s*:?\s*(?:€|\d)",
                          text, re.IGNORECASE))


def is_index_page(text: str) -> bool:
    """Does this page advertise many properties rather than one?

    Some agencies publish per-municipality landing pages under exactly the same
    /te-koop/<slug> shape as real listings - landbergh.be/te-koop/
    woning-te-koop-astene is a grid of Astene properties, not one house.
    Scraping one produces a listing carrying some other house's asking price,
    which is worse than no listing at all.

    Call this on text from which priced links have already been stripped: what
    is left should be one property's price, not five.
    """
    if has_labelled_price(text):
        return False
    return len(price_candidates(text)) >= 3


def _plausible_area(value: int | None, low: int, high: int) -> int | None:
    if value is None:
        return None
    return value if low <= value <= high else None


# -------------------------------------------------------------------- entry

def extract_listing(url: str, html: str, source: str, region: str = "") -> Listing | None:
    """Turn a detail page into a Listing, or None if it isn't a live listing."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    listing = Listing(url=url, source=source, region=region)

    listing.title = _meta(soup, "og:title", "twitter:title")
    listing.description = _meta(soup, "og:description", "description")
    listing.image_url = _meta(soup, "og:image", "twitter:image")

    _from_jsonld(jsonld_objects(BeautifulSoup(html, "lxml")), listing)

    if not listing.title:
        h1 = soup.find("h1")
        listing.title = h1.get_text(" ", strip=True) if h1 else ""
    if not listing.title and soup.title:
        listing.title = soup.title.get_text(" ", strip=True)

    # Drop the cards, teasers and "similar properties" strips before reading any
    # numbers off the page, so nothing here can pick up a neighbour's price.
    strip_priced_links(soup)
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    if is_index_page(text):
        return None

    _from_text(text, listing)
    _locate(listing, text)

    if listing.image_url:
        listing.image_url = urljoin(url, listing.image_url)

    listing.property_type = guess_property_type(listing.title, url, listing.description)

    # A page with no price and no measurable property fact is almost certainly
    # an index, a landing page, or a JS shell we can't read.
    has_substance = any((
        listing.price is not None,
        listing.bedrooms is not None,
        listing.living_area_m2 is not None,
        listing.plot_area_m2 is not None,
    ))
    if not has_substance:
        return None

    # Drop anything explicitly marked as gone. Only check the top of the page
    # so a "recently sold" sidebar doesn't sink a live listing.
    head = text[:1200].lower()
    if any(marker in head for marker in UNAVAILABLE_MARKERS):
        return None

    return listing
