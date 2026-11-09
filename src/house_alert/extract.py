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
import math
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, unquote, unquote_plus, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

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
        "perceel bouwgrond", "grond te koop", "terrain à bâtir", "terrain a batir",
    )),
    ("apartment", (
        "appartement", "apartment", "penthouse", "duplex", "triplex", "studio",
        "gelijkvloers appartement", "assistentiewoning", "flat",
    )),
    ("house", (
        "woning", "huis", "villa", "rijwoning", "rijhuis", "hoeve", "herenhuis",
        "bel-etage", "bel etage", "bungalow", "fermette", "koppelwoning",
        "halfopen bebouwing", "open bebouwing", "gesloten bebouwing",
        "eengezinswoning", "landhuis", "cottage", "house", "maison",
    )),
]

# A garage or parking space sold on its own. Only the title and URL decide,
# and only when they name no other type: "woning met dubbele garage" is a
# house, "Inpandige autostaanplaats nabij station Gent-Sint-Pieters" is not.
# The word may close a compound ("privégarage", "dubbelgarage", "parkeergarage")
# but never open one: a "garagepoort" is a door, not a garage.
PARKING_WORD = re.compile(
    r"(?<![\w-])[^\W\d_]*?(?:garage(?:s|box|boxen|complex)?|autostaanplaats(?:en)?|"
    r"staanplaats(?:en)?|parkeerplaats(?:en)?|parking(?:s)?|parkeerplek(?:ken)?|"
    r"carport(?:s)?|autobox(?:en)?)\b", re.IGNORECASE)
# "... met garage", "tuin en carport", "incl. staanplaats", "3 slpks, garage,
# tuin", "avec double garage": the garage comes with something else, named
# or not.
PARKING_AS_EXTRA = re.compile(
    r"(?:\bmet|\ben|\bincl\.?|\binclusief|\bwith|\band|\+|&|\bplus|\bof|\bavec|\bet|,)\s*"
    r"(?:\d+\s*)?"
    r"(?:dubbele\s+|ruime\s+|grote\s+|losstaande\s+|inpandige\s+|ondergrondse\s+|"
    r"afgesloten\s+|private\s+|privatieve\s+|eigen\s+|extra\s+|double\s+|un\s+|une\s+)*$",
    re.IGNORECASE)

# Presence of these means the listing is no longer on the market.
UNAVAILABLE_MARKERS = (
    "verkocht", "verhuurd", "in optie", "onder optie", "onder bod",
    "geen beschikbare", "sold", "rented",
)

# Status banners, strongest first. "compromis in opmaak", "onder optie" and
# "verkocht onder voorbehoud" all mean the house is spoken for but not gone.
STATUS_SOLD = re.compile(
    r"(?<!recent )(?<!recente )(?<!onze )"                 # "Recent verkocht" is a menu item
    r"\b(?:verkocht|verhuurd|sold|rented|vendu|lou[ée])\b(?!\s+onder\s+voorbehoud)",
    re.IGNORECASE)
STATUS_OPTION = re.compile(
    r"\b(?:in\s+optie|onder\s+optie|optie\s+genomen|onder\s+bod|onder\s+voorbehoud|compromis|"
    r"in\s+onderhandeling|gereserveerd|reserved|under\s+offer|sous\s+option|sous\s+compromis)\b",
    re.IGNORECASE)
# The listing's own title is about this house, so there the words count in
# full: crevits.be keeps a sold house online with "RECENT VERKOCHT. Exclusieve
# driegevelvilla ..." as its title, and a project with no unit left as
# "VOLLEDIG UITVERKOCHT. Botanique." kensington.be shouts a bare "OPTIE" in
# the title ("Woning met tuin OPTIE - geen bezoek mogelijk"); in lower case
# the word is a feature ("optie op garage"), so only the shouted form counts.
# opt-immo.be and flame-estate.be title a house under option "Woning met
# optie - Stationsstraat 9, 8740 Pittem" (the page's label says "Optie" and
# its price "Met optie"); "met optie op/tot ..." is a purchase option on
# something else and stays out.
TITLE_SOLD = re.compile(
    r"\b(?:uit)?(?:verkocht|verhuurd)\b(?!\s+onder\s+voorbehoud)|\b(?:sold|rented|vendu|lou[ée])\b",
    re.IGNORECASE)
TITLE_OPTION = re.compile(r"\bOPTIE\b|(?i:\bmet\s+optie\b(?!\s+(?:op|tot|om)\b))")

# URL path fragments that mark a page as a listing detail page.
LISTING_URL_HINTS = (
    "/te-koop/", "/tekoop/", "/pand/", "/panden/", "/woning/", "/woningen/",
    "/aanbod/", "/eigendom/", "/eigendommen/", "/property/", "/properties/",
    "/estate/", "/vastgoed/", "/immo/", "/for-sale/", "/detail/", "/objecten/",
    "/realisatie/", "/aanbod-", "/te-koop-",
    # axellenaerts.be files sales under /kopen/, oximo.be under /immo-te-koop/,
    # cannoodt.be and vastgoeddevos.be under /<type>/<place>/<slug>.
    "/kopen/", "-te-koop/", "/huis/", "/appartement/",
)

# ...and fragments that mean it definitely is not one.
LISTING_URL_ANTI_HINTS = (
    "te-huur", "/tehuur/", "for-rent", "/verhuur", "/nieuws", "/blog",
    "/contact", "/over-ons", "/team", "/diensten", "/schatting", "/vacature",
    "/privacy", "/cookie", "/algemene-voorwaarden", "/verkocht", "/references",
    "/nieuwbouw", "/projecten", "/gerealiseerd", "/page/", "/tag/", "/author/",
    # Documents and photos that sitemaps list under a property folder.
    "/assets/", "/uploads/", "/imager/", "/wp-content/", "/files/",
    # Other-language mirrors of the same pages, and property abroad.
    "/fr/", "/en/", "/de/", "/buitenland",
    # crevits.be keeps sold houses online as /aanbod/<id>/villa-verkochte-panden-in-9840-de-pinte
    "verkocht",
)

# Category and search-landing pages whose URL otherwise reads like a detail
# page. dewaele.com files its whole catalogue under /te-koop/alle/<type>/..., its
# SEO landing pages ("Huis te koop Ruiselede 4 slaapkamers") as /lk-<slug>, and
# a city filter as /te-koop/9000-gent/huis - none of which is one property. A
# real detail slug names the house; it is never a bare type word.
CATEGORY_PATH = re.compile(
    r"/alle(?:/|$)"
    r"|/lk-[^/]*/?$"
    r"|/(?:huis|huizen|woning|woningen|appartement|appartementen|villa|villas|"
    r"grond|bouwgrond|gronden|garage|garages|kantoor|kantoren|handelspand|"
    r"opbrengsteigendom|bedrijfsvastgoed|nieuwbouw|projecten|luxevastgoed|"
    # infinimo.be: /aanbod/te-koop/aalter-9880/studio and .../garage/parking
    r"studio|studios|parking|parkings|loft|lofts|penthouse|penthouses)/?$"
    # infinimo.be keeps its whole catalogue at /aanbod/te-koop: a path that
    # stops at the sale word is the index, never one property.
    r"|/(?:te-koop|te-huur|kopen|huren|aanbod)/?$",
    re.IGNORECASE)
# "/te-koop/9000-gent": a town filter, when the slug is that postcode's town.
# cdf.be writes "/te-koop/2335-stijlvol-appartement-in-..." with a reference
# number in the same position, which the table tells apart from a postcode.
POSTCODE_TOWN_TAIL = re.compile(r"/([1-9]\d{3})-([a-z-]+)/?$")
# The same filter with the postcode last: infinimo.be/aanbod/te-koop/gent-9000,
# or several merged towns as /evergem--sleidinge-9940.
TOWN_POSTCODE_TAIL = re.compile(r"/([a-z-]+)-([1-9]\d{3})/?$")
# immobeguin.be narrows a town filter by a category of its own naming:
# /te-koop/8530-harelbeke/investering, .../luxe, .../commercieel,
# .../kmo-industrieel, .../parkings-garages. Such a tail is a word or two of
# letters; a detail slug names the house and ends in its number.
CATEGORY_TAIL = re.compile(r"/[a-z]+(?:-[a-z]+)?/?$")
# The catalogue words a town filter hangs directly under. With one of these
# right in front, "/te-koop/9600-ronse" is a town page even when the bundled
# table knows 9600 only as Renaix (or 7711 only as Dottignies).
SALE_SEGMENT = re.compile(r"/(?:te-koop|te-huur|kopen|huren|aanbod|nl|fr|en)$")

# A heading that counts properties ("47 Panden te koop in Gent") belongs to a
# catalogue page, whatever its URL looks like.
COUNT_HEADING = re.compile(
    r"^\s*\d+\s+(?:panden|woningen|eigendommen|resultaten|properties|results)\b",
    re.IGNORECASE)

PRICE_FLOOR = 25_000
PRICE_CEILING = 5_000_000

# A Belgian-formatted amount: 395.000 / 395 000 / 395000. Crucially it must not
# match across a separator into the next number on the page: a separator is
# only allowed when followed by exactly three digits - and not by a fourth,
# or "EUR 295.000 9000 Gent" would read as 295.000900 - and the dotted form
# is tried first on its own, or "€ 215.000 137 m² 110 m²" (entree.be, the
# facts right after the price) would read as 215.000.137.110 and overflow.
AMOUNT = r"(\d{1,3}(?:\.\d{3})+(?!\d)|\d{1,3}(?:[.   ]\d{3})+(?!\d)|\d{4,8})"

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
    "foto", "opp", "kijkdag", "kijkdagen", "beschikbaar", "beschikbaarheid",
}

# "Woning te koop in Aalter", "Appartement te huur te Gent"
CITY_IN_TITLE = (
    r"\bte\s+(?:koop|huur)\s+(?:in|te)\s+"
    r"([A-Z][a-zà-ÿ]{2,}(?:[-\s][A-Z][a-zà-ÿ]+)*)"
)
# "Kortrijk - Charmante te renoveren burgerwoning"
CITY_LEADS_TITLE = r"^([A-Z][a-zà-ÿ]{2,}(?:[-\s][A-Z][a-zà-ÿ]+)*)\s+[-–|]\s+"
# "Appartement te koop te WEVELGEM (8560)" - the Omnicasa title shape.
CITY_PAREN_POSTCODE = r"\b([A-Z][A-Za-zà-ÿ'-]{2,}(?:[-\s][A-Z][A-Za-zà-ÿ'-]+)*)\s*\(([1-9]\d{3})\)"
# "Bouwjaar 1930", "renovatie 2012": a year, never a postcode, whatever follows it.
YEAR_LEAD = re.compile(r"(?:bouw|renovatie|renovatiejaar|ver)?jaar|gebouwd|anno|sinds|renovatie", re.IGNORECASE)
# ".../te-koop/9140-Temse/..." and ".../woning-te-koop-astene"
CITY_IN_URL_WITH_POSTCODE = r"(?:/|-in-|-te-)([1-9]\d{3})-([A-Za-z][A-Za-z-]{2,30}?)(?:-\d+)?(?:/|$)"
CITY_IN_URL_SLUG = r"/[\w-]*te-koop-([a-z][a-z-]{2,30})(?:/|$)"
CITY_IN_URL_IN = r"/[\w-]*?-in-([a-z][a-z-]{2,30})(?:/|$)"

# A Flemish street is one to four words, the first capitalised, the last ending
# in a street suffix, followed by a house number: "Kerkstraat 12",
# "Grote Markt 5", "Steenweg op Deinze 140", "Sint-Pietersnieuwstraat 25A".
STREET_SUFFIX = (
    "straat|steenweg|laan|weg|plein|dreef|dreve|baan|lei|kaai|markt|dries|hof|"
    "wegel|plaats|vest|kouter|pad|park|kade|dijk|ring|singel|veld|akker|berg|"
    "bos|heide|dam|gracht|poort|beek|molen|hoek|wal|zoom|erf|gaarde|dorp|wijk|"
    "rede|reep|straatje|wegje|baantje|meers|brug|tragel|steeg|vaart"
)
_STREET_WORD = r"[A-Z][^\W\d_]*(?:['’-][^\W\d_]+)*"   # letters only: not "E40", not "Kortrijk."
_STREET_CONNECTOR = r"(?:op|ter|ten|de|den|der|van|aan|'t|het|en)"
# Words are joined by a single space, never a newline: `find_street` collapses
# all other whitespace but keeps the line breaks `block_text` puts between
# blocks, so the last word of a heading cannot be glued onto the street that
# starts the next paragraph ("...nabij het Parkbos</h1><p>Overmeersstraat 9").
STREET = re.compile(
    rf"(?<![\w-])({_STREET_WORD}(?: (?:{_STREET_WORD}|{_STREET_CONNECTOR})){{0,3}})"
    rf" (\d{{1,4}}(?: ?[A-Za-z](?![\w-])| ?bus ?\d+[A-Za-z]?)?)(?![\d.,]?\d)"
)
_STREET_ENDS = re.compile(rf"(?:{STREET_SUFFIX})$", re.IGNORECASE)

# Capitalised words that precede a street on agency pages but are not part of
# its name: labels, property types, and the municipality.
STREET_LABELS = {
    "adres", "ligging", "locatie", "straat", "te", "koop", "woning", "huis",
    "appartement", "villa", "gelegen", "ref", "vraagprijs", "aan", "in", "op",
    "rijwoning", "nieuwbouw", "bouwgrond", "hoeve", "herenhuis", "bungalow",
    "opbrengsteigendom", "appartementsgebouw", "kantoorruimte", "praktijkruimte",
    "handelspand", "handelsruimte", "loft", "studio", "penthouse", "duplex", "grond",
    "project", "nieuwbouwproject", "landhuis", "woonhuis", "hoekwoning", "garage",
    # yvimat.be "EPC Jan Breydelstraat 115"; axellenaerts.be "Street View Heirbaan 270"
    "epc", "street", "view", "streetview",
    # aclea-vastgoed.be prints a project address as "Bus 201": a box, not a street.
    "bus",
    # Form labels that precede an office address in running text (Immo Casa).
    "ja", "nee", "neen", "kantoor", "bureau", "agentschap", "contact",
    # A menu entry or tab title printed right before the address block
    # (huysewinkel.be "Home", rosseel.be "Details").
    "home", "details",
}

# Elements a browser renders on a line of their own. Text on either side of one
# is never a single phrase, however the markup is indented.
BLOCK_TAGS = frozenset(
    "address article aside blockquote body br button dd details div dl dt fieldset "
    "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr label legend li main "
    "nav ol p pre section summary table tbody td tfoot th thead title tr ul".split()
)

# Page regions whose addresses belong to the agency, not the property. Matched
# per class token, and "header" only in its site-wide spellings: a listing's
# own hero is routinely a <header class="detail-header">.
STRONG_CHROME_CLASS = re.compile(
    r"^(?:(?:site|page|main|global|top|sticky)[-_]?)?(?:footer|header|nav|navbar|topbar|menu)$"
    r"|footer",
    re.IGNORECASE,
)
CHROME_CLASS = re.compile(
    STRONG_CHROME_CLASS.pattern
    + r"|contact|kantoor|office|agency"
    # Testimonials name the house a *previous* client sold (cannoodt.be:
    # "Wij verkochten onze woning met Cannoodt - Wintertuinstraat 2, 9000 Gent").
    r"|testimonial|getuigenis|referentie|review|verkochten",
    re.IGNORECASE,
)
BREADCRUMB = re.compile(r"breadcrumb|kruimel", re.IGNORECASE)


def _is_breadcrumb(node: Any) -> bool:
    tokens = list(node.get("class", [])) + [str(node.get("id") or ""), str(node.get("aria-label") or "")]
    return any(BREADCRUMB.search(t) for t in tokens)

# Property-type words that URL slugs put in front of the municipality:
# /te-koop-woning-wippelgem/ is Wippelgem, not "Woning-Wippelgem".
SLUG_TYPE_WORDS = {
    "woning", "woningen", "huis", "huizen", "appartement", "appartementen", "villa",
    "landhuis", "herenhuis", "rijwoning", "bouwgrond", "grond", "hoeve", "bungalow",
    "studio", "penthouse", "duplex", "handelspand", "kantoor", "loft", "nieuwbouw",
    "project", "garage", "assistentiewoning", "fermette", "bel-etage", "chalet",
}


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
    if CATEGORY_PATH.search(path) or is_town_filter_path(path):
        return False
    for hint in LISTING_URL_HINTS:
        index = path.find(hint)
        if index == -1:
            continue
        if path[index + len(hint):].strip("/"):
            return True
    return False


def is_town_filter_path(path: str) -> bool:
    """Does the path end in "<postcode>-<town>" where the town really belongs
    to that postcode? Then it filters a catalogue by town, it is not a house.

    A category word hung under such a town ("/8530-harelbeke/investering")
    is the same filter narrowed once more, so it is stripped first.
    """
    # infinimo.be spells "nazareth-de%20pinte-9810" and "maarkedal-9680%20".
    path = _fold(re.sub(r"\s+", "-", unquote(path).strip().lower())).rstrip("/")
    if _town_filter(path):
        return True
    tail = CATEGORY_TAIL.search(path)
    if tail and tail.start() > 0:
        return _town_filter(path[:tail.start()])
    return False


def _town_filter(path: str) -> bool:
    m = POSTCODE_TOWN_TAIL.search(path)
    if m:
        postcode, towns = m.group(1), [m.group(2)]
    else:
        m = TOWN_POSTCODE_TAIL.search(path)
        if not m:
            return False
        postcode, towns = m.group(2), m.group(1).split("--")
    from .geo import postcode_table, postcodes_for_place
    entry = postcode_table().get(postcode)
    if not entry:
        return False
    names = {_fold(str(name).lower()).replace("-", " ") for name in entry.get("names", [])}
    if any(town.replace("-", " ") in names for town in towns):
        return True
    # "merelbeke-melle-9090": a merged municipality the table knows only by
    # its parts; postcodes_for_place unites them.
    if any(postcode in postcodes_for_place(town) for town in towns):
        return True
    # The table names some towns in one language only (9600 Renaix, 7711
    # Dottignies): a short name straight under the catalogue word is still a
    # town page. cdf.be's "/te-koop/2335-stijlvol-appartement-in-..." keeps
    # its reference number apart from a postcode by naming the property.
    if m.re is POSTCODE_TOWN_TAIL and SALE_SEGMENT.search(path[:m.start()]):
        town = towns[0]
        return town.count("-") <= 2 and guess_property_type(url=town) == "unknown"
    return False


def _fold(text: str) -> str:
    """Strip accents so "péruwelz" and "peruwelz" compare equal."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


def guess_property_type(title: str = "", url: str = "", description: str = "") -> str:
    head = f"{title} {_slug_words(url)}".lower()
    if is_parking(head):
        return "parking"
    blob = f"{head} {description or ''}".lower()
    for ptype, keywords in TYPE_KEYWORDS:
        if any(kw in blob for kw in keywords):
            return ptype
    return "unknown"


def _slug_words(url: str) -> str:
    return urlparse(url).path.replace("-", " ").replace("_", " ").replace("/", " ") if url else ""


def is_parking(text: str) -> bool:
    """Does the title (or URL slug) advertise a garage or parking space itself?

    A parking word counts only when nothing else is for sale: neither another
    property type nor an "x met garage" phrasing in front of it.
    """
    if any(kw in text for _, keywords in TYPE_KEYWORDS for kw in keywords):
        return False
    return any(not PARKING_AS_EXTRA.search(text[max(0, m.start() - 40):m.start()])
               for m in PARKING_WORD.finditer(text))


# ----------------------------------------------------------- Next.js data

# wolff.be: a Next.js front end over the Skarabee API. The page prints the
# surfaces as an icon and a bare number ("1027 m²") with no label the text
# patterns could read, never says "in optie" on the detail page (only the
# overview card does), and its "Meer panden" strip is in the same markup as
# the property. Everything sits in the ``__NEXT_DATA__`` blob instead, under
# props.pageProps.property; the strip is a sibling list and is ignored.
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

NEXT_STATUS = {
    "available": "available",
    "option": "option",
    "reserved": "option",
    "sold": "sold",
    "rented": "sold",
}
NEXT_TYPES = {
    "house": "house",
    "apartment": "apartment",
    "land": "land",
    "parking": "parking",
    "garage": "parking",
}


def next_data_property(html: str) -> dict[str, Any] | None:
    """The page's own property record from a Next.js data blob, when the
    page has one in the Skarabee shape (a ``sizes`` or ``location`` dict)."""
    m = NEXT_DATA.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return None
    prop = (data.get("props") or {}).get("pageProps", {}).get("property") if isinstance(data, dict) else None
    if not isinstance(prop, dict):
        return None
    if not any(isinstance(prop.get(k), dict) for k in ("sizes", "location", "price")):
        return None
    return prop


def _from_next_property(prop: dict[str, Any], listing: Listing) -> None:
    """Fill the listing from a Skarabee-shaped Next.js property record."""
    price = prop.get("price")
    if isinstance(price, dict) and not price.get("hidden") and listing.price is None:
        amount = _int(str(price.get("amount") or ""))
        if amount and PRICE_FLOOR <= amount <= PRICE_CEILING:
            listing.price = amount

    sizes = prop.get("sizes") if isinstance(prop.get("sizes"), dict) else {}
    for key, attr, low, high in (
        ("liveable_area", "living_area_m2", 15, 2000),
        ("plot_area", "plot_area_m2", 20, 200_000),
    ):
        entry = sizes.get(key)
        size = entry.get("size") if isinstance(entry, dict) else entry
        if size is not None and getattr(listing, attr) is None:
            setattr(listing, attr, _plausible_area(_int(str(size)), low, high))

    bedrooms = _int(str(prop.get("bedrooms") or ""))
    if bedrooms is not None and 0 < bedrooms <= 20 and listing.bedrooms is None:
        listing.bedrooms = bedrooms

    epc = _int(str(prop.get("epc_value") or ""))
    if epc is not None and 0 < epc < 2000 and listing.epc_kwh is None:
        listing.epc_kwh = epc

    # The record's own title. og:title says "... | Wolff Vastgoed", which the
    # generic-title rule took for the site's and replaced with the first
    # heading naming a property: a card from the "Meer panden" strip.
    own_title = str(prop.get("description_title") or "").strip()
    if own_title:
        listing.title = own_title

    kind = str(prop.get("type") or "").lower()
    if kind in NEXT_TYPES:
        listing.property_type = NEXT_TYPES[kind]
    elif kind.startswith(("professional", "commercial", "office", "industrial")):
        listing.property_type = "commercial"

    loc = prop.get("location")
    if isinstance(loc, dict) and not loc.get("hidden"):
        postcode = str(loc.get("postal_code") or "").strip()
        city = str(loc.get("city") or "").strip()
        if postcode.isdigit() and len(postcode) == 4 and not listing.postal_code:
            listing.postal_code = postcode
        if city and not listing.city:
            listing.city = city
        street = str(loc.get("street") or "").strip()
        number = str(loc.get("number") or "").strip()
        if street and number and not listing.street:
            listing.street = f"{street} {number}"
        geo = loc.get("geo") if isinstance(loc.get("geo"), dict) else {}
        try:
            lat, lon = float(geo.get("latitude")), float(geo.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
        if lat is not None and listing.lat is None and plausible_position(lat, lon, listing.postal_code):
            listing.lat, listing.lon, listing.geo = lat, lon, "address"


# ------------------------------------------------------------------- JSON-LD

def jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            out.append(node)
            for key, v in node.items():
                # Members of an ItemList are other properties (an index
                # page, a "similar" strip); their prices are not this page's.
                if key == "itemListElement":
                    continue
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


def _from_jsonld(objects: list[dict[str, Any]], listing: Listing) -> set[str]:
    """Fill the listing from structured data. Returns the agency's own street
    addresses, found in Organization blocks, so the text pass can ignore them."""
    office: set[str] = set()
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

        is_office = any(t in types for t in (
            "organization", "localbusiness", "realestateagent",
        ))

        addr = obj.get("address")
        if not isinstance(addr, dict) and is_property and isinstance(obj.get("geo"), dict):
            addr = obj["geo"].get("address")      # mvastgoed.be nests it under geo
        if isinstance(addr, dict) and is_property:
            locality = str(addr.get("addressLocality") or "").strip()
            if re.search(r"\d", locality):
                # Skarabee sites put the street in addressLocality and the
                # municipality in addressRegion.
                locality = str(addr.get("addressRegion") or "").strip()
            if not listing.city and locality:
                listing.city = locality
            if not listing.postal_code and addr.get("postalCode"):
                listing.postal_code = str(addr["postalCode"]).strip()
            if not listing.street and addr.get("streetAddress"):
                listing.street = _clean_ws(str(addr["streetAddress"]))
        elif isinstance(addr, dict) and is_office and addr.get("streetAddress"):
            # Only an explicit organisation block is the office. Other objects
            # carrying an address (wijsvastgoed.be hangs one off GeoCoordinates)
            # describe the property.
            office.add(_clean_ws(str(addr["streetAddress"])).lower())

        # Coordinates straight from the page beat any lookup. Only trusted off
        # the property itself, or off a GeoCoordinates block whose address
        # agrees with the property's postcode - an office has one too.
        geo = obj.get("geo") if is_property else None
        if not isinstance(geo, dict) and "geocoordinates" in types and not is_office:
            geo_addr = obj.get("address") if isinstance(obj.get("address"), dict) else {}
            geo_postcode = str(geo_addr.get("postalCode") or "").strip()
            if geo_postcode and (not listing.postal_code or geo_postcode == listing.postal_code):
                geo = obj
        if isinstance(geo, dict) and listing.lat is None:
            lat, lon = _coord(geo.get("latitude")), _coord(geo.get("longitude"))
            if lat is not None and lon is not None and plausible_position(lat, lon, listing.postal_code):
                listing.lat, listing.lon, listing.geo = lat, lon, "address"

        img = obj.get("image")
        if img and not listing.image_url:
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str):
                listing.image_url = img
    return office


# irres.be names the page in its BreadcrumbList as the CMS sees it:
# "FOR_SALE_ORDER_ENDED - 9051 Sint-Denijs-Westrem - Pieter Pauwel Rubenslaan 1".
# The page itself never says the mandate ended, prints no "verkocht", and
# keeps its price and facts; the sitemap lists 774 such pages next to the
# 45 for sale. The token is the only place the truth is written.
CRUMB_NAME = re.compile(     # the parts are set off by " - "; towns hyphenate without spaces
    r"^([A-Z][A-Z_]+[A-Z])\s+-\s+([1-9]\d{3})\s+([^\W\d_]\S*(?:\s\S+)*?)\s+-\s+(\S.*)$")
CRUMB_STATUS = (
    (re.compile(r"ENDED|SOLD|RENTED|VERKOCHT|VERHUURD"), "sold"),
    (re.compile(r"OPTION|RESERVED|COMPROMIS"), "option"),
    (re.compile(r"^FOR_(?:SALE|RENT)$"), "available"),
)


def breadcrumb_facts(objects: list[dict[str, Any]]) -> dict[str, str]:
    """What the last breadcrumb item says about the page: a status word, the
    postcode, town and street, when it has the shape above. Empty otherwise."""
    for obj in objects:
        types = obj.get("@type") or ""
        types = " ".join(types) if isinstance(types, list) else str(types)
        if "breadcrumblist" not in types.lower():
            continue
        items = obj.get("itemListElement")
        if not isinstance(items, list) or not items:
            continue
        last = items[-1]
        name = str(last.get("name") or "") if isinstance(last, dict) else ""
        m = CRUMB_NAME.match(name.strip())
        if not m:
            continue
        token, postcode, city, street = m.groups()
        status = next((s for pat, s in CRUMB_STATUS if pat.search(token)), "")
        return {"status": status, "postal_code": postcode,
                "city": _clean_ws(city), "street": _clean_ws(street)}
    return {}


def breadcrumb_status(html: str) -> str:
    """The status the structured breadcrumb states, or "" when it says nothing."""
    if "BreadcrumbList" not in html:
        return ""
    return breadcrumb_facts(jsonld_objects(BeautifulSoup(html, "lxml"))).get("status", "")


# -------------------------------------------------------------------- street

def _clean_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,")


_places: frozenset[str] | None = None


def _known_places() -> frozenset[str]:
    """Every municipality and locality name in the bundled postcode table."""
    global _places
    if _places is None:
        from .geo import postcode_table  # local import: geo depends on models, not on us
        _places = frozenset(
            str(n).lower() for entry in postcode_table().values() for n in entry.get("names", [])
        )
    return _places


def is_known_place(name: str) -> bool:
    return name.strip().lower().replace("-", " ") in {p.replace("-", " ") for p in _known_places()}


def is_known_postcode(code: str) -> bool:
    from .geo import postcode_table
    return str(code).strip() in postcode_table()


# Places and countries that mark a listing as abroad when they appear in the
# title, the town or the URL. Belgian agencies sell second homes on the Spanish
# and French coasts from the same site, and those must not reach the map or
# the mail. Only unmistakable names: no "Costa" alone, no street words.
FOREIGN_HINT = re.compile(
    r"\b(?:spanje|spain|espa[nñ]a|(?:noord-?)?frankrijk|france|portugal|itali[eë]|italy|nederland|"
    r"the netherlands|turkije|turkey|dubai|griekenland|greece|"
    r"costa\s+(?:blanca|del\s+sol|c[aá]lida|brava|dorada|tropical|azul|del\s+azahar)|"
    r"c[oô]te\s+d'?azur|provence|dordogne|ard[eè]che|"
    r"alicante|marbella|torrevieja|orihuela|moraira|calpe|j[aá]vea|xabia|benidorm|"
    r"d[eé]nia|altea|benitachell|guardamar|murcia|m[aá]laga|tenerife|mallorca|ibiza|"
    r"lanzarote|fuerteventura|estepona|mijas|nerja|almer[ií]a|valencia|"
    r"sainte-maxime|grimaud|saint-tropez|cannes|antibes|fr[eé]jus|ramatuelle|cavalaire|"
    r"cogolin|le lavandou|bormes|hy[eè]res|roquebrune|saint-rapha[eë]l|les issambres|"
    r"la croix-valmer|gassin|gigaro|plan-de-la-tour|champagny|roquebrun|nontron|"
    r"cond[eé]-sur-l'?escaut|savoie|haute-savoie|alpes|pyr[eé]n[eé]es|languedoc|bretagne|"
    r"normandi[eë]|charente|gironde|cumbre del sol|finestrat|villajoyosa|rojales|"
    r"pilar de la horadada|los alc[aá]zares|san javier|la nuc[ií]a|torre-?pacheco|"
    r"ciudad quesada|benijofar|lo pag[aá]n|el campello|moj[aá]car|"
    r"arnemuiden|cadzand|breskens)\b(?!\s*\d)",     # "Sluis 22, 9290 Berlare" is a street
    re.IGNORECASE,
)


# A five-digit postcode in the URL slug or at the head of the title: Belgian
# postcodes have four. immodebo.be "appartement-te-koop-in-03380-bigastro",
# immobeguin.be "/te-koop/24300-nontron/", aclea-vastgoed.be
# "/properties/03730-javea/" and "04638 - Mojacar". A bare "/28607-villa" is
# not enough: axellenaerts.be numbers its pages that way.
FOREIGN_POSTCODE_IN_URL = re.compile(r"(?:\bin|te-koop|properties)[-/](\d{5})-[a-z]|/(0\d{4})-[a-z]", re.I)
FOREIGN_POSTCODE_IN_TITLE = re.compile(r"^\s*(\d{5})\s*[-–]\s*\S")


def in_belgium(listing: Listing) -> bool | None:
    """True when the postcode or the town is Belgian, False on evidence of
    another country, None when the page gives nothing to go on.

    A postcode the Belgian table knows settles it unless the title names a
    foreign place outright: immo-zone.be stamps its Aalst office postcode on
    its Dubai and Alicante projects. A postcode the table does not know -
    "03750", "34449" - is abroad, and so is a five-digit one in the URL or
    the title of a page that prints no address at all.
    """
    text = " ".join((listing.title or "", listing.city or "", listing.url or ""))
    if FOREIGN_POSTCODE_IN_URL.search(listing.url or "") or FOREIGN_POSTCODE_IN_TITLE.match(listing.title or ""):
        return False
    if FOREIGN_HINT.search(text):
        # "Residentie Costa Brava" in Blankenberge: a Belgian postcode whose
        # town the title or URL also names outranks a foreign-sounding word.
        # The Aalst office postcode on a Rojales villa has no such backing.
        if listing.postal_code and is_known_postcode(listing.postal_code) and listing.city:
            named = re.sub(r"[\s-]+", " ", f"{listing.title or ''} {listing.url or ''}").lower()
            if re.sub(r"[\s-]+", " ", listing.city).lower() in named:
                return True
        return False
    if listing.postal_code:
        return is_known_postcode(listing.postal_code)
    if listing.city:
        return True if is_known_place(listing.city) else None
    return None


PAGE_ROOTS = frozenset(("html", "body", "main"))


def chrome_nodes(soup: BeautifulSoup, strong_only: bool = False) -> list[Any]:
    """Elements that are site furniture rather than listing content.

    Strong chrome is structural: footer, navigation, the site-wide header.
    Weak chrome is a region named after the agency's own things - contact
    block, office, testimonials - which is where an office address is printed,
    but also where a contact form quotes the house ("Ik had graag meer info
    over De Pintelaan 209"). A street found in weak chrome is kept out of the
    body-text search but cannot veto a source that names the house outright.

    Not chrome at all: a breadcrumb (its last crumb is this page's own title,
    address included) and the <address> element, which these sites use for
    the property rather than the agency.
    """
    nodes = soup.find_all("footer")
    nodes += [n for n in soup.find_all("nav") if not _is_breadcrumb(n)]
    # A <header> is site chrome only at the top level; inside an article,
    # section or main it is the listing's own heading.
    nodes += [h for h in soup.find_all("header")
              if not h.find_parent(["article", "main", "section"])]
    pattern = STRONG_CHROME_CLASS if strong_only else CHROME_CLASS
    # A class on <body> or <main> describes the theme, not a region: the
    # WordPress body class "ehf-footer" (aclea-vastgoed.be) must not turn
    # the whole page into chrome.
    # A block named "page-header" that holds the page's own <h1> is the
    # listing's heading, not the site's: makelaarshuys.be, opt-immo.be and
    # mvastgoed.be put the house address right under the title there, and
    # recording it as the office address wiped the street.
    nodes += [n for n in soup.find_all(id=True)
              if n.name not in PAGE_ROOTS and pattern.search(str(n.get("id")))
              and n.find("h1") is None]
    nodes += [n for n in soup.find_all(class_=True)
              if n.name not in PAGE_ROOTS and any(pattern.search(c) for c in n.get("class", []))
              and n.find("h1") is None]
    nodes = [n for n in nodes if not _is_breadcrumb(n)]
    # A topbar inside a header would otherwise be counted twice.
    unique = {id(n): n for n in nodes}
    return [n for n in unique.values()
            if not any(id(p) in unique for p in n.parents)]


def chrome_places(soup: BeautifulSoup) -> dict[tuple[str, str], int]:
    """How often each (postcode, city) pair is printed in the page chrome.

    Counts, not a set: a house in Ghent sold by a Ghent agency has "9000 Gent"
    in the footer *and* in its own address line, and only the surplus
    occurrence in the body text is the property's.
    """
    found: dict[tuple[str, str], int] = {}
    for node in chrome_nodes(soup):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        for m in re.finditer(POSTAL_CITY, text):
            key = (m.group(1), f"{m.group(2) or ''}{m.group(3)}".strip().lower())
            found[key] = found.get(key, 0) + 1
    return found


def _normal_number(number: str) -> str:
    return number if "bus" in number.lower() else re.sub(r"\s+", "", number)


def _clean_street_name(name: str, city: str, require_suffix: bool = True) -> str | None:
    """Trim label words off the front and insist on a street suffix."""
    words = name.split()
    while len(words) > 1 and (
        words[0].lower().strip(":") in STREET_LABELS
        or (city and words[0].lower() == city.lower())
        or (words[0].lower() in {n.lower() for n in _known_places()})
        # "INSTAPKLARE WOONST Vlamingenstraat 2": shouting is marketing, not a name.
        or (words[0].isupper() and len(words[0]) >= 4 and not _STREET_ENDS.search(words[0]))
    ):
        words.pop(0)
    # "Kerkstraat", "Grote Markt" - or "Steenweg op Deinze", "Hof ter Linden",
    # where the suffix leads and a connector follows it.
    suffix_last = bool(_STREET_ENDS.search(words[-1]))
    suffix_first = (len(words) >= 3 and bool(_STREET_ENDS.search(words[0]))
                    and words[1].lower() in ("op", "ter", "ten", "van", "aan", "de", "der", "den"))
    if require_suffix and not (suffix_last or suffix_first):
        return None
    if words[0].lower().strip(":") in STREET_LABELS:
        return None                        # "Ref 1234, 9000 Gent": a label, not a name
    return " ".join(words)


def block_text(root: Tag) -> str:
    """The page text with a line break wherever the browser would put one.

    `get_text(" ")` flattens the whole page into one line, so a street regex
    that allows a few capitalised words reads "Home" from the menu button or
    "Parkbos" from the end of the title as part of the street name that opens
    the next block. Here every block element starts and ends a line and the
    whitespace inside a line is collapsed, so markup indentation never matters
    but a block boundary always does.
    """
    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if type(child) is NavigableString:
                parts.append(re.sub(r"\s+", " ", str(child)))
            elif isinstance(child, Tag):
                block = child.name in BLOCK_TAGS
                if block:
                    parts.append("\n")
                walk(child)
                if block:
                    parts.append("\n")

    walk(root)
    return re.sub(r"[^\S\n]*\n[\s]*", "\n", "".join(parts)).strip()


def find_street(
    text: str,
    city: str = "",
    postcode: str = "",
    exclude: set[str] | frozenset[str] = frozenset(),
    near_place: bool = False,
    require_suffix: bool = True,
) -> str | None:
    """First credible street + number in `text`, or None.

    With `near_place`, the street must be followed closely by the listing's
    postcode or city, as in "Kerkstraat 12, 9000 Gent" - the shape of a real
    address line, as opposed to a street name mentioned in running prose.
    `require_suffix=False` also accepts a name without a street word
    ("Berouw 156", "Kouter 8") - only safe on text the site labels as the
    address, and then only with `near_place`.

    Line breaks in `text` are block boundaries (see `block_text`): a street
    name never spans one, though the postcode may follow on the next line.
    """
    text = re.sub(r"[^\S\n]+", " ", text)
    for m in STREET.finditer(text):
        name = _clean_street_name(m.group(1), city, require_suffix or not near_place)
        if not name:
            continue
        number = _normal_number(m.group(2))
        digits = re.match(r"\d+", number).group()  # type: ignore[union-attr]
        tail = text[m.end(): m.end() + 40]
        # "Kerkstraat 9000 Gent" - that is a postcode, not a house number.
        if len(digits) == 4 and re.match(r"\s*[A-Z]", tail):
            continue
        street = f"{name} {number}"
        if street.lower() in exclude:
            continue
        # "Ledeberg 9050": a municipality and its postcode, not a street.
        if is_known_place(name):
            continue
        # ", 9000 Gent" / " - Gent" / " te Gent": the place must come right
        # after - or, as immotion.be writes it, right before:
        # "Kortrijk, Camiel De Stoopstraat 51".
        close = tail[:14]
        before = text[max(0, m.start() - 25):m.start()]
        if near_place and not (
            (postcode and (postcode in close or postcode in before))
            or (city and (city.lower() in close.lower() or city.lower() in before.lower()))
        ):
            continue
        return street
    return None


ADDRESS_CLASS = re.compile(r"adres|address|locat|ligging", re.IGNORECASE)
# Icon classes that mark the line next to them as the address:
# "fa-location-pin", "fa-map-marker-alt", "fa-location-dot", "bi-geo-alt".
PIN_ICON = re.compile(r"location|map-marker|marker|(?:^|[-_])pin(?:$|[-_])|geo-alt", re.IGNORECASE)
# "Ligging Sint-Amandsstraat 1 8800 Roeselare", "Adres: Kerkstraat 12"
ADDRESS_LABEL_LINE = re.compile(r"^(?:adres|address|ligging|locatie)\s*:?\s+(\S.{4,90})$",
                                re.IGNORECASE | re.MULTILINE)


def labelled_address(soup: BeautifulSoup) -> str:
    """The text of the element a site marks as *the* address, case-normalised.

    cannoodt.be prints "<span class='pand-adres'>wittemolenstraat 158 - 9040
    SINT-AMANDSBERG</span>", which the capitalised street and place patterns
    cannot read. An element that is labelled as an address - by class, by the
    <address> tag, or by a leading "Adres:"/"Ligging" label in its text - is a
    stronger source than running text, so its casing is normalised here
    ("Wittemolenstraat 158 - 9040 Sint-Amandsberg") and the usual patterns run
    on the result. Strong chrome is skipped; the office check happens at the
    call site.
    """
    chrome = {id(n) for n in chrome_nodes(soup, strong_only=True)}
    for node in soup.find_all(True):
        # Elementor names its template regions "elementor-location-popup",
        # "-header", "-single": a region, not an address.
        tokens = [t for t in list(node.get("class", [])) + [node.get("id") or ""]
                  if not t.lower().startswith("elementor-location")]
        if node.name != "address" and not any(ADDRESS_CLASS.search(t) for t in tokens):
            continue
        if any(id(p) in chrome for p in node.parents) or id(node) in chrome:
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if not text or len(text) > 100 or not re.search(r"\d", text):
            continue
        return _address_case(text)
    # From here all chrome is skipped: "Adres Hundelgemsesteenweg 155" in a
    # footer is exactly the office line a label or a pin icon introduces.
    chrome = {id(n) for n in chrome_nodes(soup)}
    # A map-pin icon and nothing else marks the line: aclea-vastgoed.be
    # "<i class='fa-solid fa-location-pin'></i> Hogevoorde 28<br>Lievegem".
    # The line must carry a number, so an icon on a "find us" button or a
    # town name alone is not taken.
    for icon in soup.find_all(["i", "svg", "span"]):
        if not any(PIN_ICON.search(t) for t in icon.get("class", [])):
            continue
        parent = icon.parent
        if parent is None or id(parent) in chrome or any(id(p) in chrome for p in parent.parents):
            continue
        lines = [ln.strip() for ln in block_text(parent).split("\n") if ln.strip()]
        text = ", ".join(lines[:2])
        if not text or len(text) > 100 or not re.search(r"\d", text):
            continue
        return _address_case(text)
    # "Ligging Sint-Amandsstraat 1 8800 Roeselare" as a short block of its own.
    for node in soup.find_all(["p", "div", "li", "span", "dd", "td", "tr", "h2", "h3", "h4"]):
        if any(id(p) in chrome for p in node.parents) or id(node) in chrome:
            continue
        text = block_text(node).split("\n", 1)[0]
        if len(text) > 100:
            continue
        m = ADDRESS_LABEL_LINE.match(text)
        if m and re.search(r"\d", m.group(1)):
            return _address_case(m.group(1).strip())
    return ""


def labelled_place(soup: BeautifulSoup) -> str:
    """A municipality printed alone in an element labelled as the location
    (oranjeberg.be: <div class="location">Gent</div>), when the page gives
    the town nowhere else. Only a name the postcode table knows counts."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for node in soup.find_all(True):
        tokens = list(node.get("class", [])) + [node.get("id") or ""]
        if not any(ADDRESS_CLASS.search(t) for t in tokens):
            continue
        if any(id(p) in chrome for p in node.parents) or id(node) in chrome:
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        text = re.sub(r"^(?:ligging|locatie|location|gemeente)\s*:?\s*", "", text, flags=re.IGNORECASE)
        if 2 < len(text) <= 30 and is_known_place(_address_case(text)):
            return _address_case(text)
    return ""


BARE_PLACE_TITLE = re.compile(r"^\s*[1-9]\d{3}\s*[-–:]\s*[^\W\d_][\w\s'’-]{1,40}\s*$")


def _heading(soup: BeautifulSoup) -> str:
    """The first h1, or failing that the first h2 outside the chrome that
    reads as a sentence rather than a label."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for tag in ("h1", "h2"):
        for node in soup.find_all(tag):
            if id(node) in chrome or any(id(p) in chrome for p in node.parents):
                continue
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            if tag == "h2" and len(text.split()) < 3:
                continue
            if text:
                return text
    return ""


def generic_title(title: str, site_name: str = "", url: str = "") -> bool:
    """Is this og:title the site's, not the property's? Only when it names
    the site itself - "Maak uw entree" on entree.be, "CdF - Vastgoedmakelaars"
    on cdf.be - or is the PHP artefact "Array", and says nothing about a
    property. A plain title without a property word ("Prachtig historische
    eigendom") is the agency's choice and stays."""
    text = title.strip()
    if not text or text.lower() == "array":
        return True
    low = text.lower()
    if any(kw in low for _, keywords in TYPE_KEYWORDS for kw in keywords) \
            or PARKING_WORD.search(low) or re.search(r"\d", text):
        return False
    names = {w for w in re.findall(r"[^\W\d_]{3,}", site_name.lower())}
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if host:
        names.add(host.split(".")[0])
    words = set(re.findall(r"[^\W\d_]{3,}", low))
    return bool(names & words)


# Headings that introduce a block rather than name the property, and the
# calls to action agency sites put in headings ("Heb je een vraag? Of wil
# je jouw woning verkopen?", "Beschrijf je droomhuis", "deel deze woning").
SECTION_HEADING = re.compile(
    r"gelijkaardig|vergelijkbaar|soortgelijk|\bandere\b|recent|\bmeer\b|ontdek|aanbod|"
    r"interesse|similar|other|related|bekijk|troeven|omschrijving|beschrijving|"
    r"kenmerken|ligging|indeling|contact|afspraak|zoek|\?|verkopen|verhuren|"
    r"\bvraag|\bdroom|\bdeel\b|\bwil\b|\bwilt\b|\bheb\b|\bjouw\b|\bjou\b|\bje\b|"
    r"\buw\b|\bu\b|waard|schatting|nieuwsbrief|blog|nieuws", re.IGNORECASE)


def address_title(title: str) -> bool:
    """Is the title nothing but an address? Skarabee sites (mvastgoed.be)
    name the listing "Kortrijk Hoveniersstraat 40" in their structured data,
    with a unit number tacked on at times ("Harelbeke Overleiestraat 75/A 12");
    the heading on the page says what is for sale."""
    text = re.sub(r"\s+", " ", title or "").strip()
    if not text or len(text) > 80:
        return False
    low = text.lower()
    if any(kw in low for _, keywords in TYPE_KEYWORDS for kw in keywords) \
            or PARKING_WORD.search(low):
        return False
    m = STREET.search(text)
    if not m or not _STREET_ENDS.search(m.group(1)):
        return False
    # Before the street: at most the town. After it: unit markers and digits.
    before = text[:m.start()].strip()
    after = text[m.end():].strip()
    return len(before.split()) <= 3 and not re.search(r"\d", before) \
        and not re.search(r"[^\W\d_]{3,}", after)


def _property_heading(soup: BeautifulSoup, tags: tuple[str, ...] = ("h1", "h2", "h3")) -> str:
    """The first heading outside the chrome that names the property: at
    least three words, a property (or parking) word, not the address line,
    not a section label. entree.be puts the address in the h1 and the real
    title in the second h2, after an "Interesse in dit pand?" form heading."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for tag in tags:
        for node in soup.find_all(tag):
            if id(node) in chrome or any(id(p) in chrome for p in node.parents):
                continue
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            if len(text.split()) < 3:
                continue
            # An h1 is never a section label; "Garage te koop op zeer
            # interessante ligging te Kortrijk" must not fail on "ligging".
            if tag != "h1" and SECTION_HEADING.search(text):
                continue
            if re.search(POSTAL_CITY, text) or BARE_PLACE_TITLE.match(text):
                continue
            low = text.lower()
            if any(kw in low for _, keywords in TYPE_KEYWORDS for kw in keywords) \
                    or PARKING_WORD.search(low):
                return text
    if tags != ("h1", "h2", "h3"):
        return ""
    # No heading names a type ("wonen in werelderfgoed Begijnhof"): take the
    # one the page's own price sits right under.
    for node in soup.find_all(["h1", "h2", "h3"]):
        if id(node) in chrome or any(id(p) in chrome for p in node.parents):
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text.split()) < 2 or SECTION_HEADING.search(text):
            continue
        if re.search(POSTAL_CITY, text) or BARE_PLACE_TITLE.match(text):
            continue
        after = node.find_next_sibling()
        if after is not None and after.name != "a" \
                and re.match(r"€\s*\d", after.get_text(" ", strip=True)):
            return text
    return ""


def _title_above_price(soup: BeautifulSoup) -> str:
    """The sentence printed right above the page's own price, when no heading
    names the property. irres.be heads the listing with the town alone and
    puts the title in a paragraph: h2 "Sint-Denijs-Westrem", p "Charmante te
    renoveren gezinswoning ...", p "€ 495.000". The address line that other
    sites print there ("Vennelaan 13, 9830 Sint-Martens-Latem") is not a title."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for node in soup.find_all(["p", "div", "span", "h1", "h2", "h3", "h4"]):
        if not re.match(r"€\s*\d", node.get_text(" ", strip=True)):
            continue
        if id(node) in chrome or any(id(p) in chrome for p in node.parents):
            continue
        before = node.find_previous_sibling()
        if before is None or before.name in ("a", "img", "svg", "ul", "ol", "table"):
            continue
        text = re.sub(r"\s+", " ", before.get_text(" ", strip=True))
        # A sentence, not a label or a question: "op uiterst bereikbare
        # ligging" is a title, "Vraagprijs:" and "Interesse?" are not.
        if len(text.split()) < 3 or len(text) > 160 or text.endswith(("?", ":")) \
                or re.search(r"prijs|price|prix", text, re.IGNORECASE):
            continue
        if re.search(POSTAL_CITY, text) or BARE_PLACE_TITLE.match(text) \
                or address_title(text) or re.search(r"[1-9]\d{3}", text):
            continue
        return text
    return ""


def _address_case(text: str) -> str:
    """'wittemolenstraat 158 - 9040 SINT-AMANDSBERG' -> 'Wittemolenstraat 158 - 9040 Sint-Amandsberg'."""
    return re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0).capitalize() if m.group(0).islower() or m.group(0).isupper() else m.group(0),
        text,
    )


MAP_URL = re.compile(r"google\.[a-z.]+/maps|maps\.google\.[a-z.]+/|goo\.gl/maps|maps\.app\.goo\.gl",
                     re.IGNORECASE)


def _coord(raw: Any) -> float | None:
    """A WGS84 coordinate as sites print it: '51.03151', '3,76455', 51.03."""
    if raw is None:
        return None
    m = re.search(r"-?\d{1,3}(?:[.,]\d+)?", str(raw))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def plausible_position(lat: float, lon: float, postcode: str) -> bool:
    """Inside Belgium and, when the postcode is known, within 20 km of its
    centroid: a map centred on the office, or on the country, is not the
    house."""
    if not (49.4 < lat < 51.6 and 2.4 < lon < 6.5):
        return False
    if postcode:
        from .geo import postcode_centroid
        centre = postcode_centroid(postcode)
        if centre is not None:
            dlat = (lat - centre[0]) * 111.0
            dlon = (lon - centre[1]) * 111.0 * math.cos(math.radians(centre[0]))
            if math.hypot(dlat, dlon) > 20.0:
                return False
    return True


# data-lat="51.03151" data-long="3.76455" (rosseel.be) and the other spellings
LAT_ATTRS = ("data-lat", "data-latitude", "data-map-lat", "data-marker-lat")
LON_ATTRS = ("data-lng", "data-lon", "data-long", "data-longitude", "data-map-lng",
             "data-map-lon", "data-marker-lng", "data-marker-lon")
# maps.google.com/maps?q=51.02891,3.671 / cbll= / ll= / center= / @51.0,3.6
MAP_COORDS = re.compile(r"(?:[?&](?:q|ll|cbll|center|sll|daddr)=|/@)(-?\d{1,2}\.\d{3,}),(-?\d{1,2}\.\d{3,})")


def page_coordinates(soup: BeautifulSoup, postcode: str = "") -> tuple[float, float] | None:
    """The house position the page itself carries, or None.

    rosseel.be hangs the map pin on the "show map" link as data-lat/data-long;
    other sites put the coordinates in a Google Maps link. That is the agency
    placing its own pin, which outranks any lookup - and is the only source
    when the agency misspells the street ("Bruilofstraat" for Bruiloftstraat).
    Links and pins in the page chrome are the office; a point far from the
    listing's postcode is a default map centre.
    """
    chrome = {id(n) for n in chrome_nodes(soup)}
    for node in soup.find_all(True):
        if id(node) in chrome or any(id(p) in chrome for p in node.parents):
            continue
        lat = next((_coord(node.get(a)) for a in LAT_ATTRS if node.get(a)), None)
        lon = next((_coord(node.get(a)) for a in LON_ATTRS if node.get(a)), None)
        if lat is None or lon is None:
            url = node.get("href") or node.get("src") or ""
            m = MAP_COORDS.search(url) if MAP_URL.search(url) else None
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
        if plausible_position(lat, lon, postcode):
            return lat, lon
    return None


def map_link_address(soup: BeautifulSoup) -> str:
    """The address a "show on map" link carries, or "".

    irres.be, huysewinkel.be and carloeggermont.be all link the listing to
    Google Maps with the house's own address in the URL:
    /maps/place/Kappetragel+1,9052+Zwijnaarde. That is the site telling us the
    address outright, and it reads "Kappetragel 1" where the street patterns
    would not, since the name has no street word. Links in the page chrome
    (route to the office) are skipped, and a link that carries coordinates
    instead of an address yields nothing.
    """
    chrome = {id(n) for n in chrome_nodes(soup)}
    for node in soup.find_all(["a", "iframe"]):
        url = node.get("href") or node.get("src") or ""
        if not MAP_URL.search(url):
            continue
        if any(id(p) in chrome for p in node.parents):
            continue
        text = _map_query(url)
        if text and len(text) <= 100 and re.search(r"\d", text):
            return _address_case(text)
    return ""


def _map_query(url: str) -> str:
    parsed = urlparse(url)
    m = re.search(r"/maps/place/([^/]+)", parsed.path)
    if m:
        raw = m.group(1)
    else:
        query = parse_qs(parsed.query)
        raw = next((v[0] for k in ("q", "query", "destination", "daddr") if (v := query.get(k))), "")
    text = re.sub(r"\s+", " ", unquote_plus(raw)).strip()
    if re.match(r"^-?\d+(?:\.\d+)?\s*,\s*-?\d+", text):
        return ""                                  # "51.02,3.67": coordinates
    # "Kappetragel 1,9052 Zwijnaarde": a comma glued to the postcode would
    # read as a thousands separator and hide the house number.
    return re.sub(r",(?=\S)", ", ", text)


# "Olsene, 9870" / "Burcht 2070": the place before the postcode, as a map link
# or a labelled element may order them.
CITY_POSTAL = re.compile(
    r"\b((?:De|Den|Sint|Groot|Nieuw)\s+)?([A-Z][a-zà-ÿ]{2,}(?:-[A-Z][a-zà-ÿ]+)*),?\s+([1-9]\d{3})\b(?![\d.,]?\d)"
)


def address_place(text: str) -> tuple[str, str]:
    """(postcode, city) read off an address line the site itself labelled.

    Accepts "9040 Sint-Amandsberg", "Olsene, 9870" and, with no postcode at
    all, a trailing municipality the table knows ("Magerstraat 18 , Mere").
    Either part may come back empty; nothing here is guessed.
    """
    m = re.search(POSTAL_CITY, text)
    if m and is_known_postcode(m.group(1)) and m.group(3).lower() not in NOT_A_CITY:
        # "9840 Nazareth-De Pinte": the fused name runs past the hyphen.
        city = _whole_place(f"{m.group(2) or ''}{m.group(3)}".strip(), text[m.end():m.end() + 40])
        # infinimo.be links the map as "Drongen 9031 Ernest Solvynsdreef 36":
        # the town comes first and the word after the postcode starts the
        # street. A name the table knows before the postcode wins over an
        # unknown one after it; an unknown hamlet after it is still accepted.
        before = CITY_POSTAL.search(text)
        town_first = bool(before) and before.group(3) == m.group(1) and is_known_place(
            f"{before.group(1) or ''}{before.group(2)}".strip())
        if is_known_place(city) or not town_first:
            return m.group(1), city
    m = CITY_POSTAL.search(text)
    if m and is_known_postcode(m.group(3)):
        city = f"{m.group(1) or ''}{m.group(2)}".strip()
        if is_known_place(city):
            return m.group(3), city
    words = re.findall(r"[^\W\d_]+(?:[-\s][^\W\d_]+)*", text)
    if words and is_known_place(words[-1].strip()):
        return "", words[-1].strip()
    return "", ""


# ".../te-koop/tiegemberg-34-8573-tiegem-4485313" (real-immo.be),
# ".../aanbod/parkstraat-78-8560-wevelgem" (mijnhabitat.be),
# ".../pand/de-pintelaan-209-9000-gent/8563167" (entree.be)
SLUG_ADDRESS = re.compile(r"/([a-z][a-z-]{2,40}?)-(\d{1,4}[a-z]?)-([1-9]\d{3})-([a-z][a-z-]{2,30}?)(?:-\d+[a-z0-9-]*)?(?:/|$)")


def slug_address(url: str) -> tuple[str, str, str]:
    """(street, postcode, city) when the URL slug spells the whole address
    out, street-number-postcode-city. The street must carry a street word:
    a slug is not labelled, so "lot-3-9000-gent" must not become a street."""
    m = SLUG_ADDRESS.search(urlparse(url).path.lower())
    if not m or not is_known_postcode(m.group(3)):
        return "", "", ""
    street = find_street(f"{_titleize(m.group(1)).replace('-', ' ')} {m.group(2).upper()}")
    if not street:
        return "", "", ""
    return street, m.group(3), _titleize(m.group(4))


def chrome_streets(soup: BeautifulSoup, strong_only: bool = False) -> set[str]:
    """Every street address printed in the page chrome. On an agency site
    that is the office address, repeated on every listing page."""
    found: set[str] = set()
    for node in chrome_nodes(soup, strong_only):
        # Block-aware, like the body read, or the two disagree on the same
        # door: "<h3>...met Cannoodt</h3><h5>Wintertuinstraat 2</h5>" must
        # not be recorded here as "cannoodt wintertuinstraat 2".
        text = re.sub(r"[^\S\n]+", " ", block_text(node))
        for m in STREET.finditer(text):
            name = _clean_street_name(m.group(1), "")
            if name:
                found.add(f"{name} {_normal_number(m.group(2))}".lower())
    return found


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
            rf"(?:vraagprijs|verkoopprijs|richtprijs|prijs|price)\s*:?\s*(?:€\s*)?{AMOUNT}",
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
            # infinimo.be: "196m² vloeroppervlakte" in the banner and
            # "Vloeroppervlakte 196 m²" among the facts.
            r"vloeropp\w*\D{0,15}([\d.,]{2,8})",
            r"([\d.,]{2,8})\s*m²?\s*vloeropp",
            r"([\d.,]{2,8})\s*m²?\s*bewoonbaar",
            r"living\s+area\D{0,15}([\d.,]{2,8})",
        ))
        listing.living_area_m2 = _plausible_area(_int(raw), 15, 2000)

    if listing.plot_area_m2 is None:
        raw = _first(text, (
            r"grond\s*opp\w*\D{0,15}([\d.,]{2,9})",
            r"perceelopp\w*\D{0,15}([\d.,]{2,9})",
            r"opp\w*\s+perceel\D{0,15}([\d.,]{2,9})",
            r"terrein\s*opp\w*\D{0,15}([\d.,]{2,9})",      # infinimo.be: "Terrein opp. 474 m²"
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


# oximo.be prints its facts strip as one block per fact, the number first and
# the label abbreviated: "<span>150m<sup>2</sup></span> bew. opp.",
# "<span>100m<sup>2</sup></span> grond opp.". Flattened to one line that is
# "150m 2 bew. opp. 100m 2 grond opp.", where a label-first pattern reads the
# plot's 100 as the living area. Read per line of `block_text` instead, with
# the number anchored to the start of its own block: only then is the number
# right before a label that label's value, and not the previous fact's.
FACT_LINE_LIVING = re.compile(
    r"^([\d.,]{2,8})\s*m\s*(?:²|2)?\s*(?:bew\.?|bewoonbare)\s*opp", re.IGNORECASE | re.MULTILINE)
FACT_LINE_PLOT = re.compile(
    r"^([\d.,]{2,9})\s*m\s*(?:²|2)?\s*(?:grond|perceel)\s*opp", re.IGNORECASE | re.MULTILINE)


def _from_fact_lines(lines: str, listing: Listing) -> None:
    """Surfaces printed as "<number> m² <label>", one fact per block."""
    if listing.living_area_m2 is None:
        m = FACT_LINE_LIVING.search(lines)
        listing.living_area_m2 = _plausible_area(_int(m.group(1)) if m else None, 15, 2000)
    if listing.plot_area_m2 is None:
        m = FACT_LINE_PLOT.search(lines)
        listing.plot_area_m2 = _plausible_area(_int(m.group(1)) if m else None, 20, 200_000)


# irres.be prints its "Kenmerken" strip as an icon plus a bare value, and
# names the fact only in an attribute: <li data-value="Slaapkamers"><p>3</p>,
# <li data-value="Terrein oppervlakte"><p>832 m²</p>. Nothing in the text
# then stands next to the number, so the label is read off the attribute.
FACT_LABEL_ATTRS = ("data-value", "data-label", "data-title", "data-key", "aria-label", "title")
FACT_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bedrooms", re.compile(r"^(?:aantal\s+)?slaapkamers?$|^bedrooms?$", re.IGNORECASE)),
    ("living_area_m2", re.compile(r"^(?:bewoonbare?|woon|vloer)\s*opp\w*$|^living\s+area$", re.IGNORECASE)),
    ("plot_area_m2", re.compile(r"^(?:terrein|grond|perceel)\s*opp\w*$|^(?:oppervlakte\s+)?(?:perceel|terrein)$", re.IGNORECASE)),
    ("epc_kwh", re.compile(r"^epc(?:\s*\(?kwh/m²\)?)?$|^e-?peil$", re.IGNORECASE)),
)
FACT_VALUE = re.compile(r"^\s*([\d.,]{1,9})\s*(?:m²|m2|m|kwh/m²|kwh)?\s*$", re.IGNORECASE)


def _from_labelled_attrs(soup: BeautifulSoup, listing: Listing) -> None:
    """Facts whose label is an attribute of the element holding the value."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for node in soup.find_all(lambda t: any(t.has_attr(a) for a in FACT_LABEL_ATTRS)):
        if id(node) in chrome or any(id(p) in chrome for p in node.parents):
            continue
        labels = [str(node.get(a) or "").strip() for a in FACT_LABEL_ATTRS]
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        m = FACT_VALUE.match(text)
        if not m:
            continue
        for field_name, pattern in FACT_LABELS:
            if getattr(listing, field_name) is not None:
                continue
            if any(pattern.match(label) for label in labels if label):
                value = _int(m.group(1))
                if field_name == "bedrooms":
                    value = value if value is not None and value <= 20 else None
                elif field_name == "living_area_m2":
                    value = _plausible_area(value, 15, 2000)
                elif field_name == "plot_area_m2":
                    value = _plausible_area(value, 20, 200_000)
                else:
                    value = value if value is not None and 0 < value <= 1500 else None
                setattr(listing, field_name, value)


# Containers that hold the property's photos. A page without og:image or
# structured data (oximo.be) still shows the house in a gallery block; the
# first picture in one is the listing's picture. Icons, logos and inline
# data URIs are not pictures.
GALLERY_CLASS = re.compile(
    r"gallery|galerij|images|photo|foto|slider|carousel|hero|lightbox|fancybox|swiper|fullscreen",
    re.IGNORECASE)


def _image_source(img: Tag) -> str:
    """The picture an <img> shows: src, a lazy-load data-src, or the first
    candidate of a srcset when src is only a transparent placeholder
    (irres.be: src="data:image/svg+xml,...", srcset="/uploads_c/....jpg 360w")."""
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        src = str(img.get(attr) or "").strip()
        if src and not src.lower().startswith("data:"):
            return src
    for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
        srcset = str(img.get(attr) or "").strip()
        if srcset:
            first = srcset.split(",")[0].strip().split()
            if first and not first[0].lower().startswith("data:"):
                return first[0]
    return ""


def gallery_image(soup: BeautifulSoup) -> str:
    """The first photo inside a gallery-like container outside the chrome."""
    chrome = {id(n) for n in chrome_nodes(soup)}
    for img in soup.find_all("img"):
        src = _image_source(img)
        low = src.lower()
        if not src or low.endswith(".svg") or "logo" in low:
            continue
        if any(id(p) in chrome for p in img.parents):
            continue
        for node in img.parents:
            if node is None or node.name in ("body", "html", "[document]"):
                break
            tokens = " ".join(node.get("class") or []) + " " + str(node.get("id") or "")
            if GALLERY_CLASS.search(tokens) or node.has_attr("data-fancybox"):
                return src
    return ""


def _locate(listing: Listing, text: str,
            chrome_places: dict[tuple[str, str], int] | None = None) -> None:
    chrome_places = chrome_places or {}
    """Fill in city/postcode, most trustworthy source first.

    Page text is the last resort on purpose: an agency's own address sits in
    the header or footer of every single listing page, so trusting the text
    first would stamp the office's municipality onto the whole portfolio.
    JSON-LD (already applied) and the URL describe the actual property.
    """
    url, title = listing.url, listing.title

    # "Woning te koop - Beekstraat 27, 9940 Wippelgem": a postcode in the
    # title is the property's, and the most reliable thing on the page.
    if not (listing.postal_code and listing.city):
        m = re.search(CITY_PAREN_POSTCODE, title)
        if m and m.group(1).lower() not in NOT_A_CITY:
            listing.postal_code = listing.postal_code or m.group(2)
            listing.city = listing.city or _place_case(m.group(1))
    if not (listing.postal_code and listing.city):
        m = re.search(POSTAL_CITY, title)
        if m and m.group(3).lower() not in NOT_A_CITY:
            listing.postal_code = listing.postal_code or m.group(1)
            listing.city = listing.city or f"{m.group(2) or ''}{m.group(3)}".strip()

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
            if not m:
                continue
            candidate = _titleize(m.group(1))
            # "/woning-in-een-prachtige-tuin" is not a place. The "-in-" shape
            # is weak enough that the name must be a known municipality.
            if pattern is CITY_IN_URL_IN and not is_known_place(candidate):
                continue
            # "te-koop-in-het-hart-van-spiere-helkijn": prose, not a place.
            if re.match(r"(?:in|te|het|een|de|met|op)-", m.group(1)) and not is_known_place(candidate):
                continue
            listing.city = candidate
            break

    if not listing.city:
        # "/te-koop/deinze/woning/4486809" (demeester.be): a path segment that
        # is a municipality on its own.
        for segment in urlparse(url).path.lower().split("/"):
            if 3 <= len(segment) <= 30 and is_known_place(_titleize(segment)):
                listing.city = _titleize(segment)
                break

    if not listing.postal_code:
        for m in re.finditer(POSTAL_CITY, text):
            city = _whole_place(f"{m.group(2) or ''}{m.group(3)}".strip(), text[m.end():m.end() + 40])
            if city.lower() in NOT_A_CITY:
                continue
            # Body text is the weakest source: "2024 Energie" and "1250
            # Beschikbaar" have the shape of a postcode and a town, and the
            # office's "1000 Brussel" sits in every footer. So the postcode
            # must exist, and the pair must not be one the page chrome prints.
            # (The place name itself is not checked against the table: it
            # lists municipalities, not every hamlet an agency may name.)
            if not is_known_postcode(m.group(1)):
                continue
            if YEAR_LEAD.search(text[max(0, m.start() - 25):m.start()]):
                continue                       # "Bouwjaar 1930 Opp. bewoonbaar"
            printed_in_chrome = chrome_places.get((m.group(1), city.lower()), 0)
            if printed_in_chrome:
                in_body = len(re.findall(rf"\b{m.group(1)}\s+{re.escape(city)}\b", text))
                if in_body <= printed_in_chrome:
                    continue
            # Only accept a postcode from the page body when it agrees with the
            # city we already trust, or when we have no city at all.
            if listing.city and city.lower() != listing.city.lower():
                continue
            listing.postal_code = m.group(1)
            listing.city = listing.city or city
            break


def _whole_place(name: str, tail: str) -> str:
    """'Nazareth-De' + ' Pinte ...' -> 'Nazareth-De Pinte' when the table knows
    the fused municipality; otherwise the name as matched."""
    m = re.match(r"\s+([A-Z][a-zà-ÿ]+)", tail)
    if not m:
        return name
    # The 2025 fusions (Nazareth-De Pinte, Merelbeke-Melle) postdate the
    # bundled table, so a dangling particle is enough to take the next word.
    if is_known_place(f"{name} {m.group(1)}") or re.search(r"-(?:De|Den|Der|Ter|Ten|Op|Aan|Sint)$", name):
        return f"{name} {m.group(1)}"
    return name


def _place_case(name: str) -> str:
    """'DE PINTE' -> 'De Pinte', 'SINT-AMANDSBERG' -> 'Sint-Amandsberg'; mixed case kept."""
    if not name.isupper():
        return name
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), name)


def _titleize(slug: str) -> str:
    parts = slug.split("-")
    while len(parts) > 1 and parts[0].lower() in SLUG_TYPE_WORDS:
        parts.pop(0)
    return "-".join(part.capitalize() for part in parts)


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
    # The same fact in attribute form: a card that carries data-price and links
    # somewhere (rosseel.be prints the amount next to the link, not inside it).
    for card in soup.find_all(attrs={"data-price": True}):
        if card.find("a", href=True):
            card.decompose()
            removed += 1
    return removed


def price_candidates(text: str) -> set[int]:
    return {
        v for v in (_int(m) for m in re.findall(rf"€\s*{AMOUNT}", text))
        if v is not None and PRICE_FLOOR <= v <= PRICE_CEILING
    }


def has_labelled_price(text: str) -> bool:
    # "Prijs € 362.500" (immodavinci.be) is a label too; "prijs van ... tot" on
    # a search form is not, because a word sits between label and amount.
    return bool(re.search(r"\b(?:vraag|verkoop|richt)?prijs\s*:?\s*(?:€|\d)",
                          text, re.IGNORECASE))


# A monthly figure: € 250 to € 9.999. Below that it is a fee, above it the
# cheapest garage box.
RENT_MIN, RENT_MAX = 250, 10_000
# AMOUNT wants four digits or a thousands group; a rent is often "€ 680".
# "€ 950.00" is 950, "€ 1.925.000" is not a rent at all.
RENT_AMOUNT = r"(\d{1,2}[. ]\d{3}|\d{3,4})(?!\d)(?!\.\d{3})"
# Small amounts a sale page prints legitimately: the cadastral income
# (makelaarshuys.be "Niet geïndexeerd K.I.: € 334"), monthly charges of an
# apartment, a loan simulator's instalment, and the bounds of a search
# form's price filter (immo-emeraude.com "≤ € 500k Van € 500 tot € 750").
NOT_RENT_LABEL = re.compile(
    r"(?:\bk\.?\s*i\b\.?|kadastra\w*\s+inkomen|revenu\s+cadastral|"
    r"kosten|lasten|charges|syndic|provisie|voorschot|aflossing|per\s+jaar|/\s*jaar)"
    r"[^€]{0,40}$"
    # The range words only count right before the amount: "van" is in half
    # the Dutch titles ("appartement aan het Van Duyseplein € 1.500").
    r"|(?:\bvan|\btot|\bvanaf|\bfrom|\bmin|\bmax|≤|≥|<=|>=)\s*$", re.IGNORECASE)
# "€ 500k" is a filter bound in thousands, never a rent.
THOUSANDS_SUFFIX = re.compile(r"\s*k\b", re.IGNORECASE)
RENT_LABELLED = re.compile(
    rf"(?:huurprijs|maandhuur|loyer)\s*:?\s*€\s*{RENT_AMOUNT}"
    rf"|€\s*{RENT_AMOUNT}\s*(?:/|per)\s*(?:maand|mnd|month|mois)\b", re.IGNORECASE)
SALE_HINT = re.compile(r"te[-\s]?koop|kopen|for[-\s]?sale|[àa][-\s]?vendre|"
                       r"vraagprijs|verkoopprijs", re.IGNORECASE)


def is_rental(text: str, url: str = "", title: str = "") -> bool:
    """Is this page about a property for rent, judging by its amounts?

    Called only when no sale price was read. A labelled rent decides on its
    own. Otherwise the page is a rental when it prints a rent-sized amount
    and nothing (URL path, title) says it is for sale - a sale page whose
    price is rendered by JavaScript may still print the cadastral income,
    and that must stay a listing with an unknown price.
    """
    for m in RENT_LABELLED.finditer(text):
        if not NOT_RENT_LABEL.search(text[max(0, m.start() - 60):m.start()]):
            return True
    if SALE_HINT.search(urlparse(url).path) or SALE_HINT.search(title):
        return False
    for m in re.finditer(rf"€\s*{RENT_AMOUNT}", text):
        value = _int(m.group(1))
        if value is None or not RENT_MIN <= value < RENT_MAX:
            continue
        if NOT_RENT_LABEL.search(text[max(0, m.start() - 60):m.start()]):
            continue
        if THOUSANDS_SUFFIX.match(text, m.end()):
            continue
        return True
    return False


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


# -------------------------------------------------------------------- status

# A slug that says so: crevits.be files sold houses under "...-verkochte-panden-in-...".
SOLD_IN_URL = re.compile(r"verkocht|verhuurd|/sold|/vendu", re.IGNORECASE)


def detect_status(html: str, url: str = "") -> str:
    """Is the property still for sale, according to its own page?

    Returns "available", "option" or "sold". The URL counts first: a slug
    that says "verkocht" settles it. Otherwise only the listing's own banner
    counts: the title, the headings, and the first stretch of body text once
    the site chrome is gone - a "recent verkocht" strip in a sidebar must
    not sink a live listing, while the same words at the head of the title
    ("RECENT VERKOCHT. Exclusieve driegevelvilla ...") are about this house.
    """
    if url and SOLD_IN_URL.search(urlparse(url).path):
        return "sold"
    # wolff.be marks "in optie" on the overview card only; the detail page's
    # data blob carries the status the text never prints.
    prop = next_data_property(html)
    if prop is not None:
        known = NEXT_STATUS.get(str(prop.get("status") or "").lower())
        if known is not None:
            return known
    # irres.be keeps the page of a house whose mandate ended, price and all,
    # and only its breadcrumb data says so ("FOR_SALE_ORDER_ENDED - ...").
    # A breadcrumb that says "for sale" still yields to a banner in the text.
    crumb = breadcrumb_status(html)
    if crumb in ("sold", "option"):
        return crumb
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for node in chrome_nodes(soup):
        node.decompose()

    strong = " ".join(filter(None, [
        _meta(soup, "og:title", "twitter:title"),
        soup.title.get_text(" ", strip=True) if soup.title else "",
        *[h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:6]],
    ]))
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:800]
    if STATUS_OPTION.search(strong) or TITLE_OPTION.search(strong):
        return "option"
    if TITLE_SOLD.search(strong):
        return "sold"
    if STATUS_OPTION.search(body):
        return "option"
    if STATUS_SOLD.search(body):
        return "sold"
    return "available"


# -------------------------------------------------------------------- entry

def extract_listing(url: str, html: str, source: str) -> Listing | None:
    """Turn a detail page into a Listing, or None if it isn't a live listing."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    listing = Listing(url=url, source=source)

    listing.title = _meta(soup, "og:title", "twitter:title")
    listing.description = _meta(soup, "og:description", "description")
    listing.image_url = _meta(soup, "og:image", "twitter:image")

    # Two office lists: `office_hard` is what the site states outright (an
    # Organization block, the footer, the nav) and vetoes every source;
    # `office` adds the weak chrome - contact blocks and the like - and only
    # guards the body-text search, where the house is not named as such.
    office_hard = chrome_streets(soup, strong_only=True)
    office = chrome_streets(soup)
    offices_at = chrome_places(soup)
    objects = jsonld_objects(BeautifulSoup(html, "lxml"))
    office_hard |= _from_jsonld(objects, listing)
    office |= office_hard
    crumb = breadcrumb_facts(objects)
    if crumb:
        # irres.be: "FOR_SALE - 9000 Gent - Nederpolder 16" is the one place
        # a page without og:title or map link states its address.
        listing.postal_code = listing.postal_code or crumb["postal_code"]
        listing.city = listing.city or crumb["city"]
    next_prop = next_data_property(html)
    if next_prop is not None:
        _from_next_property(next_prop, listing)

    if not listing.title or BARE_PLACE_TITLE.match(listing.title):
        # aclea-vastgoed.be titles every page "9950 - Lievegem"; the heading
        # ("Te renoveren open bebouwing met landelijke ligging") is the title.
        listing.title = _heading(soup) or listing.title
    if not listing.title and soup.title:
        listing.title = soup.title.get_text(" ", strip=True)
    if listing.title and generic_title(listing.title, _meta(soup, "og:site_name"), url):
        # Older entree.be pages carry the site slogan ("Maak uw entree", or a
        # bare "Array") as og:title; the property is named further down.
        listing.title = _property_heading(soup) or _title_above_price(soup) or listing.title
        if crumb.get("street") and generic_title(listing.title, _meta(soup, "og:site_name"), url):
            # irres.be pages the agency never titled: town, price, nothing
            # else. The breadcrumb address beats the site name in a mail.
            listing.title = f"{crumb['street']}, {crumb['postal_code']} {crumb['city']}"
    if listing.title and address_title(listing.title):
        # mvastgoed.be has no og:title and names the listing by its address
        # in the structured data; the h1 ("Afgesloten garage te koop in de
        # Hoveniersstraat") says what it is, which the type guess needs.
        listing.title = _property_heading(soup, tags=("h1",)) or listing.title

    # infinimo.be/aanbod/te-koop/gent-9000 is headed "47 Panden te koop in
    # Gent": a count of properties is a catalogue, however the page is priced.
    headings = [listing.title or ""] + [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    if any(COUNT_HEADING.match(h) for h in headings):
        return None

    # Drop the cards, teasers and "similar properties" strips before reading any
    # numbers off the page, so nothing here can pick up a neighbour's price.
    strip_priced_links(soup)
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    lines = block_text(soup)

    # A Product/Offer block is structured data about *one* property; with it
    # the price count below means a "similar properties" strip, not a grid.
    if listing.price is None and is_index_page(text):
        return None

    # Rentals share URL shapes with sales on some sites; the title says which.
    lowered_title = listing.title.lower()
    if "te huur" in lowered_title and "te koop" not in lowered_title:
        return None

    _from_labelled_attrs(soup, listing)
    _from_fact_lines(lines, listing)
    _from_text(text, listing)

    # entree.be prints "€ 1.600" for a rental with nothing that says rent: the
    # price floor turned that into "price unknown", which the filters let
    # through. A page whose only amount is rent-sized is a rental.
    if listing.price is None and is_rental(text, url, listing.title):
        return None

    # An element the site itself labels as the address, read before the
    # weaker sources: it may be the only place the postcode is printed in a
    # shape the patterns can read (cannoodt.be: "9040 SINT-AMANDSBERG").
    labelled = labelled_address(soup) or map_link_address(soup)
    slug_street, slug_postcode, slug_city = slug_address(url)
    if labelled and not (listing.postal_code and listing.city):
        postcode, city = address_place(labelled)
        if city and (not listing.city or city.lower() == listing.city.lower()):
            listing.city = listing.city or city
            listing.postal_code = listing.postal_code or postcode
    if slug_postcode and not (listing.postal_code and listing.city):
        if not listing.city or slug_city.lower() == listing.city.lower():
            listing.city = listing.city or slug_city
            listing.postal_code = listing.postal_code or slug_postcode
    if not listing.city:
        listing.city = labelled_place(soup)

    _locate(listing, text, offices_at)

    # The street: structured data first, then the labelled address element,
    # the URL slug, the title, and finally an address line in the body.
    # Whatever the site states as its own door is never the house.
    if listing.street and listing.street.lower() in office_hard:
        listing.street = ""
    if not listing.street:
        listing.street = (
            find_street(labelled, listing.city, listing.postal_code, exclude=office_hard,
                        near_place=True, require_suffix=False)
            or find_street(labelled, listing.city, exclude=office_hard)
            or (slug_street if slug_street.lower() not in office_hard else "")
            or find_street(listing.title, listing.city, exclude=office_hard)
            # The breadcrumb's "Nederpolder 16 - 0201" names the unit after
            # the number; the house is what the registers know.
            or re.sub(r"\s+-\s+[\w/]+$", "", crumb.get("street", "")).strip()
            or find_street(lines, listing.city, listing.postal_code,
                           exclude=office, near_place=True)
            or ""
        )

    if not listing.image_url:
        listing.image_url = gallery_image(soup)
    if listing.image_url:
        listing.image_url = urljoin(url, listing.image_url)

    if listing.property_type == "unknown":       # a data blob may have said already
        listing.property_type = guess_property_type(listing.title, url, listing.description)

    # The agency's own map pin, when the structured data carried none.
    if listing.lat is None:
        pin = page_coordinates(soup, listing.postal_code)
        if pin is not None:
            listing.lat, listing.lon, listing.geo = pin[0], pin[1], "address"

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

    # Drop anything already sold or under option at first sight; the daily
    # re-check (scan.recheck_status) tracks what happens to the rest.
    if detect_status(html, url) != "available":
        return None

    return listing
