"""Loading and shaping of the YAML config files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Agency

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("HOUSE_ALERT_CONFIG_DIR", REPO_ROOT / "config"))
STATE_DIR = Path(os.environ.get("HOUSE_ALERT_STATE_DIR", REPO_ROOT / "state"))


class ConfigError(RuntimeError):
    pass


@dataclass
class Range:
    min: float | None = None
    max: float | None = None
    include_unknown: bool = True

    def accepts(self, value: float | None) -> bool:
        if value is None:
            return self.include_unknown
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True

    @property
    def is_open(self) -> bool:
        return self.min is None and self.max is None


@dataclass
class Criteria:
    property_types: list[str] = field(default_factory=list)
    price: Range = field(default_factory=Range)
    bedrooms: Range = field(default_factory=Range)
    living_area_m2: Range = field(default_factory=Range)
    plot_area_m2: Range = field(default_factory=Range)
    epc_max_kwh: float | None = None
    epc_include_unknown: bool = True
    include_municipalities: list[str] = field(default_factory=list)
    exclude_municipalities: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    highlight_keywords: list[str] = field(default_factory=list)

    @property
    def epc(self) -> Range:
        return Range(None, self.epc_max_kwh, self.epc_include_unknown)


def _range(raw: Any) -> Range:
    if not isinstance(raw, dict):
        return Range()
    return Range(
        min=raw.get("min"),
        max=raw.get("max"),
        include_unknown=bool(raw.get("include_unknown", True)),
    )


def _str_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw if x is not None]


def load_criteria(path: Path | None = None) -> Criteria:
    path = path or CONFIG_DIR / "criteria.yaml"
    if not path.exists():
        raise ConfigError(f"criteria file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    epc = raw.get("epc") or {}
    muni = raw.get("municipalities") or {}
    return Criteria(
        property_types=[t.lower() for t in _str_list(raw.get("property_types"))],
        price=_range(raw.get("price")),
        bedrooms=_range(raw.get("bedrooms")),
        living_area_m2=_range(raw.get("living_area_m2")),
        plot_area_m2=_range(raw.get("plot_area_m2")),
        epc_max_kwh=epc.get("max_kwh"),
        epc_include_unknown=bool(epc.get("include_unknown", True)),
        include_municipalities=[m.lower() for m in _str_list(muni.get("include"))],
        exclude_municipalities=[m.lower() for m in _str_list(muni.get("exclude"))],
        exclude_keywords=[k.lower() for k in _str_list(raw.get("exclude_keywords"))],
        highlight_keywords=[k.lower() for k in _str_list(raw.get("highlight_keywords"))],
    )


def load_agencies(
    path: Path | None = None,
    regions: list[str] | None = None,
    only: list[str] | None = None,
) -> list[Agency]:
    path = path or CONFIG_DIR / "agencies.yaml"
    if not path.exists():
        raise ConfigError(f"agency registry not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}

    agencies: list[Agency] = []
    for entry in raw.get("agencies") or []:
        if not entry.get("url"):
            continue
        a = Agency(
            name=str(entry.get("name") or entry["url"]),
            url=str(entry["url"]).rstrip("/"),
            region=str(entry.get("region", "")),
            platform=str(entry.get("platform", defaults.get("platform", "auto"))),
            enabled=bool(entry.get("enabled", defaults.get("enabled", True))),
        )
        if not a.enabled:
            continue
        if regions and a.region not in regions:
            continue
        if only and not any(o.lower() in a.name.lower() for o in only):
            continue
        agencies.append(a)

    if not agencies:
        raise ConfigError("no enabled agencies matched the given filters")
    return agencies


def save_agencies_platforms(platforms: dict[str, str], path: Path | None = None) -> int:
    """Write detected platforms back into agencies.yaml, keyed by agency url.

    Rewrites only the `platform:` value on matching lines so the file's
    comments and hand-tuned layout survive.
    """
    path = path or CONFIG_DIR / "agencies.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = 0

    for i, line in enumerate(lines):
        for url, platform in platforms.items():
            if f'url: "{url}"' not in line:
                continue
            if "platform:" in line:
                import re as _re

                new = _re.sub(r"platform:\s*[\w-]+", f"platform: {platform}", line)
            else:
                new = line.rstrip().rstrip("}").rstrip().rstrip(",")
                new = f"{new}, platform: {platform} }}"
            if new != line:
                lines[i] = new
                changed += 1
            break

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed
