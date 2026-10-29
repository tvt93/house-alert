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
from urllib.parse import parse_qs, unquote_plus, urljoin, urlparse

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
    r"opbrengsteigendom|bedrijfsvastgoed|nieuwbouw|projecten|luxevastgoed)/?$",
    re.IGNORECASE)
# "/te-koop/9000-gent": a town filter, when the slug is that postcode's town.
# cdf.be writes "/te-koop/2335-stijlvol-appartement-in-..." with a reference
# number in the same position, which the table tells apart from a postcode.
POSTCODE_TOWN_TAIL = re.compile(r"/([1-9]\d{3})-([a-z-]+)/?$")

PRICE_FLOOR = 25_000
PRICE_CEILING = 5_000_000

# A Belgian-formatted amount: 395.000 / 395 000 / 395000. Crucially it must not
# match across a separator into the next number on the page, so whitespace and
# dots are only allowed when followed by exactly three digits - and not by a
# fourth, or "EUR 295.000 9000 Gent" would read as 295.000900.
AMOUNT = r"(\d{1,3}(?:[.   ]\d{3})+(?!\d)|\d{4,8})"

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
    to that postcode? Then it filters a catalogue by town, it is not a house."""
    m = POSTCODE_TOWN_TAIL.search(path.lower())
    if not m:
        return False
    from .geo import postcode_table
    entry = postcode_table().get(m.group(1))
    if not entry:
        return False
    slug = m.group(2).replace("-", " ")
    return any(slug == str(name).lower().replace("-", " ") for name in entry.get("names", []))


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
            lat, lon = _num(str(geo.get("latitude") or "")), _num(str(geo.get("longitude") or ""))
            if lat is not None and lon is not None and 49.4 < lat < 51.6 and 2.4 < lon < 6.5:
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
    nodes += [n for n in soup.find_all(id=True)
              if n.name not in PAGE_ROOTS and pattern.search(str(n.get("id")))]
    nodes += [n for n in soup.find_all(class_=True)
              if n.name not in PAGE_ROOTS and any(pattern.search(c) for c in n.get("class", []))]
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


def _address_case(text: str) -> str:
    """'wittemolenstraat 158 - 9040 SINT-AMANDSBERG' -> 'Wittemolenstraat 158 - 9040 Sint-Amandsberg'."""
    return re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0).capitalize() if m.group(0).islower() or m.group(0).isupper() else m.group(0),
        text,
    )


MAP_URL = re.compile(r"google\.[a-z.]+/maps|maps\.google\.[a-z.]+/|goo\.gl/maps|maps\.app\.goo\.gl",
                     re.IGNORECASE)


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
        return m.group(1), _whole_place(f"{m.group(2) or ''}{m.group(3)}".strip(), text[m.end():m.end() + 40])
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
    not sink a live listing.
    """
    if url and SOLD_IN_URL.search(urlparse(url).path):
        return "sold"
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
    for text in (strong, body):
        if STATUS_OPTION.search(text):
            return "option"
        if STATUS_SOLD.search(text):
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
    office_hard |= _from_jsonld(jsonld_objects(BeautifulSoup(html, "lxml")), listing)
    office |= office_hard

    if not listing.title or BARE_PLACE_TITLE.match(listing.title):
        # aclea-vastgoed.be titles every page "9950 - Lievegem"; the heading
        # ("Te renoveren open bebouwing met landelijke ligging") is the title.
        listing.title = _heading(soup) or listing.title
    if not listing.title and soup.title:
        listing.title = soup.title.get_text(" ", strip=True)

    # Drop the cards, teasers and "similar properties" strips before reading any
    # numbers off the page, so nothing here can pick up a neighbour's price.
    strip_priced_links(soup)
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    # A Product/Offer block is structured data about *one* property; with it
    # the price count below means a "similar properties" strip, not a grid.
    if listing.price is None and is_index_page(text):
        return None

    # Rentals share URL shapes with sales on some sites; the title says which.
    lowered_title = listing.title.lower()
    if "te huur" in lowered_title and "te koop" not in lowered_title:
        return None

    _from_text(text, listing)

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
            or find_street(block_text(soup), listing.city, listing.postal_code,
                           exclude=office, near_place=True)
            or ""
        )

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

    # Drop anything already sold or under option at first sight; the daily
    # re-check (scan.recheck_status) tracks what happens to the rest.
    if detect_status(html, url) != "available":
        return None

    return listing
