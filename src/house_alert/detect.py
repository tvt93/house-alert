"""Platform fingerprinting.

Fetch an agency's homepage and look for the tell-tale asset hosts, script
names and markup that each listing platform leaves behind. Purely advisory:
an undetected site still works via the generic sitemap scraper.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Agency
from .net import Fetcher

# Ordered most-specific-first; the first platform with a hit wins.
FINGERPRINTS: list[tuple[str, tuple[str, ...]]] = [
    ("sweepbright", ("sweepbright", "estates.api.sweepbright", "sweepbright.com")),
    ("whise", ("whise.eu", "whise.be", "whisecdn", "whise-", "wsapi.whise")),
    ("skarabee", ("skarabee.com", "skarabee.be", "skarabee-", "sk-widget")),
    ("omnicasa", ("omnicasa", "onlineadmin.omnicasa", "ocwebsite")),
]


@dataclass
class DetectResult:
    agency: Agency
    platform: str          # detected platform, or "auto" when unrecognised
    reachable: bool
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.platform != self.agency.platform


def detect(agency: Agency, fetcher: Fetcher) -> DetectResult:
    html = fetcher.get(agency.url)
    if html is None:
        return DetectResult(agency, agency.platform, reachable=False,
                            note="homepage unreachable")

    low = html.lower()
    for platform, markers in FINGERPRINTS:
        hit = next((m for m in markers if m in low), None)
        if hit:
            return DetectResult(agency, platform, reachable=True, note=f"matched '{hit}'")

    return DetectResult(agency, "auto", reachable=True, note="no known platform marker")
