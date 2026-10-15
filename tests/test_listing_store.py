import json
from datetime import date, timedelta

from house_alert.models import Listing
from house_alert.site import export_site
from house_alert.store import ListingStore


def house(n: int, **kwargs) -> Listing:
    base = dict(url=f"https://x.be/te-koop/{n}", source="X", title=f"Woning {n}",
                city="Gent", postal_code="9000", price=300000 + n, property_type="house")
    base.update(kwargs)
    return Listing(**base)


def test_store_roundtrip_keeps_first_seen_and_geo(tmp_path):
    store = ListingStore(path=tmp_path / "listings.json", listings={})
    first = house(1, street="Kerkstraat 12", lat=51.0, lon=3.7, geo="address")
    assert store.add(first, today=date(2026, 10, 1))
    assert first.first_seen == "2026-10-01"
    # Adding the same listing again does not overwrite what we already know.
    assert not store.add(house(1, price=1), today=date(2026, 10, 2))
    store.save()

    reloaded = ListingStore.load(tmp_path / "listings.json")
    got = reloaded.listings[first.id]
    assert got.first_seen == "2026-10-01"
    assert (got.lat, got.lon, got.geo) == (51.0, 3.7, "address")
    assert got.price == 300001
    assert got.street == "Kerkstraat 12"


def test_prune_and_pending_lookups(tmp_path):
    store = ListingStore(path=tmp_path / "listings.json", listings={})
    old = date.today() - timedelta(days=200)
    store.add(house(1), today=old)
    store.add(house(2, street="Kerkstraat 1"), today=date.today())
    store.add(house(3, street="Kerkstraat 2", geo="address", lat=1.0, lon=1.0))
    assert [l.title for l in store.needing_address_lookup()] == ["Woning 2"]
    assert store.prune(retention_days=180) == 1
    assert len(store) == 2


def test_export_site_writes_public_fields_newest_first(tmp_path):
    store = ListingStore(path=tmp_path / "listings.json", listings={})
    store.add(house(1, matched_keywords=["tuin"]), today=date(2026, 10, 1))
    store.add(house(2, description="x" * 500), today=date(2026, 10, 3))
    target = export_site(store, tmp_path / "docs", agency_count=87)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["count"] == 2 and data["agencies"] == 87
    assert [l["title"] for l in data["listings"]] == ["Woning 2", "Woning 1"]
    record = data["listings"][0]
    assert "matched_keywords" not in record
    assert len(record["description"]) == 300
    assert record["first_seen"] == "2026-10-03"
    assert {"lat", "lon", "geo", "street", "plot_area_m2"} <= set(record)
