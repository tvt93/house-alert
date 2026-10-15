"""Export the listing store as the data file behind the overview page.

`docs/` is served by GitHub Pages: `index.html` is the static app and
`listings.json` is written here after every run. Only fields the page shows
are exported, newest first.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT
from .models import Listing
from .store import ListingStore

SITE_DIR = REPO_ROOT / "docs"

PUBLIC_FIELDS = (
    "id", "url", "source", "region", "title", "city", "postal_code", "street",
    "price", "property_type", "bedrooms", "living_area_m2", "plot_area_m2",
    "epc_kwh", "image_url", "description", "first_seen", "lat", "lon", "geo",
)


def listing_record(listing: Listing) -> dict:
    d = listing.to_dict()
    out = {k: d.get(k) for k in PUBLIC_FIELDS}
    out["description"] = (out["description"] or "")[:300]
    return out


def export_site(
    store: ListingStore,
    out_dir: Path | None = None,
    agency_count: int = 0,
    unreachable: int = 0,
) -> Path:
    out_dir = out_dir or SITE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(store.listings.values(),
                     key=lambda l: (l.first_seen, l.id), reverse=True)
    payload = {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "agencies": agency_count,
        "unreachable": unreachable,
        "count": len(ordered),
        "listings": [listing_record(l) for l in ordered],
    }
    target = out_dir / "listings.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return target
