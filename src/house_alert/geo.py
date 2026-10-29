"""Where is a listing?

Levels of precision, cheapest first:

  postcode  - the centroid of the postal code, from a bundled GeoNames table.
              Free, instant, and always available when a postcode is known.
  street    - the street itself, when the house number is nowhere to be found.
  address   - the house itself, from the regional address register (Flanders
              with Brussels, or Wallonia) or failing that Nominatim
              (OpenStreetMap), when the page gave us a street and number.

Nominatim is a shared, donated service with a strict usage policy: at most one
request per second, an identifying User-Agent, and results cached. All three
are enforced here. A listing that cannot be resolved at street level keeps its
postcode centroid rather than a guess, and `geo` says which one it got.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from .config import STATE_DIR
from .models import Listing
from .net import USER_AGENT

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY = 1.1          # policy says at least 1 s between requests
FLANDERS_URL = "https://geo.api.vlaanderen.be/geolocation/v4/Location"
FLANDERS_DELAY = 0.3           # no stated policy; stay well clear of bursts
WALLONIA_URL = "https://geoservices.wallonie.be/geocodeWS/geocode"
WALLONIA_DELAY = 0.3
DEFAULT_MAX_REQUESTS = 300     # per run; whatever is left is picked up tomorrow

_postcodes: dict[str, dict[str, Any]] | None = None


def postcode_table() -> dict[str, dict[str, Any]]:
    global _postcodes
    if _postcodes is None:
        text = resources.files("house_alert.data").joinpath("postcodes_be.json").read_text("utf-8")
        _postcodes = json.loads(text)
    return _postcodes


def postcode_centroid(postcode: str) -> tuple[float, float] | None:
    entry = postcode_table().get(str(postcode).strip())
    if not entry:
        return None
    return float(entry["lat"]), float(entry["lon"])


_by_name: dict[str, list[str]] | None = None


def _known_names() -> dict[str, list[str]]:
    """Normalised place name -> postcodes, from the bundled table."""
    global _by_name
    if _by_name is None:
        _by_name = {}
        for code, entry in postcode_table().items():
            for name in entry.get("names", []):
                _by_name.setdefault(_norm(str(name)), []).append(code)
    return _by_name


def city_centroid(city: str) -> tuple[float, float] | None:
    """Centroid for a place name, for listings that print a city but no
    postcode. Only the coordinates are used - the postcode itself is never
    written back onto the listing, because a name like "Gent" could mean one
    of several codes and the filters must not run on a guess."""
    codes = _known_names().get(_norm(city)) if city else None
    if not codes:
        return None
    return postcode_centroid(codes[0])


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


# Words a register or OSM may drop or add in a street name: the agency writes
# "Trieu De Wazon", OSM has "Trieu Wazon" and the Walloon register "Trieu de
# Wazon". Everything else must match word for word.
STREET_FILLER = frozenset("de du des la le les l d van der den het t s".split())


def _street_key(name: str) -> str:
    words = re.split(r"[\s'’]+", _norm(str(name or "")))
    return " ".join(w for w in words if w and w not in STREET_FILLER)


def bare_number(street: str) -> str:
    """'Grote Steenweg 105B' -> 'Grote Steenweg 105'; '' when the number
    carries no letter. A new-build project is sold as 105B, 105C, 105D at
    one gate: when the register does not know the letter yet, the bare
    number is the same plot, where the street centreline is not."""
    bare = re.sub(r"(\s\d+)[A-Za-z](?=\s|$)", r"\1", street, count=1)
    return bare if bare != street else ""


def _house_number(street: str) -> str:
    """'Kerkstraat 28 bus 3' -> '28'; '' when there is none."""
    m = re.match(r"\s*\S.*?\s(\d+[A-Za-z]?)", street)
    return m.group(1).lower() if m else ""


def region(postcode: str) -> str:
    """Which address register answers for a Belgian postcode: "flanders",
    "brussels", "wallonia", or "" when it is not a Belgian postcode."""
    if not re.fullmatch(r"[1-9]\d{3}", str(postcode).strip()):
        return ""                                 # "03750" is Spain, not 3750
    n = int(str(postcode).strip())
    if 1000 <= n <= 1299:
        return "brussels"
    if 1300 <= n <= 1499 or 4000 <= n <= 7999:
        return "wallonia"
    if 1500 <= n <= 3999 or 8000 <= n <= 9999:
        return "flanders"
    return ""


def cache_key(street: str, postcode: str, city: str) -> str:
    return f"{_norm(street)}|{_norm(postcode)}|{_norm(city)}"


@dataclass
class Geocoder:
    cache_path: Path = field(default_factory=lambda: STATE_DIR / "geocache.json")
    max_requests: int = DEFAULT_MAX_REQUESTS
    delay: float = NOMINATIM_DELAY
    online: bool = True
    entries: dict[str, dict[str, float] | None] = field(default_factory=dict)
    requests_made: int = 0
    _last_request_at: float = 0.0
    _last_flanders_at: float = 0.0
    _last_wallonia_at: float = 0.0
    _client: httpx.Client | None = None

    # ------------------------------------------------------------- cache

    @classmethod
    def load(cls, path: Path | None = None, **kwargs: Any) -> "Geocoder":
        path = path or STATE_DIR / "geocache.json"
        geocoder = cls(cache_path=path, **kwargs)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                geocoder.entries = raw.get("entries") or {}
            except json.JSONDecodeError:
                log.warning("geocache unreadable, starting empty: %s", path)
        return geocoder

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        self.cache_path.write_text(
            json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "Geocoder":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def budget_left(self) -> int:
        return max(0, self.max_requests - self.requests_made)

    # ----------------------------------------------------------- locate

    def locate(self, listing: Listing) -> bool:
        """Fill in lat/lon/geo as precisely as the data allows. True if changed."""
        before = (listing.lat, listing.lon, listing.geo)

        # A street with a postcode, or failing that a street with a town:
        # oximo.be and demeester.be print "Ledelaan 26" and "Sleidinge" but
        # never the postcode. Without a postcode `accept_hit` insists that
        # the hit names the town.
        if listing.street and (listing.postal_code or listing.city) and listing.geo != "address":
            found = self.lookup_address(listing.street, listing.postal_code or "", listing.city)
            if found is not None and (found[2] == "address" or listing.geo != "street"):
                listing.lat, listing.lon, listing.geo = found

        if listing.lat is None and listing.postal_code:
            centroid = postcode_centroid(listing.postal_code)
            if centroid is not None:
                listing.lat, listing.lon = centroid
                listing.geo = "postcode"

        if listing.lat is None and listing.city:
            centroid = city_centroid(listing.city)
            if centroid is not None:
                listing.lat, listing.lon = centroid
                listing.geo = "city"

        return (listing.lat, listing.lon, listing.geo) != before

    def lookup_address(self, street: str, postcode: str, city: str) -> tuple[float, float, str] | None:
        """(lat, lon, level) from cache or Nominatim, or None when unknown.

        Level "address" is the house. When OSM has the street but not the
        number - Kerkstraat 28 in Destelbergen - the street itself is looked
        up and the point is its centreline, level "street": a few hundred
        metres off at worst, where the postcode centroid is kilometres.

        Cache entries: {"lat", "lon"} is the house; {"lat", "lon", "level":
        "street"} the street; {"level": "none"} a definitive miss. A bare
        None is a miss written before the street fallback existed, and gets
        one street lookup. A network failure or an exhausted budget is never
        cached, so it is retried tomorrow.
        """
        key = cache_key(street, postcode, city)
        entry = self.entries.get(key)
        if isinstance(entry, dict):
            if "lat" in entry:
                return entry["lat"], entry["lon"], entry.get("level", "address")
            return None
        if not self.online or self.budget_left == 0:
            return None

        # The regional address register first: Flanders (with Brussels) or
        # Wallonia, chosen by postcode, both when there is none. A register
        # is the authority for its addresses, answers nothing rather than
        # the wrong house, and knows numbers OSM lacks. Nominatim covers the
        # rest, and the street alone is the last resort.
        name = street_name(street)
        bare = bare_number(street)
        registers = {
            "flanders": (self._flanders,), "brussels": (self._flanders,),
            "wallonia": (self._wallonia,),
        }.get(region(postcode), (self._flanders, self._wallonia))
        steps = (
            *((ask, street, False, "address") for ask in registers),
            *((ask, bare, False, "address") for ask in registers),
            (self._nominatim, street, False, "address"),
            *((ask, name, True, "street") for ask in registers),
            (self._nominatim, name, True, "street"),
        )
        for ask, what, street_only, level in steps:
            if not what:
                continue
            if self.budget_left == 0:
                return None                       # retried tomorrow, nothing cached
            result = ask(what, postcode, city, street_only=street_only)
            if result == "error":
                return None
            if result is not None:
                self.entries[key] = {"lat": result[0], "lon": result[1], "level": level}
                return result[0], result[1], level
        self.entries[key] = {"level": "none"}
        return None

    # -------------------------------------------------------- nominatim

    def _flanders(self, street: str, postcode: str, city: str,
                  street_only: bool = False) -> tuple[float, float] | None | str:
        """The Flemish government's geolocation service (Geopunt, backed by
        the Basisregisters address register, plus Urbis for Brussels). Free,
        no key, exact house positions. Outside its area it answers [].

        A house result must carry the requested house number and, when the
        listing has one, the postcode; "Kortrijksesteenweg 12" also returns
        number 120, and a street query returns the street itself."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=20.0,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "nl-BE,nl;q=0.9"},
            )
        wait = FLANDERS_DELAY - (time.monotonic() - self._last_flanders_at)
        if wait > 0:
            time.sleep(wait)
        self._last_flanders_at = time.monotonic()
        self.requests_made += 1

        query = f"{re.sub(r'\s+bus\s*\S+$', '', street, flags=re.IGNORECASE)}, {postcode} {city}".strip(", ")
        try:
            resp = self._client.get(FLANDERS_URL, params={"q": query, "c": 3})
        except httpx.HTTPError as exc:
            log.debug("flanders geolocation failed for %s: %s", street, exc)
            return "error"
        if resp.status_code != 200:
            log.debug("flanders geolocation returned %s for %s", resp.status_code, street)
            return "error"
        try:
            results = resp.json().get("LocationResult") or []
        except (ValueError, AttributeError):
            return "error"
        return accept_flanders(results, street, postcode, city, street_only)

    def _wallonia(self, street: str, postcode: str, city: str,
                  street_only: bool = False) -> tuple[float, float] | None | str:
        """The Walloon government's geocoder (geocodeWS, backed by the ICAR
        address register). Free, no key, exact house positions; nothing
        outside Wallonia. Asked for a number it does not know it answers
        with the street alone, which `accept_wallonia` only takes as such."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=20.0,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "nl-BE,nl;q=0.9"},
            )
        wait = WALLONIA_DELAY - (time.monotonic() - self._last_wallonia_at)
        if wait > 0:
            time.sleep(wait)
        self._last_wallonia_at = time.monotonic()
        self.requests_made += 1

        query = f"{re.sub(r'\s+bus\s*\S+$', '', street, flags=re.IGNORECASE)} {postcode} {city}".strip()
        try:
            resp = self._client.get(WALLONIA_URL, params={"address": query, "crs": "EPSG:4326"})
        except httpx.HTTPError as exc:
            log.debug("wallonia geocode failed for %s: %s", street, exc)
            return "error"
        if resp.status_code != 200:
            log.debug("wallonia geocode returned %s for %s", resp.status_code, street)
            return "error"
        try:
            candidates = resp.json().get("candidates") or []
        except (ValueError, AttributeError):
            return "error"
        return accept_wallonia(candidates, street, postcode, city, street_only)

    def _nominatim(self, street: str, postcode: str, city: str,
                   street_only: bool = False) -> tuple[float, float] | None | str:
        if self._client is None:
            self._client = httpx.Client(
                timeout=20.0,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "nl-BE,nl;q=0.9"},
            )
        wait = self.delay - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()
        self.requests_made += 1

        # Street + postcode is unambiguous in Belgium, and the city name as an
        # agency writes it ("Gullegem-Wevelgem-Heule") only makes OSM miss.
        params: dict[str, Any] = {
            "street": re.sub(r"\s+bus\s*\S+$", "", street, flags=re.IGNORECASE),
            "country": "Belgium",
            "format": "jsonv2", "limit": 1, "addressdetails": 1,
        }
        if postcode:
            params["postalcode"] = postcode
        elif city:
            params["city"] = city
        try:
            resp = self._client.get(NOMINATIM_URL, params=params)
        except httpx.HTTPError as exc:
            log.debug("nominatim failed for %s: %s", street, exc)
            return "error"
        if resp.status_code != 200:
            log.debug("nominatim returned %s for %s", resp.status_code, street)
            return "error"
        try:
            hits = resp.json()
        except ValueError:
            return "error"
        if street_only:
            return accept_street_hit(hits, street, postcode, city)
        return accept_hit(hits, postcode, city)


def street_name(street: str) -> str:
    """'Kerkstraat 28 bus 3' -> 'Kerkstraat'; '' when there is no name part."""
    return re.sub(r"\s+\d.*$", "", street).strip()


def accept_flanders(results: Any, street: str, postcode: str, city: str,
                    street_only: bool) -> tuple[float, float] | None:
    """The first Flemish geolocation result that is really the requested
    house (or, for the street fallback, the requested street)."""
    if not isinstance(results, list):
        return None
    number = _house_number(street)
    for hit in results:
        if not isinstance(hit, dict):
            continue
        kind = str(hit.get("LocationType") or "")
        zipcode = str(hit.get("Zipcode") or "")
        if postcode and zipcode and zipcode != str(postcode):
            continue
        if not postcode and city and _norm(city) not in _norm(str(hit.get("FormattedAddress") or "")):
            continue
        if street_only:
            if not kind.endswith("_straat"):
                continue
            if _street_key(str(hit.get("Thoroughfarename") or "")) != _street_key(street):
                continue
        else:
            if "huisnummer" not in kind:
                continue
            if str(hit.get("Housenumber") or "").lower() != number:
                continue
        try:
            loc = hit["Location"]
            return float(loc["Lat_WGS84"]), float(loc["Lon_WGS84"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def accept_wallonia(candidates: Any, street: str, postcode: str, city: str,
                    street_only: bool) -> tuple[float, float] | None:
    """The first geocodeWS candidate that is the requested house (or, for
    the street fallback, the requested street). A candidate carries the
    municipality as `city`, the postcode as `zone.ident` with the locality
    name, the `street`, and the `house` only when the number is known."""
    if not isinstance(candidates, list):
        return None
    number = _house_number(street)
    wanted_street = _street_key(street if street_only else street_name(street))
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        zone = cand.get("zone") or {}
        town = cand.get("city") or {}
        if postcode and str(zone.get("ident") or "") != str(postcode):
            continue
        if not postcode and city:
            if _norm(city) not in {_norm(str(zone.get("name") or "")), _norm(str(town.get("name") or ""))}:
                continue
        road = cand.get("street") or {}
        if _street_key(str(road.get("name") or "")) != wanted_street:
            continue
        if street_only:
            geometry = road.get("geometry")
        else:
            house = cand.get("house") or {}
            if not number or str(house.get("name") or "").lower() != number:
                continue
            geometry = house.get("geometry")
        coords = (geometry or {}).get("coordinates") or []
        try:
            return float(coords[1]), float(coords[0])
        except (IndexError, TypeError, ValueError):
            continue
    return None


def accept_street_hit(hits: Any, name: str, postcode: str, city: str) -> tuple[float, float] | None:
    """A point on the street itself, for the fallback when the house number
    is not in OSM. The hit must be a road carrying exactly the requested
    name, in the listing's postcode or a hit that names its town."""
    if not isinstance(hits, list) or not hits:
        return None
    hit = hits[0]
    # jsonv2 calls the OSM class "category"; the v1 format called it "class".
    if not isinstance(hit, dict) or (hit.get("category") or hit.get("class")) != "highway":
        return None
    address = hit.get("address") or {}
    if _street_key(str(address.get("road") or "")) != _street_key(name):
        return None
    found_postcode = str(address.get("postcode") or "")
    if found_postcode and postcode and found_postcode != str(postcode):
        if not _names_town(address, postcode, city):
            return None
    if not postcode and not _names_town(address, "", city):
        return None
    try:
        return float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None


AREA_TYPES = ("city", "town", "village", "municipality", "postcode", "suburb",
              "administrative", "county", "state")
PLACE_FIELDS = ("village", "town", "city", "municipality", "suburb", "hamlet",
                "neighbourhood", "city_district", "quarter")


def accept_hit(hits: Any, postcode: str, city: str = "") -> tuple[float, float] | None:
    """Take Nominatim's answer only when it is really the house, not the town.

    Asking for a street that OSM does not know returns the municipality
    centre, which would be plotted with false confidence. A result counts only
    when it is not an administrative area and its own postcode, if it has one,
    agrees with the listing's.

    The one exception is a house whose OSM postcode is the neighbouring one
    but whose address names the listing's town: Krekelstraat 29 in Zevergem
    (9840) is tagged 9052 in OSM, with "Zevergem" and "Nazareth-De Pinte" in
    its address. The names of the listing's postcode, from the bundled table,
    and the city as the agency wrote it both count as the town.
    """
    if not isinstance(hits, list) or not hits:
        return None
    hit = hits[0]
    if not isinstance(hit, dict):
        return None
    kind = hit.get("category") or hit.get("class")      # jsonv2 / v1 naming
    if kind in ("boundary", "highway") or hit.get("addresstype") in AREA_TYPES:
        return None
    address = hit.get("address") or {}
    found_postcode = str(address.get("postcode") or "")
    if found_postcode and found_postcode != str(postcode):
        if not _names_town(address, postcode, city):
            return None
    try:
        return float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _names_town(address: dict[str, Any], postcode: str, city: str) -> bool:
    """Does one of the hit's place fields name the listing's town?"""
    entry = postcode_table().get(str(postcode).strip()) or {}
    wanted = {_norm(str(n)) for n in entry.get("names", []) if n}
    if city:
        wanted.add(_norm(city))
    wanted.discard("")
    if not wanted:
        return False
    known = _known_names()
    for field_name in PLACE_FIELDS:
        value = address.get(field_name)
        if not value:
            continue
        parts = {_norm(str(value))}
        # "Nazareth-De Pinte" is a merged municipality and each half counts -
        # but only when every half is a place in its own right, or
        # "Sint-Denijs-Westrem" would pass as Westrem.
        halves = [_norm(p) for p in re.split(r"\s*-\s*", str(value))]
        if len(halves) > 1 and all(h in known for h in halves):
            parts.update(halves)
        if parts & wanted:
            return True
    return False
