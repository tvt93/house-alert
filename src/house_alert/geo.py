"""Where is a listing?

Two levels of precision, cheapest first:

  postcode  - the centroid of the postal code, from a bundled GeoNames table.
              Free, instant, and always available when a postcode is known.
  address   - the house itself, from Nominatim (OpenStreetMap) when the page
              gave us a street and number.

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
            point = self.lookup_address(listing.street, listing.postal_code or "", listing.city)
            if point is not None:
                listing.lat, listing.lon = point
                listing.geo = "address"

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

    def lookup_address(self, street: str, postcode: str, city: str) -> tuple[float, float] | None:
        """Street-level point, from cache or Nominatim. None when unknown.

        A definitive miss is cached so it is not retried every day; a network
        failure or an exhausted budget is not, so it is retried tomorrow.
        """
        key = cache_key(street, postcode, city)
        if key in self.entries:
            hit = self.entries[key]
            return (hit["lat"], hit["lon"]) if hit else None
        if not self.online or self.budget_left == 0:
            return None

        result = self._nominatim(street, postcode, city)
        if result == "error":
            return None
        if result is None:
            self.entries[key] = None
            return None
        self.entries[key] = {"lat": result[0], "lon": result[1]}
        return result

    # -------------------------------------------------------- nominatim

    def _nominatim(self, street: str, postcode: str, city: str) -> tuple[float, float] | None | str:
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
        return accept_hit(hits, postcode, city)


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
    if hit.get("class") == "boundary" or hit.get("addresstype") in AREA_TYPES:
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
