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
