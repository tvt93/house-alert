"""Core data types."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any


PropertyType = str  # "house" | "apartment" | "land" | "commercial" | "parking" | "unknown"


def listing_id(url: str) -> str:
    """Stable identity for a listing URL.

    The scheme and a leading "www." are ignored: a site that links itself as
    both www.immo-lvb.be (menu) and immo-lvb.be (sitemap) is one site, and
    this hash is what keeps such a page from being stored - and shown on the
    map - twice. Query strings and fragments are dropped so tracking
    parameters don't resurface a listing as "new" the next day. The one
    exception is a numeric `id=`: Omnicasa sites address every property as
    /nl/te-koop?view=detail&id=795, so there the id *is* the identity - and
    the path is not, because the same property is also served as
    /nl/component/properties/?view=detail&id=795 (LD-Vastgoed, Pattymo).
    A "binnenkort te koop" page that becomes "te koop" keeps its slug and
    moves one path segment up (Rosseel), while the old page stays online:
    the segment is read as te-koop so the house does not appear twice.
    """
    path, _, query = url.split("#")[0].partition("?")
    path = re.sub(r"^https?://(www\.)?", "", path, flags=re.IGNORECASE)
    canonical = path.rstrip("/").lower().replace("/binnenkort-te-koop/", "/te-koop/")
    m = re.search(r"\bid=(\d+)", query)
    if m and re.search(r"\bview=detail\b", query):
        canonical = canonical.split("/", 1)[0] + f"?view=detail&id={m.group(1)}"
    elif m:
        canonical += f"?id={m.group(1)}"
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


@dataclass
class Agency:
    name: str
    url: str
    platform: str = "auto"
    enabled: bool = True
    # Postcode prefixes to keep ("8", "90"); empty = everything. For chains
    # with offices across the country. A listing whose postcode could not be
    # read is kept: unknown is never treated as a mismatch.
    postcodes: list[str] = field(default_factory=list)
    # Pages that list the properties, for a site whose sitemap and homepage
    # do not lead to them (engelvoelkers.com: one index per region and type).
    index_urls: list[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return re.sub(r"^https?://", "", self.url).strip("/")


@dataclass
class Listing:
    """One property advertisement, normalised across sources."""

    url: str
    source: str                      # agency display name
    title: str = ""
    city: str = ""
    postal_code: str = ""
    street: str = ""                 # "Kerkstraat 12" - only when read with confidence
    price: int | None = None
    property_type: PropertyType = "unknown"
    bedrooms: int | None = None
    living_area_m2: int | None = None
    plot_area_m2: int | None = None
    epc_kwh: int | None = None
    image_url: str = ""
    description: str = ""
    # Filled in by the pipeline, not the scrapers.
    matched_keywords: list[str] = field(default_factory=list)
    first_seen: str = ""             # ISO date the listing first appeared in a scan
    lat: float | None = None
    lon: float | None = None
    geo: str = ""                    # "address" | "postcode" | "city" | "" (not located)
    status: str = "available"        # "available" | "option" | "sold" | "gone"
    checked: str = ""                # ISO date the status was last verified on the page
    mailed: bool = False             # has a mail run already considered this listing?

    @property
    def available(self) -> bool:
        return self.status == "available"

    @property
    def id(self) -> str:
        return listing_id(self.url)

    @property
    def price_display(self) -> str:
        if self.price is None:
            return "Prijs op aanvraag"
        return f"€ {self.price:,}".replace(",", ".")

    @property
    def location_display(self) -> str:
        bits = [b for b in (self.postal_code, self.city) if b]
        place = " ".join(bits)
        if self.street and place:
            return f"{self.street}, {place}"
        return self.street or place

    def facts(self) -> list[tuple[str, str]]:
        """Key/value pairs to show on the email card, skipping unknowns."""
        out: list[tuple[str, str]] = []
        if self.bedrooms is not None:
            out.append(("Slaapkamers", str(self.bedrooms)))
        if self.living_area_m2 is not None:
            out.append(("Bewoonbaar", f"{self.living_area_m2} m²"))
        if self.plot_area_m2 is not None:
            out.append(("Grond", f"{self.plot_area_m2} m²"))
        if self.epc_kwh is not None:
            out.append(("EPC", f"{self.epc_kwh} kWh/m²"))
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Listing":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})
