"""One house, one row: a page served under two spellings, or relisted under a
new number with the old URL redirecting to it (irres.be, Sint-Denijslaan 26
stood on the map three times)."""

from collections import Counter
from datetime import date
from pathlib import Path

from house_alert import scan as scan_module
from house_alert.models import Agency, Listing, listing_id
from house_alert.net import Fetched
from house_alert.scan import moved_to, recheck_status, scan_agency
from house_alert.store import ListingStore, SeenStore


OLD = "https://irres.be/pand/9064127/instapklare-gezinswoning-met-vier-slaapkamers-en-groene-zichten"
BARE = "https://irres.be/pand/9064127/9064127"
NEW = "https://irres.be/pand/9098257/instapklare-gezinswoning-met-vier-slaapkamers-en-groene-zichten"


def test_property_number_in_the_path_is_the_identity():
    # irres.be serves the same house with and without its slug; immo-zone.be
    # renamed a slug; vlaemynck.be appended "-0"; fleximmo.be has two routes.
    assert listing_id(OLD) == listing_id(BARE)
    assert listing_id("https://www.immo-zone.be/te-koop/wetteren/woning-met-3-slks/97250") == \
        listing_id("https://immo-zone.be/te-koop/wetteren/woning-met-4-slks/97250")
    assert listing_id("https://www.vlaemynck.be/te-koop/veurne/grond/11200317/grond-0") == \
        listing_id("https://www.vlaemynck.be/te-koop/veurne/grond/11200317/grond")
    assert listing_id("https://www.fleximmo.be/nl/property/4494436/") == \
        listing_id("https://www.fleximmo.be/nl/te-koop/heule/bungalow/4494436/")
    # A new number is a new identity, and so is the same number on another site.
    assert listing_id(OLD) != listing_id(NEW)
    assert listing_id(BARE) != listing_id("https://other.be/pand/9064127/9064127")


def test_project_units_keep_their_own_identity():
    # immoboss.be nests the unit under the project number: the last number
    # counts, and the project page is not any of its units.
    project = "https://immoboss.be/nl/aanbod/3551684/residentie-rooots-in-9600-renaix"
    unit_a = project + "/4498004"
    unit_b = project + "/3551745"
    assert len({listing_id(project), listing_id(unit_a), listing_id(unit_b)}) == 3
    # Four digits is a postcode or a short id in a slug, not this rule.
    assert listing_id("https://x.be/te-koop/gent/villa/2753") != listing_id("https://x.be/te-koop/gent/villa/2754")
    # Dewaele's reference sits inside the slug and stays out of it.
    assert listing_id("https://www.dewaele.com/nl/te-koop/8900-ieper/huis/herenhuis-d8900-26207-258488") != \
        listing_id("https://www.dewaele.com/nl/te-koop/8950-heuvelland/huis/herenhuis-d8900-26207-h-260093")


def test_moved_to_only_for_another_listing_on_the_same_site():
    assert moved_to(Fetched(BARE, 200, "", NEW)) == NEW
    assert moved_to(Fetched(BARE, 200, "", OLD)) is None                     # same identity
    assert moved_to(Fetched(BARE, 200, "", BARE)) is None
    assert moved_to(Fetched(BARE, 200, "", "https://irres.be/")) is None     # bounced home: gone
    assert moved_to(Fetched(BARE, 200, "", "https://x.be/pand/1/2")) is None  # another site
    assert moved_to(Fetched("https://x.be/nl/pand/12345/a", 200, "", "https://x.be/nl-be/pand/12345/a")) is None


class RedirectingFetcher:
    """Every URL answers with the house page; the old numbers redirect."""

    def __init__(self, redirects: dict[str, str]):
        self.redirects = redirects
        self.asked: list[str] = []

    def fetch(self, url: str) -> Fetched:
        self.asked.append(url)
        final = self.redirects.get(url, url)
        return Fetched(url, 200, f"<html>{final}</html>", final)

    def get(self, url: str) -> str | None:
        return self.fetch(url).text


class StubScraper:
    def __init__(self, urls: list[str]):
        self.urls = urls

    def discover(self, agency, fetcher):
        return list(self.urls)

    def parse(self, url: str, html: str, source: str) -> Listing:
        return Listing(url=url, source=source, title="Woning", city="Gent", postal_code="9000",
                       price=625000, property_type="house")


def test_scan_follows_a_redirect_to_a_known_listing(monkeypatch):
    agency = Agency(name="Irres", url="https://irres.be")
    seen = SeenStore(path=Path("unused"), entries={})
    seen.record(Listing(url=NEW, source="Irres"), today=date(2026, 11, 3))
    monkeypatch.setattr(scan_module, "scraper_for", lambda a, max_listings=80: StubScraper([BARE]))
    fetcher = RedirectingFetcher({BARE: NEW})

    result = scan_agency(agency, fetcher, seen, max_listings=80)  # type: ignore[arg-type]

    # The page is the known listing's: nothing new, the alias is seen.
    assert result.listings == []
    assert result.dead_urls == [BARE]


def test_scan_stores_a_redirected_page_under_its_own_url(monkeypatch):
    agency = Agency(name="Irres", url="https://irres.be")
    seen = SeenStore(path=Path("unused"), entries={})
    monkeypatch.setattr(scan_module, "scraper_for",
                        lambda a, max_listings=80: StubScraper([BARE, OLD, NEW]))
    fetcher = RedirectingFetcher({BARE: NEW, OLD: NEW})

    result = scan_agency(agency, fetcher, seen, max_listings=80)  # type: ignore[arg-type]

    # BARE and OLD are one identity, fetched once; its page is NEW's, stored
    # once under NEW; NEW itself is then an alias of what is already taken.
    assert [l.url for l in result.listings] == [NEW]
    assert set(result.dead_urls) == {BARE, NEW}


def test_recheck_folds_a_relisted_house_into_its_new_number():
    store = ListingStore(path=Path("unused"), listings={})
    old = Listing(url=OLD, source="Irres", price=645000, mailed=True,
                  lat=51.03, lon=3.69, geo="address")
    new = Listing(url=NEW, source="Irres", price=625000, geo="postcode", lat=51.0, lon=3.7)
    store.add(old, today=date(2026, 10, 27))
    store.add(new, today=date(2026, 11, 3))
    fetcher = RedirectingFetcher({OLD: NEW})

    changes = recheck_status([old], fetcher, date(2026, 11, 9), workers=1, store=store)  # type: ignore[arg-type]

    assert changes == Counter({"checked": 1, "available->gone": 1, "superseded": 1})
    assert list(store.listings.values()) == [new]
    assert new.first_seen == "2026-10-27"         # on the market since then
    assert new.mailed is True                     # the house was in a mail already
    assert (new.lat, new.lon, new.geo) == (51.03, 3.69, "address")


def test_recheck_marks_a_move_to_an_unknown_number_gone():
    store = ListingStore(path=Path("unused"), listings={})
    old = Listing(url=OLD, source="Irres", price=645000)
    store.add(old, today=date(2026, 10, 27))
    fetcher = RedirectingFetcher({OLD: NEW})

    changes = recheck_status([old], fetcher, date(2026, 11, 9), workers=1, store=store)  # type: ignore[arg-type]

    assert changes == Counter({"checked": 1, "available->gone": 1})
    assert old.status == "gone" and old.id in store.listings


def test_store_load_folds_two_spellings_of_one_page(tmp_path):
    import json
    path = tmp_path / "listings.json"
    rows = [Listing(url=OLD, source="Irres", first_seen="2026-10-27", mailed=True).to_dict(),
            Listing(url=BARE, source="Irres", first_seen="2026-11-04", status="sold",
                    checked="2026-11-05").to_dict()]
    path.write_text(json.dumps({"version": 1, "listings": rows}), encoding="utf-8")
    kept = list(ListingStore.load(path).listings.values())
    assert len(kept) == 1
    assert kept[0].first_seen == "2026-10-27" and kept[0].mailed
    assert kept[0].status == "sold"
