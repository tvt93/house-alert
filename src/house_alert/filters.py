"""Criteria matching."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Criteria
from .models import Listing


@dataclass
class Verdict:
    listing: Listing
    passed: bool
    reason: str = ""      # why it was rejected; empty when it passed

    def __bool__(self) -> bool:
        return self.passed


def evaluate(listing: Listing, criteria: Criteria) -> Verdict:
    blob = f"{listing.title} {listing.description}".lower()

    for word in criteria.exclude_keywords:
        if word in blob:
            return Verdict(listing, False, f"excluded keyword '{word}'")

    if criteria.property_types:
        if listing.property_type == "unknown":
            # An unrecognised type is kept: better a stray listing in the email
            # than a silently dropped one.
            pass
        elif listing.property_type not in criteria.property_types:
            return Verdict(listing, False, f"type {listing.property_type} not wanted")

    checks = (
        ("price", criteria.price, listing.price),
        ("bedrooms", criteria.bedrooms, listing.bedrooms),
        ("living area", criteria.living_area_m2, listing.living_area_m2),
        ("plot area", criteria.plot_area_m2, listing.plot_area_m2),
        ("EPC", criteria.epc, listing.epc_kwh),
    )
    for label, rng, value in checks:
        if not rng.accepts(value):
            shown = "unknown" if value is None else value
            return Verdict(listing, False, f"{label} {shown} outside range")

    if criteria.postcodes:
        if not listing.postal_code:
            return Verdict(listing, False, "postcode unknown")
        if not any(listing.postal_code.startswith(p) for p in criteria.postcodes):
            return Verdict(listing, False, f"postcode {listing.postal_code} not wanted")

    where = f"{listing.city} {listing.postal_code} {listing.title}".lower()
    if criteria.include_municipalities:
        if not any(m in where for m in criteria.include_municipalities):
            return Verdict(listing, False, "outside included municipalities")
    for m in criteria.exclude_municipalities:
        if m in where:
            return Verdict(listing, False, f"excluded municipality '{m}'")

    listing.matched_keywords = [w for w in criteria.highlight_keywords if w in blob]
    return Verdict(listing, True)


def apply(listings: list[Listing], criteria: Criteria) -> tuple[list[Listing], list[Verdict]]:
    """Split listings into matches and rejections (rejections keep their reason)."""
    matches: list[Listing] = []
    rejected: list[Verdict] = []
    for listing in listings:
        verdict = evaluate(listing, criteria)
        (matches.append(listing) if verdict else rejected.append(verdict))
    return matches, rejected
