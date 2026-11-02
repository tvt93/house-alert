"""A daily mail set up from the overview page, delivered as a GitHub issue.

GitHub Pages is static, so the page cannot write a subscription file itself.
Instead the "Dagelijkse mail instellen" button opens a prefilled issue: a
readable summary for people, plus a hidden HTML comment carrying the request
as JSON. The `subscribe` workflow hands that body to `house-alert
subscribe-from-issue`, which validates it, writes
config/subscriptions/<slug>.yaml and lets the workflow commit it and close
the issue.

The address arrives sealed (see `sealed.py`): neither the issue nor the file
ever shows it in clear.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

TYPES = ("house", "apartment", "land", "commercial", "parking")
RANGES = ("price", "bedrooms", "living_area_m2", "plot_area_m2")
MARKER = "house-alert"
MAX_SUBSCRIPTIONS = 20
# enc: + base64 of 32 + 24 + 16 bytes of overhead plus an address.
SEALED_RE = re.compile(r"^enc:[A-Za-z0-9+/]{96,480}={0,2}$")


class SubscribeError(ValueError):
    """Why a request was refused; the message is shown on the issue."""


@dataclass
class Range:
    min: float | None = None
    max: float | None = None
    include_unknown: bool = True


@dataclass
class Request:
    name: str
    slug: str
    to: str
    property_types: list[str] = field(default_factory=list)
    ranges: dict[str, Range] = field(default_factory=dict)
    postcodes: list[str] = field(default_factory=list)


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFD", name.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "filter"


def payload_from_issue(body: str) -> dict[str, Any]:
    """The JSON the page hid in an HTML comment: `<!-- house-alert {...} -->`."""
    m = re.search(r"<!--\s*" + MARKER + r"\s*(\{.*?\})\s*-->", body or "", re.S)
    if not m:
        raise SubscribeError("dit bericht komt niet van de overzichtspagina (geen gegevens gevonden)")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise SubscribeError(f"de gegevens zijn beschadigd ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise SubscribeError("de gegevens hebben niet de verwachte vorm")
    return data


def _number(value: Any, key: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(str(value).replace(",", "."))
    except ValueError:
        raise SubscribeError(f"{key}: geen geldig getal")
    if n != n or n < 0 or n > 1e9:
        raise SubscribeError(f"{key}: geen geldig getal")
    return int(n) if n.is_integer() else n


def validate(raw: dict[str, Any]) -> Request:
    name = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(raw.get("name") or ""))).strip()[:60]
    if not name:
        raise SubscribeError("de naam ontbreekt")

    to = str(raw.get("to") or "").strip()
    if not SEALED_RE.match(to):
        raise SubscribeError("het adres is niet versleuteld; gebruik de knop op de pagina")

    wanted = raw.get("property_types") or []
    if not isinstance(wanted, list):
        raise SubscribeError("woningtypes hebben niet de verwachte vorm")
    types = list(dict.fromkeys(str(t).lower() for t in wanted))
    unknown = [t for t in types if t not in TYPES]
    if unknown:
        raise SubscribeError(f"onbekend woningtype: {', '.join(unknown)}")

    ranges: dict[str, Range] = {}
    for key in RANGES:
        r = raw.get(key) if isinstance(raw.get(key), dict) else {}
        lo, hi = _number(r.get("min"), key), _number(r.get("max"), key)
        if lo is not None and hi is not None and lo > hi:
            raise SubscribeError(f"{key}: het minimum is groter dan het maximum")
        ranges[key] = Range(lo, hi, r.get("include_unknown", True) is not False)

    codes = raw.get("postcodes") or []
    if not isinstance(codes, list) or len(codes) > 50:
        raise SubscribeError("postcodes: hoogstens 50")
    postcodes = list(dict.fromkeys(str(c).strip() for c in codes))
    if any(not re.fullmatch(r"\d{1,4}", c) for c in postcodes):
        raise SubscribeError("postcodes: 1 tot 4 cijfers")

    return Request(name=name, slug=slugify(name), to=to, property_types=types,
                   ranges=ranges, postcodes=postcodes)


# ------------------------------------------------------------------- yaml

def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _range(key: str, r: Range) -> str:
    fmt = lambda v: "" if v is None else str(v)
    return (f"{key}:\n  min: {fmt(r.min)}\n  max: {fmt(r.max)}\n"
            f"  include_unknown: {'true' if r.include_unknown else 'false'}\n")


def _list(items: list[str], quote: bool) -> str:
    if not items:
        return " []"
    return "\n" + "\n".join("  - " + (_q(x) if quote else x) for x in items)


def to_yaml(req: Request, today: date | None = None) -> str:
    today = today or date.today()
    return (
        f"# Dagelijkse mail, ingesteld vanaf de overzichtspagina op {today.isoformat()}.\n"
        "# Het adres is versleuteld met de sleutel van de site; alleen de mailrun (MAIL_KEY) kan het lezen.\n"
        "# Verwijder dit bestand om de mail te stoppen.\n\n"
        f"name: {_q(req.name)}\n"
        f"to:\n  - {_q(req.to)}\n"
        "enabled: true\n\n"
        f"property_types:{_list(req.property_types, False)}\n\n"
        + "\n".join(_range(k, req.ranges[k]) for k in RANGES) + "\n"
        f"postcodes:{_list(req.postcodes, True)}\n\n"
        "municipalities:\n  include: []\n  exclude: []\n\n"
        "exclude_keywords:\n  - garagebox\n  - staanplaats\n  - parkeerplaats\n"
        "  - opbrengsteigendom\n  - nieuwbouwproject\n  - handelspand\n\n"
        "highlight_keywords:\n  - tuin\n  - zuidgericht\n  - te renoveren\n"
    )


def write_subscription(req: Request, directory: Path, limit: int = MAX_SUBSCRIPTIONS) -> Path:
    """The new file; a taken name gets -2, -3, ... rather than overwriting."""
    directory.mkdir(parents=True, exist_ok=True)
    existing = {p.name.lower() for p in directory.iterdir() if p.suffix.lower() in (".yaml", ".yml")}
    if len(existing) >= limit:
        raise SubscribeError("het maximum aantal mails is bereikt; vraag de beheerder er een te verwijderen")
    name, n = f"{req.slug}.yaml", 2
    while name in existing:
        name, n = f"{req.slug}-{n}.yaml", n + 1
    path = directory / name
    path.write_text(to_yaml(req), encoding="utf-8")
    return path


def from_issue(body: str, directory: Path) -> Path:
    return write_subscription(validate(payload_from_issue(body)), directory)


# -------------------------------------------------------------- overview

CREATED_RE = re.compile(r"\bop (\d{4}-\d{2}-\d{2})\b")


def _created(text: str) -> str | None:
    """The date in the header comment to_yaml writes; None for hand-made files."""
    m = CREATED_RE.search(text.split("\n", 1)[0])
    return m.group(1) if m else None


def _public_range(raw: Any) -> dict[str, Any]:
    r = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in ("min", "max"):
        v = r.get(key)
        if v not in (None, ""):
            out[key] = v
    return out


def overview(directory: Path) -> list[dict[str, Any]]:
    """What the page may show about each subscription: the name, when it was
    set up and the filters. Never the recipients, not even sealed: the page
    only needs to know that a mail exists to offer to stop it."""
    records: list[dict[str, Any]] = []
    paths = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml")))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        rec: dict[str, Any] = {
            "slug": path.stem,
            "name": str(raw.get("name") or path.stem),
            "created": _created(text),
            "property_types": [str(t) for t in (raw.get("property_types") or [])],
            "postcodes": [str(p) for p in (raw.get("postcodes") or [])],
            "include_unknown": all(
                (raw.get(k) or {}).get("include_unknown", True) is not False
                for k in RANGES if isinstance(raw.get(k), dict)),
        }
        for key in RANGES:
            rec[key] = _public_range(raw.get(key))
        records.append(rec)
    return records


def export_overview(directory: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated": date.today().isoformat(), "subscriptions": overview(directory)}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


# ----------------------------------------------------------- unsubscribe

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def unsubscribe(raw: dict[str, Any], directory: Path,
                secrets: dict[str, str] | None = None) -> Path:
    """Remove the subscription the page asked to stop, but only when the
    address that came along (sealed, like on subscribing) is one of that
    mail's recipients. The repository is public and anyone can open an issue,
    so knowing the address is what proves the mail is yours. Returns the path
    that was removed."""
    from .config import resolve_recipient

    slug = str(raw.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise SubscribeError("de naam van de mail ontbreekt of is ongeldig")
    to = str(raw.get("to") or "").strip()
    if not SEALED_RE.match(to):
        raise SubscribeError("het adres is niet versleuteld; gebruik de knop op de pagina")

    path = next((p for p in (directory / f"{slug}.yaml", directory / f"{slug}.yml") if p.is_file()), None)
    if path is None:
        raise SubscribeError("deze mail bestaat niet (meer)")

    claimed = resolve_recipient(to, secrets)
    if not claimed:
        raise SubscribeError("het adres kon niet gelezen worden (sleutel ontbreekt of is veranderd)")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("to") if isinstance(data, dict) else None
    entries = entries if isinstance(entries, list) else ([entries] if entries else [])
    actual: set[str] = set()
    for entry in entries:
        value = resolve_recipient(str(entry), secrets) or ""
        actual.update(a.strip().lower() for a in value.split(",") if a.strip())
    if claimed.strip().lower() not in actual:
        raise SubscribeError("het adres komt niet overeen met het adres van deze mail")

    path.unlink()
    return path


def handle_issue(body: str, directory: Path,
                 secrets: dict[str, str] | None = None) -> tuple[str, Path]:
    """One entry point for the workflow: ("subscribe", new file) or
    ("unsubscribe", removed file), decided by the request itself."""
    payload = payload_from_issue(body)
    if payload.get("action") == "unsubscribe":
        return "unsubscribe", unsubscribe(payload, directory, secrets)
    return "subscribe", write_subscription(validate(payload), directory)
