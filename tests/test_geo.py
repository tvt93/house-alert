from house_alert.geo import Geocoder, accept_hit, cache_key, postcode_centroid
from house_alert.models import Listing


def test_postcode_centroids_come_from_the_bundled_table():
    lat, lon = postcode_centroid("9000")
    assert 50.9 < lat < 51.2 and 3.6 < lon < 3.8
    lat, lon = postcode_centroid("8500")
    assert 50.7 < lat < 50.9 and 3.2 < lon < 3.4
    assert postcode_centroid("0000") is None
    assert postcode_centroid("") is None


def test_accept_hit_rejects_towns_and_foreign_postcodes():
    house = [{"lat": "51.05", "lon": "3.72", "class": "building", "addresstype": "house",
              "address": {"postcode": "9000"}}]
    assert accept_hit(house, "9000") == (51.05, 3.72)
    assert accept_hit(house, "9030") is None

    town = [{"lat": "51.05", "lon": "3.72", "class": "boundary", "addresstype": "city",
             "address": {"postcode": "9000"}}]
    assert accept_hit(town, "9000") is None
    assert accept_hit([], "9000") is None
    assert accept_hit({"error": "x"}, "9000") is None

    # A street without its own postcode is still accepted: mid-street beats centroid.
    street = [{"lat": "51.05", "lon": "3.72", "class": "highway", "addresstype": "road",
               "address": {}}]
    assert accept_hit(street, "9000") == (51.05, 3.72)


def test_city_name_is_the_last_resort(tmp_path):
    geocoder = Geocoder(cache_path=tmp_path / "geo.json", online=False)
    listing = Listing(url="https://x.be/1", source="X", city="Roeselare")
    assert geocoder.locate(listing)
    assert listing.geo == "city"
    assert 50.8 < listing.lat < 51.0 and listing.postal_code == ""
    nowhere = Listing(url="https://x.be/2", source="X", city="Atlantis")
    assert not geocoder.locate(nowhere)
    assert nowhere.lat is None and nowhere.geo == ""


def test_offline_geocoder_falls_back_to_postcode(tmp_path):
    geocoder = Geocoder(cache_path=tmp_path / "geo.json", online=False)
    listing = Listing(url="https://x.be/1", source="X", street="Kerkstraat 12",
                      postal_code="9000", city="Gent")
    assert geocoder.locate(listing)
    assert listing.geo == "postcode"
    assert listing.lat is not None
    assert geocoder.requests_made == 0


def test_cached_address_is_used_without_network(tmp_path):
    key = cache_key("Kerkstraat 12", "9000", "Gent")
    geocoder = Geocoder(cache_path=tmp_path / "geo.json", online=False,
                        entries={key: {"lat": 51.0, "lon": 3.7}})
    listing = Listing(url="https://x.be/1", source="X", street="kerkstraat  12",
                      postal_code="9000", city="GENT")
    assert geocoder.locate(listing)
    assert (listing.lat, listing.lon, listing.geo) == (51.0, 3.7, "address")

    # A cached miss means: postcode, and no retry.
    geocoder.entries[key] = None
    other = Listing(url="https://x.be/2", source="X", street="Kerkstraat 12",
                    postal_code="9000", city="Gent")
    geocoder.locate(other)
    assert other.geo == "postcode"


def test_cache_roundtrip(tmp_path):
    geocoder = Geocoder(cache_path=tmp_path / "geo.json")
    geocoder.entries["a|b|c"] = {"lat": 1.0, "lon": 2.0}
    geocoder.save()
    assert Geocoder.load(tmp_path / "geo.json").entries == {"a|b|c": {"lat": 1.0, "lon": 2.0}}
