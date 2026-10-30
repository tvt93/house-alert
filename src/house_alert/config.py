"""Loading and shaping of the YAML config files."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import sealed
from .models import Agency

log = logging.getLogger(__name__)

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
    # Exact postcodes ("9000") or prefixes ("90" = 9000-9099). Empty = any.
    postcodes: list[str] = field(default_factory=list)

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


def criteria_from_dict(raw: dict[str, Any]) -> Criteria:
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
        postcodes=[str(p).strip() for p in _str_list(raw.get("postcodes"))],
    )


def load_criteria(path: Path | None = None) -> Criteria:
    """Criteria from one YAML file. Subscription files also work here: the
    `name` and `to` keys are simply ignored."""
    path = path or CONFIG_DIR / "subscriptions" / "default.yaml"
    if not path.exists():
        raise ConfigError(f"criteria file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return criteria_from_dict(raw)


# ----------------------------------------------------------- subscriptions

@dataclass
class Subscription:
    """One daily email: who gets it and what they want to see in it."""

    name: str
    recipients: list[str]
    criteria: Criteria
    path: Path | None = None
    unresolved: list[str] = field(default_factory=list)   # "$SECRET" names with no value

    @property
    def deliverable(self) -> bool:
        return bool(self.recipients)


MAIL_KEY = "MAIL_KEY"   # the private key that opens `enc:` recipients


def resolve_recipient(raw: str, secrets: dict[str, str] | None = None) -> str | None:
    """A recipient is an address, `$NAME` for an environment variable or
    Actions secret of that name, or `enc:...`: the address sealed by the
    overview page with the site's public key and opened here with the
    private key in MAIL_KEY. Both keep the address out of a public repository.
    None when it cannot be resolved (no such variable, no key, wrong key)."""
    raw = raw.strip()
    if sealed.is_sealed(raw):
        key = os.environ.get(MAIL_KEY) or (secrets or {}).get(MAIL_KEY) or ""
        if not key.strip():
            return None
        try:
            return sealed.open_sealed(raw, key).strip() or None
        except sealed.SealError as exc:
            log.warning("sealed recipient could not be opened: %s", exc)
            return None
    m = re.fullmatch(r"\$\{?([A-Z][A-Z0-9_]*)\}?", raw)
    if not m:
        return raw or None
    name = m.group(1)
    value = os.environ.get(name) or (secrets or {}).get(name) or ""
    return value.strip() or None


def load_subscriptions(directory: Path | None = None,
                       secrets: dict[str, str] | None = None) -> list[Subscription]:
    """Every subscription file. `$NAME` recipients resolve from the environment
    (the workflow maps each recipient secret to a variable of the same name)."""
    directory = directory or CONFIG_DIR / "subscriptions"
    if not directory.is_dir():
        raise ConfigError(f"subscriptions directory not found: {directory}")
    subs: list[Subscription] = []
    for path in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path.name}: expected a mapping at the top level")
        if not raw.get("enabled", True):
            continue
        recipients: list[str] = []
        unresolved: list[str] = []
        for entry in _str_list(raw.get("to")):
            value = resolve_recipient(entry, secrets)
            if value:
                for addr in value.split(","):
                    if addr.strip():
                        recipients.append(addr.strip())
            else:
                # An enc: token is long and meaningless in a report; name what it needs.
                unresolved.append(f"enc:… (needs {MAIL_KEY})" if sealed.is_sealed(entry) else entry)
        subs.append(Subscription(
            name=str(raw.get("name") or path.stem),
            recipients=recipients,
            criteria=criteria_from_dict(raw),
            path=path,
            unresolved=unresolved,
        ))
    return subs


def load_agencies(
    path: Path | None = None,
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
            platform=str(entry.get("platform", defaults.get("platform", "auto"))),
            enabled=bool(entry.get("enabled", defaults.get("enabled", True))),
            postcodes=[str(p) for p in (entry.get("postcodes") or defaults.get("postcodes") or [])],
            index_urls=[u.strip() for u in _str_list(entry.get("index_urls")) if u.strip()],
        )
        if not a.enabled:
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
