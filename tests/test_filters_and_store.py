import json
from datetime import date, timedelta

from house_alert.config import Criteria, Range
from house_alert.filters import apply, evaluate
from house_alert.models import Listing, listing_id
from house_alert.store import ListingStore, SeenStore


def house(**kwargs) -> Listing:
    base = dict(
        url="https://x.be/te-koop/1", source="X", title="Rijwoning met tuin",
        city="Gent", postal_code="9000", price=395000, property_type="house",
        bedrooms=3, living_area_m2=168,
    )
    base.update(kwargs)
    return Listing(**base)


def test_price_ceiling_rejects():
    criteria = Criteria(price=Range(max=350000))
    verdict = evaluate(house(), criteria)
    assert not verdict
    assert "price" in verdict.reason


def test_unknown_value_respects_include_unknown():
    strict = Criteria(bedrooms=Range(min=3, include_unknown=False))
    lenient = Criteria(bedrooms=Range(min=3, include_unknown=True))
    assert not evaluate(house(bedrooms=None), strict)
    assert evaluate(house(bedrooms=None), lenient)


def test_type_filter_keeps_unknown_type():
    criteria = Criteria(property_types=["house"])
    assert evaluate(house(property_type="unknown", title="Iets"), criteria)
    assert not evaluate(house(property_type="apartment", title="Iets"), criteria)


def test_exclude_keyword_wins_over_everything():
    criteria = Criteria(exclude_keywords=["garagebox"])
    assert not evaluate(house(title="Garagebox te koop"), criteria)


def test_municipality_include_and_exclude():
    assert evaluate(house(), Criteria(include_municipalities=["gent"]))
    assert not evaluate(house(), Criteria(include_municipalities=["kortrijk"]))
    assert not evaluate(house(), Criteria(exclude_municipalities=["gent"]))


def test_highlight_keywords_are_recorded():
    listing = house()
    assert evaluate(listing, Criteria(highlight_keywords=["tuin", "zwembad"]))
    assert listing.matched_keywords == ["tuin"]


def test_apply_splits_matches_and_rejections():
    criteria = Criteria(price=Range(max=400000))
    matches, rejected = apply([house(), house(price=500000)], criteria)
    assert len(matches) == 1
    assert len(rejected) == 1
    assert rejected[0].reason


def test_listing_id_ignores_query_and_case():
    a = listing_id("https://x.be/te-koop/Woning-1/?utm_source=mail")
    b = listing_id("https://x.be/te-koop/woning-1")
    assert a == b


def test_listing_id_ignores_scheme_and_www():
    # immo-lvb.be links its pages as www.immo-lvb.be from the menu and as
    # immo-lvb.be from the sitemap; the villa was stored - and mapped - twice.
    a = listing_id("https://www.immo-lvb.be/nl/te-koop/kortrijk/villa/2753/")
    b = listing_id("https://immo-lvb.be/nl/te-koop/kortrijk/villa/2753")
    c = listing_id("http://IMMO-LVB.be/nl/te-koop/kortrijk/villa/2753")
    assert a == b == c
    assert listing_id("https://immo-lvb.be/nl/te-koop/kortrijk/villa/2754") != a


def test_listing_id_reads_binnenkort_te_koop_as_te_koop():
    # rosseel.be keeps /binnenkort-te-koop/<slug> online after the house went
    # on sale as /te-koop/<slug>; three houses sat on the map twice.
    soon = listing_id("https://rosseel.be/binnenkort-te-koop/gent/appartementen/woning-aan-de-ottogracht")
    sale = listing_id("https://rosseel.be/te-koop/gent/appartementen/woning-aan-de-ottogracht")
    assert soon == sale
    assert listing_id("https://rosseel.be/te-huur/gent/appartementen/woning-aan-de-ottogracht") != sale


def test_merged_listing_keeps_the_plain_url(tmp_path):
    path = tmp_path / "listings.json"
    rows = [
        house(url="https://rosseel.be/binnenkort-te-koop/gent/huizen/x", first_seen="2026-10-27"),
        house(url="https://rosseel.be/te-koop/gent/huizen/x", first_seen="2026-10-29"),
    ]
    path.write_text(json.dumps({"version": 1, "listings": [r.to_dict() for r in rows]}), encoding="utf-8")
    kept = next(iter(ListingStore.load(path).listings.values()))
    assert kept.url == "https://rosseel.be/te-koop/gent/huizen/x"
    assert kept.first_seen == "2026-10-27"


def test_listing_store_merges_rows_stored_under_two_spellings(tmp_path):
    path = tmp_path / "listings.json"
    rows = [
        house(url="https://www.immo-lvb.be/nl/te-koop/kortrijk/villa/2753/",
              first_seen="2026-10-17", checked="2026-10-27", mailed=False, geo="address", lat=50.8, lon=3.2),
        house(url="https://immo-lvb.be/nl/te-koop/kortrijk/villa/2753/",
              first_seen="2026-10-15", checked="2026-10-28", status="sold", mailed=True, geo="postcode"),
    ]
    path.write_text(json.dumps({"version": 1, "listings": [r.to_dict() for r in rows]}), encoding="utf-8")

    store = ListingStore.load(path)
    assert len(store) == 1
    kept = next(iter(store.listings.values()))
    assert kept.first_seen == "2026-10-15"           # the row a mail already showed
    assert kept.mailed is True
    assert (kept.status, kept.checked) == ("sold", "2026-10-28")   # the later re-check
    assert (kept.geo, kept.lat) == ("address", 50.8)               # the better point
    store.save()
    assert len(ListingStore.load(path)) == 1


def test_seen_store_rekeys_entries_by_url(tmp_path):
    # Keys written by an older identity rule must not make the page new again.
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"version": 1, "seen": {
        "old-key-www": {"first_seen": "2026-10-17", "url": "https://www.immo-lvb.be/nl/te-koop/kortrijk/villa/2753/", "source": "scan"},
        "old-key-bare": {"first_seen": "2026-10-15", "url": "https://immo-lvb.be/nl/te-koop/kortrijk/villa/2753/", "source": "scan"},
    }}), encoding="utf-8")

    seen = SeenStore.load(path)
    assert len(seen) == 1
    assert not seen.is_new(house(url="https://immo-lvb.be/nl/te-koop/kortrijk/villa/2753"))
    assert next(iter(seen.entries.values()))["first_seen"] == "2026-10-15"


def test_store_roundtrip_and_prune(tmp_path):
    store = SeenStore(path=tmp_path / "seen.json", entries={})
    listing = house()
    assert store.is_new(listing)

    old = date.today() - timedelta(days=200)
    store.record(listing, today=old)
    store.save()

    reloaded = SeenStore.load(tmp_path / "seen.json")
    assert not reloaded.is_new(listing)
    assert reloaded.prune(retention_days=180) == 1
    assert reloaded.is_empty
