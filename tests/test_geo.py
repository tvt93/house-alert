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

    # A road is never the house: it is the street-level fallback's job, and
    # there it must carry the requested name. jsonv2 says "category", v1 "class".
    from house_alert.geo import accept_street_hit
    street = [{"lat": "51.05", "lon": "3.72", "category": "highway", "addresstype": "road",
               "address": {"road": "Kerkstraat", "postcode": "9000"}}]
    assert accept_hit(street, "9000") is None
    assert accept_street_hit(street, "Kerkstraat", "9000", "Gent") == (51.05, 3.72)
    assert accept_street_hit(street, "Kerklaan", "9000", "Gent") is None


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


def test_accept_hit_takes_neighbouring_postcode_when_the_town_matches():
    # Krekelstraat 29, 9840 Zevergem: OSM tags the building 9052 (Zwijnaarde)
    # but its address names Zevergem and the merged municipality.
    hit = [{"lat": "51.0", "lon": "3.7", "addresstype": "building", "class": "building",
            "address": {"house_number": "29", "road": "Krekelstraat", "suburb": "Zwijnaarde",
                        "village": "Zevergem", "town": "Nazareth-De Pinte", "postcode": "9052"}}]
    assert accept_hit(hit, "9840", "De Pinte Zevergem") == (51.0, 3.7)
    assert accept_hit(hit, "9840") == (51.0, 3.7)          # the table knows Zevergem
    # A different town with the same street name stays rejected.
    other = [{"lat": "51.0", "lon": "3.7", "addresstype": "building", "class": "building",
              "address": {"road": "Kerkstraat", "town": "Sint-Martens-Latem", "postcode": "9830"}}]
    assert accept_hit(other, "9840", "De Pinte") is None
    # "Sint-Denijs-Westrem" is one place, not Westrem (9230).
    sdw = [{"lat": "51.0", "lon": "3.7", "addresstype": "building", "class": "building",
            "address": {"road": "Kerkstraat", "suburb": "Sint-Denijs-Westrem", "postcode": "9051"}}]
    assert accept_hit(sdw, "9230", "Westrem") is None


def test_locate_looks_up_street_with_city_when_postcode_is_unknown(tmp_path):
    from house_alert.models import Listing
    calls = []

    class Fake(Geocoder):
        def _wallonia(self, street, postcode, city, street_only=False):
            return None

        def _flanders(self, street, postcode, city, street_only=False):
            return None

        def _nominatim(self, street, postcode, city, street_only=False):
            calls.append((street, postcode, city))
            return (51.0, 3.7)

    g = Fake(cache_path=tmp_path / "c.json")
    l = Listing(url="https://x.be/1", source="X", street="Ledelaan 26", city="Sleidinge")
    assert g.locate(l) is True
    assert (l.lat, l.lon, l.geo) == (51.0, 3.7, "address")
    assert calls == [("Ledelaan 26", "", "Sleidinge")]
    # Without a postcode the hit must name the town.
    hit = [{"lat": "51.0", "lon": "3.7", "addresstype": "building", "class": "building",
            "address": {"road": "Ledelaan", "village": "Sleidinge", "postcode": "9940"}}]
    assert accept_hit(hit, "", "Sleidinge") == (51.0, 3.7)
    assert accept_hit(hit, "", "Evergem") is None


def test_street_fallback_when_the_house_number_is_not_in_osm(tmp_path):
    # Kerkstraat 28, 9070 Destelbergen: OSM knows the street, not the number.
    from house_alert.geo import accept_street_hit, street_name
    from house_alert.models import Listing

    house_elsewhere = [{"lat": "51.03", "lon": "3.76", "addresstype": "building", "class": "building",
                        "address": {"house_number": "28", "road": "Kerkstraat", "town": "Gentbrugge", "postcode": "9050"}}]
    road = [{"lat": "51.0578", "lon": "3.7968", "addresstype": "residential", "class": "highway",
             "address": {"road": "Kerkstraat", "town": "Destelbergen", "postcode": "9070"}}]
    calls = []

    class Fake(Geocoder):
        def _wallonia(self, street, postcode, city, street_only=False):
            return None

        def _flanders(self, street, postcode, city, street_only=False):
            return None                       # outside Flanders' register

        def _nominatim(self, street, postcode, city, street_only=False):
            calls.append((street, street_only))
            return (accept_street_hit(road, street, postcode, city) if street_only
                    else accept_hit(house_elsewhere, postcode, city))

    g = Fake(cache_path=tmp_path / "c.json")
    l = Listing(url="https://x.be/1", source="X", street="Kerkstraat 28", postal_code="9070", city="Destelbergen")
    assert g.locate(l) is True
    assert (l.lat, l.lon, l.geo) == (51.0578, 3.7968, "street")
    assert calls == [("Kerkstraat 28", False), ("Kerkstraat", True)]
    assert g.entries[cache_key("Kerkstraat 28", "9070", "Destelbergen")] == {"lat": 51.0578, "lon": 3.7968, "level": "street"}
    # Next run: served from the cache, nothing asked, nothing changed.
    assert g.locate(l) is False
    assert len(calls) == 2

    # A miss cached before the fallback existed gets exactly one street lookup.
    g.entries[cache_key("Dorpstraat 5", "9070", "Destelbergen")] = None
    l2 = Listing(url="https://x.be/2", source="X", street="Dorpstraat 5", postal_code="9070", city="Destelbergen")
    road[0]["address"]["road"] = "Dorpstraat"
    assert g.locate(l2) is True and l2.geo == "street"
    assert calls[-1] == ("Dorpstraat", True)
    # Definitive miss: stored as such, never asked again.
    road[0]["address"]["road"] = "Elsewherestraat"
    l3 = Listing(url="https://x.be/3", source="X", street="Nergensstraat 1", postal_code="9070", city="Destelbergen")
    assert g.locate(l3) is True and l3.geo == "postcode"
    assert g.entries[cache_key("Nergensstraat 1", "9070", "Destelbergen")] == {"level": "none"}
    n = len(calls)
    g.locate(l3)
    assert len(calls) == n

    assert street_name("Kerkstraat 28 bus 3") == "Kerkstraat"
    assert street_name("Steenweg op Deinze 140") == "Steenweg op Deinze"
    # The road must carry the requested name and sit in the right place.
    assert accept_street_hit(road, "Kerkstraat", "9070", "Destelbergen") is None
    road[0]["address"]["road"] = "Kerkstraat"
    assert accept_street_hit(road, "Kerkstraat", "9000", "Gent") is None
    assert accept_street_hit(road, "Kerkstraat", "", "Destelbergen") == (51.0578, 3.7968)


def test_flemish_register_is_asked_first_and_must_name_the_house(tmp_path):
    from house_alert.geo import accept_flanders
    from house_alert.models import Listing

    def result(address, kind, zipcode, number, lat=51.05785, lon=3.79876, street="Kerkstraat"):
        return {"FormattedAddress": address, "LocationType": kind, "Zipcode": zipcode,
                "Housenumber": number, "Thoroughfarename": street, "Municipality": "Destelbergen",
                "Location": {"Lat_WGS84": lat, "Lon_WGS84": lon}}

    house = [result("Kerkstraat 28, 9070 Destelbergen", "basisregisters_huisnummer_aangeduidDoorBeheerder", "9070", "28")]
    # "Kortrijksesteenweg 12" also returns 120: the number must match exactly.
    wrong_number = [result("Kortrijksesteenweg 120, 9000 Gent", "basisregisters_huisnummer_aangeduidDoorBeheerder", "9000", "120")]
    road = [result("Kerkstraat, Destelbergen", "basisregisters_straat", "9070", None)]
    assert accept_flanders(house, "Kerkstraat 28", "9070", "Destelbergen", False) == (51.05785, 3.79876)
    assert accept_flanders(house, "Kerkstraat 28 bus 2", "9070", "Destelbergen", False) == (51.05785, 3.79876)
    assert accept_flanders(house, "Kerkstraat 28", "9000", "Gent", False) is None
    assert accept_flanders(house, "Kerkstraat 28", "", "Destelbergen", False) == (51.05785, 3.79876)
    assert accept_flanders(house, "Kerkstraat 28", "", "Gent", False) is None
    assert accept_flanders(wrong_number, "Kortrijksesteenweg 12", "9000", "Gent", False) is None
    assert accept_flanders(road, "Kerkstraat 28", "9070", "Destelbergen", False) is None
    assert accept_flanders(road, "Kerkstraat", "9070", "Destelbergen", True) == (51.05785, 3.79876)
    assert accept_flanders(road, "Kerklaan", "9070", "Destelbergen", True) is None
    assert accept_flanders([], "Kerkstraat 28", "9070", "Destelbergen", False) is None

    calls = []

    class Fake(Geocoder):
        def _wallonia(self, street, postcode, city, street_only=False):
            return None

        def _flanders(self, street, postcode, city, street_only=False):
            calls.append(("vl", street, street_only))
            return accept_flanders(house, street, postcode, city, street_only)

        def _nominatim(self, street, postcode, city, street_only=False):
            calls.append(("osm", street, street_only))
            return None

    g = Fake(cache_path=tmp_path / "c.json")
    l = Listing(url="https://x.be/1", source="X", street="Kerkstraat 28", postal_code="9070", city="Destelbergen")
    assert g.locate(l) is True
    assert (l.lat, l.lon, l.geo) == (51.05785, 3.79876, "address")
    assert calls == [("vl", "Kerkstraat 28", False)]        # Nominatim never asked
    # Wallonia: the Walloon register (silent here) is asked instead of the
    # Flemish one; it answers nothing, Nominatim takes over, then the street.
    l2 = Listing(url="https://x.be/2", source="X", street="Rue du Trieu 8", postal_code="7750", city="Russeignies")
    g.locate(l2)
    assert [c[0] for c in calls[1:]] == ["osm", "osm"]
    assert g.entries[cache_key("Rue du Trieu 8", "7750", "Russeignies")] == {"level": "none"}


def test_walloon_register_answers_for_walloon_postcodes(tmp_path):
    """Trieu De Wazon 36, 7520 Templeuve: the Flemish register knows no
    Hainaut address and OSM has no number 36 there, but the Walloon
    register does. Written "Trieu de Wazon" there and "Trieu Wazon" in OSM:
    connector words never decide a street match."""
    from house_alert.geo import accept_wallonia, region, _street_key
    from house_alert.models import Listing

    assert region("7520") == "wallonia" and region("9070") == "flanders"
    assert region("1050") == "brussels" and region("1300") == "wallonia" and region("03750") == ""
    assert _street_key("Trieu De Wazon") == _street_key("Trieu Wazon") == _street_key("Trieu du Wazon")
    assert _street_key("Kerkstraat") != _street_key("Kerklaan")

    house = [{"score": 111, "city": {"ident": "57081", "name": "Tournai"},
              "zone": {"ident": "7520", "name": "Templeuve"},
              "street": {"ident": 7723957, "name": "Trieu de Wazon"},
              "house": {"ident": 971044, "name": "36",
                        "geometry": {"type": "Point", "coordinates": [3.27994, 50.63584]}}}]
    road = [{"score": 310, "city": {"ident": "57081", "name": "Tournai"},
             "zone": {"ident": "7520", "name": "Templeuve"},
             "street": {"ident": 7723957, "name": "Trieu de Wazon",
                        "geometry": {"type": "Point", "coordinates": [3.28101, 50.63544]}}}]
    assert accept_wallonia(house, "Trieu De Wazon 36", "7520", "Templeuve", False) == (50.63584, 3.27994)
    assert accept_wallonia(house, "Trieu De Wazon 36", "7500", "Tournai", False) is None
    assert accept_wallonia(house, "Trieu De Wazon 36", "", "Templeuve", False) == (50.63584, 3.27994)
    assert accept_wallonia(house, "Trieu De Wazon 36", "", "Tournai", False) == (50.63584, 3.27994)
    assert accept_wallonia(house, "Trieu De Wazon 36", "", "Mouscron", False) is None
    assert accept_wallonia(house, "Trieu De Wazon 38", "7520", "Templeuve", False) is None
    # The register answers a missing number with the street: never the house.
    assert accept_wallonia(road, "Trieu De Wazon 9999", "7520", "Templeuve", False) is None
    assert accept_wallonia(road, "Trieu De Wazon", "7520", "Templeuve", True) == (50.63544, 3.28101)
    assert accept_wallonia(road, "Rue Verte", "7520", "Templeuve", True) is None
    assert accept_wallonia([], "Trieu De Wazon 36", "7520", "Templeuve", False) is None

    calls = []

    class Fake(Geocoder):
        def _flanders(self, street, postcode, city, street_only=False):
            calls.append(("vl", street, street_only))
            return None

        def _wallonia(self, street, postcode, city, street_only=False):
            calls.append(("wal", street, street_only))
            return accept_wallonia(house, street, postcode, city, street_only)

        def _nominatim(self, street, postcode, city, street_only=False):
            calls.append(("osm", street, street_only))
            return None

    g = Fake(cache_path=tmp_path / "c.json")
    l = Listing(url="https://x.be/1", source="X", street="Trieu De Wazon 36", postal_code="7520", city="Templeuve")
    assert g.locate(l) is True
    assert (l.lat, l.lon, l.geo) == (50.63584, 3.27994, "address")
    # A Walloon postcode never troubles the Flemish register.
    assert calls == [("wal", "Trieu De Wazon 36", False)]


def test_osm_street_fallback_ignores_connector_words():
    from house_alert.geo import accept_street_hit
    hit = [{"lat": "50.6354", "lon": "3.2810", "category": "highway", "type": "residential",
            "address": {"road": "Trieu Wazon", "postcode": "7520", "city": "Doornik"}}]
    assert accept_street_hit(hit, "Trieu De Wazon", "7520", "Templeuve") == (50.6354, 3.281)
    assert accept_street_hit(hit, "Trieu De Wazon", "7520", "Templeuve") == (50.6354, 3.281)
    assert accept_street_hit(hit, "Rue Wazon", "7520", "Templeuve") is None


def test_lettered_number_falls_back_to_the_bare_number_in_the_register(tmp_path):
    """flame-estate.be sells a project as Grote Steenweg 105B, 105C, 105D.
    When the register does not know a letter yet, the bare number is the
    same gate; Nominatim's guess for "105B" was 500 m up the road."""
    from house_alert.geo import bare_number
    from house_alert.models import Listing

    assert bare_number("Grote Steenweg 105B") == "Grote Steenweg 105"
    assert bare_number("Kerkstraat 28A bus 3") == "Kerkstraat 28 bus 3"
    assert bare_number("Kerkstraat 28") == ""
    assert bare_number("Kerkstraat 28 bus 3") == ""

    known = {"Grote Steenweg 105": (50.98347, 3.6802), "Grote Steenweg 105B": (50.98307, 3.68018)}
    calls = []

    class Fake(Geocoder):
        def _flanders(self, street, postcode, city, street_only=False):
            calls.append(("vl", street, street_only))
            return None if street_only else known.get(street)

        def _wallonia(self, street, postcode, city, street_only=False):
            return None

        def _nominatim(self, street, postcode, city, street_only=False):
            calls.append(("osm", street, street_only))
            return (50.9867, 3.6747)          # the wrong house, never reached

    g = Fake(cache_path=tmp_path / "c.json")
    l = Listing(url="https://x.be/1", source="X", street="Grote Steenweg 105C", postal_code="9840", city="Nazareth-De Pinte")
    assert g.locate(l) is True
    assert (l.lat, l.lon, l.geo) == (50.98347, 3.6802, "address")
    assert calls == [("vl", "Grote Steenweg 105C", False), ("vl", "Grote Steenweg 105", False)]
    # A letter the register knows is taken as is.
    l2 = Listing(url="https://x.be/2", source="X", street="Grote Steenweg 105B", postal_code="9840", city="Nazareth-De Pinte")
    g.locate(l2)
    assert (l2.lat, l2.lon) == (50.98307, 3.68018)
