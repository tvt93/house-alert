"""Street extraction: the house's address, never the agency's."""

from house_alert.extract import extract_listing, find_street
from house_alert.models import Listing
from house_alert.scan import strip_shared_street


def test_find_street_shapes():
    assert find_street("Kerkstraat 12") == "Kerkstraat 12"
    assert find_street("Grote Markt 5, 9000 Gent") == "Grote Markt 5"
    assert find_street("Steenweg op Deinze 140") == "Steenweg op Deinze 140"
    assert find_street("Sint-Pietersnieuwstraat 25A") == "Sint-Pietersnieuwstraat 25A"
    assert find_street("Kortrijksesteenweg 12 bus 3") == "Kortrijksesteenweg 12 bus 3"
    assert find_street("Hof ter Linden 3") == "Hof ter Linden 3"


def test_find_street_ignores_numbers_that_are_not_house_numbers():
    assert find_street("Bouwjaar 1975 EPC 248 kWh") is None
    # A postcode after a street name is not a house number.
    assert find_street("Kerkstraat 9000 Gent") is None
    assert find_street("Woning 3 slaapkamers") is None
    assert find_street("Vraagprijs 395.000") is None


def test_find_street_trims_labels_and_city():
    assert find_street("Adres: Kerkstraat 12", city="Gent") == "Kerkstraat 12"
    assert find_street("Gent Kerkstraat 12", city="Gent") == "Kerkstraat 12"
    # The leading word is a genuine part of the name when it is not a label.
    assert find_street("Oude Kerkstraat 12", city="Gent") == "Oude Kerkstraat 12"


def test_find_street_near_place_needs_postcode_or_city_after_it():
    text = "Gelegen vlakbij Kerkstraat 12 en het park. Adres: Molenstraat 4, 9000 Gent"
    assert find_street(text, city="Gent", postcode="9000", near_place=True) == "Molenstraat 4"


JSONLD_PAGE = """
<html><head><title>Woning te koop</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"House","name":"Rijwoning",
 "address":{"@type":"PostalAddress","streetAddress":"Kerkstraat 12",
            "postalCode":"9000","addressLocality":"Gent"},
 "offers":{"@type":"Offer","price":"395000","priceCurrency":"EUR"}}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"RealEstateAgent","name":"Immo X",
 "address":{"@type":"PostalAddress","streetAddress":"Stationsstraat 5",
            "postalCode":"9000","addressLocality":"Gent"}}
</script>
</head><body><h1>Rijwoning</h1><p>3 slaapkamers</p></body></html>
"""


def test_jsonld_property_street_is_used_and_office_is_not():
    listing = extract_listing("https://x.be/te-koop/1", JSONLD_PAGE, source="X")
    assert listing is not None
    assert listing.street == "Kerkstraat 12"
    assert listing.postal_code == "9000"
    assert listing.city == "Gent"


OFFICE_IN_JSONLD_ONLY = JSONLD_PAGE.replace(
    '"streetAddress":"Kerkstraat 12",', '"streetAddress":"Stationsstraat 5",'
)


def test_property_street_equal_to_office_is_dropped():
    listing = extract_listing("https://x.be/te-koop/1", OFFICE_IN_JSONLD_ONLY, source="X")
    assert listing is not None
    assert listing.street == ""


CHROME_PAGE = """
<html><head>
  <meta property="og:title" content="Charmante rijwoning met tuin">
</head><body>
  <header><div class="topbar">Immo X · Stationsstraat 5, 9000 Gent · 09 123 45 67</div></header>
  <h1>Charmante rijwoning met tuin</h1>
  <div class="address">Kerkstraat 12, 9000 Gent</div>
  <ul><li>Vraagprijs: € 395.000</li><li>3 slaapkamers</li></ul>
  <footer>Immo X, Stationsstraat 5, 9000 Gent</footer>
</body></html>
"""


def test_address_line_in_body_beats_office_in_chrome():
    listing = extract_listing("https://x.be/te-koop/1", CHROME_PAGE, source="X")
    assert listing is not None
    assert listing.street == "Kerkstraat 12"
    assert listing.postal_code == "9000"


TITLE_PAGE = """
<html><head><meta property="og:title" content="Kerkstraat 12 - Gent"></head>
<body><h1>Kerkstraat 12 - Gent</h1><p>Vraagprijs: € 295.000</p><p>9000 Gent</p>
<footer>Immo X, Stationsstraat 5, 9000 Gent</footer></body></html>
"""


def test_street_from_title():
    listing = extract_listing("https://x.be/te-koop/1", TITLE_PAGE, source="X")
    assert listing is not None
    assert listing.street == "Kerkstraat 12"
    assert listing.location_display == "Kerkstraat 12, 9000 Gent"


HERO_IN_HEADER = """
<html><head><meta property="og:title" content="Woning te koop - Beekstraat 27, 9940 Wippelgem"></head>
<body>
  <header class="site-header"><nav>Immo X · Stationsstraat 5, 9000 Gent</nav></header>
  <main><article>
    <header class="detail-header"><h1>Woning te koop - Beekstraat 27, 9940 Wippelgem</h1></header>
    <p>Vraagprijs: € 850.000</p><p>4 slaapkamers</p>
  </article></main>
  <footer>Immo X, Stationsstraat 5, 9000 Gent</footer>
</body></html>
"""


def test_listing_hero_in_a_header_element_is_not_chrome():
    # Seen on wijsvastgoed.be: the property title sits in <header class="detail-header">.
    listing = extract_listing(
        "https://x.be/detail/te-koop-woning-wippelgem/7677007", HERO_IN_HEADER, source="X")
    assert listing is not None
    assert listing.street == "Beekstraat 27"
    assert listing.postal_code == "9940"
    assert listing.city == "Wippelgem"


def test_slug_city_drops_property_type_words():
    html = "<html><body><h1>Landhuis</h1><p>Vraagprijs: € 800.000</p></body></html>"
    listing = extract_listing(
        "https://x.be/detail/te-koop-landhuis-woning-assenede/1", html, source="X")
    assert listing is not None
    assert listing.city == "Assenede"


GEO_PAGE = """
<html><head><meta property="og:title" content="Woning te koop - Beekstraat 27, 9940 Wippelgem">
<script type="application/ld+json">
{"@type":"GeoCoordinates","latitude":"51.1465","longitude":"3.7012",
 "address":{"@type":"PostalAddress","streetAddress":"Beekstraat 27","postalCode":"9940"}}
</script>
<script type="application/ld+json">
{"@type":"RealEstateAgent","name":"Wijs",
 "geo":{"@type":"GeoCoordinates","latitude":"51.05","longitude":"3.72"},
 "address":{"@type":"PostalAddress","streetAddress":"Gaverlandstraat 3A","postalCode":"9031"}}
</script>
</head><body><h1>Woning</h1><p>Vraagprijs: € 850.000</p></body></html>
"""


def test_geocoordinates_with_the_property_address_are_not_the_office():
    # wijsvastgoed.be: a GeoCoordinates block carries the house's address and
    # position; only the RealEstateAgent block is the office.
    listing = extract_listing("https://x.be/detail/te-koop-woning-wippelgem/1", GEO_PAGE, source="X")
    assert listing is not None
    assert listing.street == "Beekstraat 27"
    assert (listing.lat, listing.lon, listing.geo) == (51.1465, 3.7012, "address")


def test_office_coordinates_are_never_used():
    html = GEO_PAGE.split('<script type="application/ld+json">', 1)[0] + \
        GEO_PAGE.split("</script>", 1)[1]
    listing = extract_listing("https://x.be/detail/te-koop-woning-wippelgem/1", html, source="X")
    assert listing is not None
    assert listing.lat is None and listing.geo == ""
    assert listing.street == "Beekstraat 27"


def test_shared_street_across_an_agency_is_the_office():
    def l(street):
        return Listing(url=f"https://x.be/{street}", source="X", street=street)

    listings = [l("Stationsstraat 5"), l("Stationsstraat 5"), l("Stationsstraat 5"),
                l("Kerkstraat 12"), l("")]
    assert strip_shared_street(listings) == 3
    assert [x.street for x in listings] == ["", "", "", "Kerkstraat 12", ""]

    # Three out of ten is a coincidence, not an office.
    many = [l("Stationsstraat 5")] * 3 + [l(f"Straat {i}") for i in range(7)]
    assert strip_shared_street(many) == 0
