from house_alert.extract import (
    _num,
    extract_listing,
    guess_property_type,
    looks_like_listing_url,
)


def test_num_handles_belgian_formatting():
    assert _num("350.000") == 350000
    assert _num("€ 1.250.000") == 1250000
    assert _num("123,5") == 123.5
    assert _num("") is None
    assert _num("op aanvraag") is None


def test_price_followed_by_postcode_is_not_swallowed():
    # "€ 295.000 9000 Gent": the postcode must not become a thousands group.
    html = "<html><body><h1>Woning</h1><p>Vraagprijs: € 295.000 9000 Gent</p></body></html>"
    listing = extract_listing("https://x.be/te-koop/1", html, source="X")
    assert listing is not None
    assert listing.price == 295000
    assert listing.postal_code == "9000"


def test_listing_url_detection():
    assert looks_like_listing_url("https://x.be/te-koop/woning-gent-123")
    assert looks_like_listing_url("https://x.be/nl/pand/456")
    assert not looks_like_listing_url("https://x.be/te-huur/appartement-1")
    assert not looks_like_listing_url("https://x.be/contact")
    assert not looks_like_listing_url("https://x.be/")


def test_property_type_from_title_and_url():
    assert guess_property_type("Charmante rijwoning", "") == "house"
    assert guess_property_type("Ruim appartement met terras", "") == "apartment"
    assert guess_property_type("", "https://x.be/te-koop/bouwgrond-1") == "land"
    assert guess_property_type("Handelspand centrum", "") == "commercial"
    # Apartment wins over the generic "woning" when both appear.
    assert guess_property_type("Assistentiewoning appartement", "") == "apartment"
    assert guess_property_type("Iets onbekends", "") == "unknown"


HTML_HEURISTIC = """
<html><head>
  <meta property="og:title" content="Charmante rijwoning met tuin">
  <meta property="og:image" content="/media/foto.jpg">
</head><body>
  <h1>Charmante rijwoning met tuin</h1>
  <p>9000 Gent</p>
  <ul>
    <li>Vraagprijs: € 395.000</li>
    <li>3 slaapkamers</li>
    <li>Bewoonbare oppervlakte: 168 m²</li>
    <li>Grondoppervlakte: 240 m²</li>
    <li>EPC: 248 kWh/m²</li>
  </ul>
</body></html>
"""


def test_extract_from_dutch_text():
    listing = extract_listing("https://x.be/te-koop/1", HTML_HEURISTIC, source="X")
    assert listing is not None
    assert listing.price == 395000
    assert listing.bedrooms == 3
    assert listing.living_area_m2 == 168
    assert listing.plot_area_m2 == 240
    assert listing.epc_kwh == 248
    assert listing.postal_code == "9000"
    assert listing.city == "Gent"
    assert listing.property_type == "house"
    assert listing.image_url == "https://x.be/media/foto.jpg"


HTML_JSONLD = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"SingleFamilyResidence",
 "name":"Villa met zwembad","numberOfBedrooms":4,
 "floorSize":{"@type":"QuantitativeValue","value":232},
 "address":{"@type":"PostalAddress","postalCode":"8500","addressLocality":"Kortrijk"},
 "offers":{"@type":"Offer","price":"675000","priceCurrency":"EUR"}}
</script>
</head><body><h1>Villa met zwembad</h1></body></html>
"""


def test_extract_prefers_jsonld():
    listing = extract_listing("https://x.be/te-koop/2", HTML_JSONLD, source="X")
    assert listing is not None
    assert listing.price == 675000
    assert listing.bedrooms == 4
    assert listing.living_area_m2 == 232
    assert listing.city == "Kortrijk"
    assert listing.postal_code == "8500"


def test_sold_listing_is_dropped():
    html = HTML_HEURISTIC.replace("<h1>", "<h1>VERKOCHT - ")
    assert extract_listing("https://x.be/te-koop/3", html, source="X") is None


def test_page_without_facts_is_not_a_listing():
    html = "<html><body><h1>Over ons</h1><p>Welkom bij ons kantoor.</p></body></html>"
    assert extract_listing("https://x.be/te-koop/4", html, source="X") is None


def test_implausible_area_is_discarded():
    html = HTML_HEURISTIC.replace("168 m²", "999999 m²")
    listing = extract_listing("https://x.be/te-koop/5", html, source="X")
    assert listing is not None
    assert listing.living_area_m2 is None
