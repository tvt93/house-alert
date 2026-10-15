"""Core data types."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any


PropertyType = str  # "house" | "apartment" | "land" | "commercial" | "unknown"


def listing_id(url: str) -> str:
    """Stable identity for a listing URL.

    Query strings and fragments are dropped so tracking parameters don't
    resurface a listing as "new" the next day.
    """
    canonical = url.split("?")[0].split("#")[0].rstrip("/").lower()
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


@dataclass
class Agency:
    name: str
    url: str
    region: str = ""
    platform: str = "auto"
    enabled: bool = True

    @property
    def host(self) -> str:
        return re.sub(r"^https?://", "", self.url).strip("/")


@dataclass
class Listing:
    """One property advertisement, normalised across sources."""

    url: str
    source: str                      # agency display name
    region: str = ""
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
