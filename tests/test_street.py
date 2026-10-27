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


def test_find_street_skips_codes_and_shouting():
    # landbergh.be: "Afrit E40 Adres Damstraat 41"; "Kortrijk. Adres Peter Benoitlaan 33"
    assert find_street("Afrit E40 Adres Damstraat 41") == "Damstraat 41"
    assert find_street("Kortrijk. Adres Peter Benoitlaan 33, 9800 Deinze") == "Peter Benoitlaan 33"
    assert find_street("INSTAPKLARE WOONST Vlamingenstraat 2") == "Vlamingenstraat 2"
    assert find_street("Kouter 8 bus 202") == "Kouter 8 bus 202"


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


def test_form_labels_and_place_names_are_not_part_of_the_street():
    # immocasa.be prints "Ja Kantoor Ingelmunster Stationsstraat 4 bus 1".
    assert find_street("Ja Kantoor Ingelmunster Stationsstraat 4 bus 1") == "Stationsstraat 4 bus 1"
    assert find_street("Neen Kantoor Ingelmunster Stationsstraat 4") == "Stationsstraat 4"


def test_in_slug_city_must_be_a_real_place():
    html = "<html><body><h1>Woning</h1><p>Vraagprijs: € 300.000</p></body></html>"
    bad = extract_listing("https://x.be/te-koop/woning-in-een-prachtige-tuin/1", html, source="X")
    assert bad is not None and bad.city == ""
    good = extract_listing("https://x.be/te-koop/woning-in-heule/1", html, source="X")
    assert good is not None and good.city == "Heule"


def test_rental_by_title_is_not_a_listing():
    html = ('<html><head><meta property="og:title" content="Te huur - Rustig gelegen"></head>'
            '<body><h1>Te huur</h1><p>Huurprijs: € 1.250</p><p>2 slaapkamers</p></body></html>')
    assert extract_listing("https://x.be/pand/123", html, source="X") is None


def test_shared_street_across_an_agency_is_the_office():
    def l(street):
        return Listing(url=f"https://x.be/{street}", source="X", street=street)

    listings = [l("Stationsstraat 5"), l("Ja Kantoor Stationsstraat 5"), l("Stationsstraat 5"),
                l("Kerkstraat 12"), l("")]
    assert strip_shared_street(listings) == 3
    assert [x.street for x in listings] == ["", "", "", "Kerkstraat 12", ""]

    # Three out of ten in one postcode is a project with several units.
    many = [l("Stationsstraat 5")] * 3 + [l(f"Straat {i}") for i in range(7)]
    for m in many:
        m.postal_code = "9000"
    assert strip_shared_street(many) == 0

    # The same door on listings in different towns is the office (immocasa.be).
    spread = [l("Stationsstraat 4 bus 1") for _ in range(3)] + [l(f"Straat {i}") for i in range(7)]
    for m, pc in zip(spread, ("8770", "8870", "8770")):
        m.postal_code = pc
    assert strip_shared_street(spread) == 3


def test_find_street_does_not_cross_a_line_break():
    # irres.be: the title ends in a place name and the address block follows it.
    text = "Ruime gezinswoning nabij het Parkbos\nOvermeersstraat 9\n9051 Sint-Denijs-Westrem"
    assert find_street(text, postcode="9051", near_place=True) == "Overmeersstraat 9"
    # The postcode may sit on the next line; the name may not.
    assert find_street("Kerkstraat 12\n9000 Gent", postcode="9000", near_place=True) == "Kerkstraat 12"
    assert find_street("Kerkstraat\n12, 9000 Gent", postcode="9000", near_place=True) is None
    # Indentation inside a line is still just a space.
    assert find_street("Kerkstraat \t 12,   9000 Gent", postcode="9000", near_place=True) == "Kerkstraat 12"


MENU_BEFORE_ADDRESS = """
<html><head><meta property="og:title" content="Eigendom met praktijk op toplocatie"></head>
<body>
  <header><nav><button><span>Home</span></button></nav></header>
  <main>
    <div class="pretitle">
      Beukenlaan 17, 9051 Sint-Denijs-Westrem
    </div>
    <h1>Eigendom met praktijk op toplocatie</h1>
    <p>Vraagprijs: € 1.250.000</p><p>3 slaapkamers</p>
  </main>
</body></html>
"""

HEADING_BEFORE_ADDRESS = """
<html><head><meta property="og:title" content="Ruime gezinswoning nabij het Parkbos"></head>
<body>
  <h1>Ruime gezinswoning in een verkeersluwe straat nabij het Parkbos</h1>
  <div><p>Overmeersstraat 9</p><p>9051 Sint-Denijs-Westrem</p></div>
  <p>Vraagprijs: € 695.000</p><p>4 slaapkamers</p>
</body></html>
"""

TAB_BEFORE_ADDRESS = """
<html><head><meta property="og:title" content="Charmante instapklare woning met mooie tuin"></head>
<body>
  <h1>Charmante instapklare woning met mooie tuin</h1>
  <p>Vraagprijs: € 649.000</p><p>3 slaapkamers</p>
  <div class="accordion-header"><button>Details</button></div>
  <div class="accordion-body"><div class="spec"><a class="showMap">
      Wittepaalstraat 41 , 9051 Afsnee</a></div></div>
</body></html>
"""


def test_word_from_the_previous_block_is_not_part_of_the_street():
    # huysewinkel.be, irres.be and rosseel.be each print the address right
    # after a block that ends in a capitalised word; Nominatim then misses
    # "Home Beukenlaan 17" and the house is drawn at the postcode centroid.
    for page, street in (
        (MENU_BEFORE_ADDRESS, "Beukenlaan 17"),
        (HEADING_BEFORE_ADDRESS, "Overmeersstraat 9"),
        (TAB_BEFORE_ADDRESS, "Wittepaalstraat 41"),
    ):
        listing = extract_listing("https://x.be/te-koop/1", page, source="X")
        assert listing is not None
        assert listing.street == street
        assert listing.postal_code == "9051"


TESTIMONIAL_PAGE = """
<html><head><meta property="og:title" content="Brede woning met 5 slaapkamers"></head>
<body>
  <h1>Brede woning met 5 slaapkamers</h1>
  <p>Vraagprijs: € 495.000</p><p>5 slaapkamers</p>
  <div class="adres-one-line">wittemolenstraat 158 - 9040 SINT-AMANDSBERG</div>
  <section class="view-wij-verkochten-onze-woning-met-cannoodt">
    <h3>Wij verkochten onze woning met Cannoodt</h3>
    <h5>Wintertuinstraat 2 - 9000 Gent</h5>
    <p>Heel tevreden over de aanpak.</p>
  </section>
</body></html>
"""


def test_testimonial_address_is_not_the_house():
    # cannoodt.be prints a sold client's address under the listing; before
    # block-aware text it was read as "Cannoodt Wintertuinstraat 2", a street
    # Nominatim rejected, which hid the mistake. Now it must not be read at all.
    listing = extract_listing(
        "https://x.be/huis/sint-amandsberg/brede-woning", TESTIMONIAL_PAGE, source="X")
    assert listing is not None
    assert listing.street == ""
