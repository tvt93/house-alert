"""Digest rendering: photo-card HTML plus a plain-text fallback."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import REPO_ROOT
from ..models import Listing

TEMPLATE_DIR = Path(REPO_ROOT) / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def subject_for(listings: list[Listing], run_date: date) -> str:
    if not listings:
        return f"house-alert: geen nieuwe woningen ({run_date:%d/%m})"
    n = len(listings)
    cheapest = min((l.price for l in listings if l.price is not None), default=None)
    tail = f" vanaf € {cheapest:,}".replace(",", ".") if cheapest else ""
    noun = "nieuwe woning" if n == 1 else "nieuwe woningen"
    return f"{n} {noun}{tail}"


def render_html(
    listings: list[Listing],
    run_date: date,
    agency_count: int,
    candidate_count: int,
    scan_errors: int = 0,
) -> str:
    template = _env().get_template("digest.html.j2")
    return template.render(
        subject=subject_for(listings, run_date),
        listings=listings,
        run_date=run_date.strftime("%d/%m/%Y"),
        agency_count=agency_count,
        candidate_count=candidate_count,
        scan_errors=scan_errors,
    )


def render_text(listings: list[Listing], run_date: date) -> str:
    if not listings:
        return f"Geen nieuwe woningen op {run_date:%d/%m/%Y}."

    lines = [f"{len(listings)} nieuwe woningen - {run_date:%d/%m/%Y}", ""]
    for listing in listings:
        lines.append(f"{listing.price_display} - {listing.title or listing.url}")
        if listing.location_display:
            lines.append(f"  {listing.location_display}")
        facts = ", ".join(f"{label}: {value}" for label, value in listing.facts())
        if facts:
            lines.append(f"  {facts}")
        lines.append(f"  {listing.source}: {listing.url}")
        lines.append("")
    return "\n".join(lines)
