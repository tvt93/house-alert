from datetime import date, timedelta

from house_alert.config import Criteria, Range
from house_alert.filters import apply, evaluate
from house_alert.models import Listing, listing_id
from house_alert.store import SeenStore


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
