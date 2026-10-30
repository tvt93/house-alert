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
