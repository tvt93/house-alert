"""Which listings have we already seen?

Deliberately a plain JSON file rather than SQLite: it is diffable, survives a
git round-trip, and merges without ceremony - which matters because the
scheduled run commits the updated state back to the repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .config import STATE_DIR
from .models import Listing

VERSION = 1
DEFAULT_RETENTION_DAYS = 180


@dataclass
class SeenStore:
    path: Path
    entries: dict[str, dict[str, str]]

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def load(cls, path: Path | None = None) -> "SeenStore":
        path = path or STATE_DIR / "seen.json"
        if not path.exists():
            return cls(path=path, entries={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A corrupt state file would otherwise mean re-emailing everything;
            # starting empty is the same outcome, so be explicit about it.
            raise RuntimeError(f"state file is not valid JSON: {path}")
        return cls(path=path, entries=raw.get("seen") or {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "seen": self.entries}
        self.path.write_text(
            json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------ use

    def is_new(self, listing: Listing) -> bool:
        return listing.id not in self.entries

    def record(self, listing: Listing, today: date | None = None) -> None:
        today = today or date.today()
        self.entries.setdefault(listing.id, {
            "first_seen": today.isoformat(),
            "url": listing.url,
            "source": listing.source,
        })

    def record_all(self, listings: list[Listing], today: date | None = None) -> None:
        for listing in listings:
            self.record(listing, today)

    def prune(self, retention_days: int = DEFAULT_RETENTION_DAYS,
              today: date | None = None) -> int:
        """Forget entries older than the retention window. Returns count removed."""
        today = today or date.today()
        cutoff = today - timedelta(days=retention_days)
        stale = [
            key for key, entry in self.entries.items()
            if _parse_date(entry.get("first_seen")) is not None
            and _parse_date(entry["first_seen"]) < cutoff  # type: ignore[operator]
        ]
        for key in stale:
            del self.entries[key]
        return len(stale)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------- listings

@dataclass
class ListingStore:
    """Every listing parsed in the retention window, with its coordinates.

    `seen.json` only remembers URLs so a page is fetched once; this file keeps
    what was read off those pages, because the overview site needs to show
    everything currently online, not just what came in today. A listing is
    stored once and never re-fetched, so fields are frozen at first sight.
    """

    path: Path
    listings: dict[str, Listing]

    @classmethod
    def load(cls, path: Path | None = None) -> "ListingStore":
        path = path or STATE_DIR / "listings.json"
        if not path.exists():
            return cls(path=path, listings={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError(f"listing file is not valid JSON: {path}")
        items = [Listing.from_dict(d) for d in raw.get("listings") or []]
        return cls(path=path, listings={l.id: l for l in items})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.listings.values(),
                         key=lambda l: (l.first_seen, l.id), reverse=True)
        payload = {"version": VERSION, "listings": [l.to_dict() for l in ordered]}
        self.path.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self.listings)

    def add(self, listing: Listing, today: date | None = None) -> bool:
        """Store a listing unless it is already known. Returns True when added."""
        if listing.id in self.listings:
            return False
        if not listing.first_seen:
            listing.first_seen = (today or date.today()).isoformat()
        self.listings[listing.id] = listing
        return True

    def add_all(self, listings: list[Listing], today: date | None = None) -> int:
        return sum(self.add(l, today) for l in listings)

    def needing_address_lookup(self) -> list[Listing]:
        """Listings with a street we have not yet resolved to a point."""
        return [l for l in self.listings.values()
                if l.street and (l.postal_code or l.city) and l.geo != "address"]

    def unmailed(self) -> list[Listing]:
        """Available listings no mail run has considered yet, oldest first."""
        return sorted((l for l in self.listings.values() if not l.mailed and l.available),
                      key=lambda l: (l.first_seen, l.id))

    def mark_mailed(self, listings: list[Listing]) -> None:
        for listing in listings:
            listing.mailed = True

    def due_for_check(self, today: date | None = None, every_days: int = 2,
                      limit: int = 400) -> list[Listing]:
        """Listings whose status should be verified again: never checked
        first, then the longest ago. Sold and vanished ones are final."""
        today = today or date.today()
        cutoff = (today - timedelta(days=every_days)).isoformat()
        due = [l for l in self.listings.values()
               if l.status in ("available", "option") and (not l.checked or l.checked <= cutoff)]
        due.sort(key=lambda l: (l.checked, l.first_seen, l.id))
        return due[:limit]

    def prune(self, retention_days: int = DEFAULT_RETENTION_DAYS,
              today: date | None = None, closed_days: int = 30) -> int:
        """Forget listings older than the retention window, and sold or
        vanished ones a month after that was established."""
        today = today or date.today()
        cutoff = today - timedelta(days=retention_days)
        closed_cutoff = (today - timedelta(days=closed_days)).isoformat()
        stale = [
            key for key, listing in self.listings.items()
            if (_parse_date(listing.first_seen) is not None
                and _parse_date(listing.first_seen) < cutoff)  # type: ignore[operator]
            or (listing.status in ("sold", "gone") and listing.checked
                and listing.checked <= closed_cutoff)
        ]
        for key in stale:
            del self.listings[key]
        return len(stale)
