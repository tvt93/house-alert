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


ENTREE_RENTAL = """<html><head><meta property="og:title" content="Charmante villa in De Pinte"></head>
<body><h1>Reevijver 17, 9840 DE PINTE</h1><h2>Charmante villa in De Pinte</h2>
<p class="text-29 font-serif">€ 1.600</p>
<dl><dt>Perceeloppervlakte</dt><dd>903 m²</dd><dt>Bewoonbare oppervlakte</dt><dd>250 m²</dd>
<dt>Beschikbaar vanaf</dt><dd>Onmiddellijk</dd></dl><p>3 slaapkamers</p>
<a href="/pand/other/1">Lochristi € 303.500 bekijk pand</a>
<footer>Aanbod <a href="/te-koop">Te koop</a> <a href="/te-huur">Te huur</a></footer>
</body></html>"""


def test_rent_sized_only_amount_is_a_rental():
    # entree.be: nothing on the page says rent except the size of the figure.
    assert extract_listing("https://entree.be/pand/reevijver-17-9840-de-pinte/8544868",
                           ENTREE_RENTAL, source="Entree") is None
    # Three-digit rents too.
    assert extract_listing("https://entree.be/pand/x/1", ENTREE_RENTAL.replace("1.600", "680"),
                           source="Entree") is None


def test_labelled_rent_is_a_rental():
    for amount in ("Huurprijs: € 950", "€ 850 / maand", "€ 1.250 per maand"):
        html = f"<html><body><h1>Rustige woning</h1><p>{amount}</p><p>3 slaapkamers</p></body></html>"
        assert extract_listing("https://x.be/pand/1", html, source="X") is None, amount


def test_cadastral_income_is_not_a_rent():
    # makelaarshuys.be renders the price in JavaScript; the only amount in
    # the HTML is the cadastral income. Still a listing, price unknown.
    html = ("<html><body><h1>Woning Mariakerke</h1><p>3 slaapkamers</p>"
            "<table><tr><td>Niet geïndexeerd K.I.:</td><td>€ 334</td></tr></table></body></html>")
    for url in ("https://www.makelaarshuys.be/detail/kopen-woning-mariakerke/7243527",
                "https://x.be/pand/1"):
        listing = extract_listing(url, html, source="X")
        assert listing is not None and listing.price is None, url


def test_monthly_charges_on_a_sale_page_are_not_a_rent():
    html = ("<html><body><h1>Appartement Gent</h1><p>2 slaapkamers</p>"
            "<p>Gemeenschappelijke kosten: € 150 per maand</p></body></html>")
    listing = extract_listing("https://x.be/pand/1", html, source="X")
    assert listing is not None and listing.price is None


def test_sale_url_keeps_a_small_amount_listing():
    html = "<html><body><h1>Garagebox</h1><p>€ 950</p><p>Oppervlakte 18 m²</p><p>1 slaapkamer</p></body></html>"
    # A sale URL is never a rental, whatever the amount says.
    assert extract_listing("https://x.be/te-koop/garage-1", html, source="X") is not None


def test_facts_after_the_price_are_not_thousands_groups():
    # entree.be: "€ 215.000" directly followed by "137 m²" and "110 m²".
    html = ("<html><body><h2>Handelswoning Destelbergen</h2><p>€ 215.000</p>"
            "<span>137 m²</span><span>110 m²</span><span>3</span><span>2</span>"
            "<p>3 slaapkamers</p></body></html>")
    listing = extract_listing("https://entree.be/pand/x-9070-destelbergen/1", html, source="Entree")
    assert listing is not None
    assert listing.price == 215000


def test_search_filter_bounds_are_not_a_rent():
    # immo-emeraude.com renders the price in JavaScript; the static HTML
    # holds the search form's price buckets. Not a rental.
    html = ("<html><body><h1>Exclusieve nieuwbouwvilla in Rekkem</h1><p>4 slaapkamers</p>"
            "<p>Land : 559 m² huisvesting : 287 m²</p>"
            "<ul><li>≤ € 500k</li><li>Van € 500 tot € 750</li><li>Van € 750 tot € 1.000</li></ul>"
            "</body></html>")
    listing = extract_listing("https://www.immo-emeraude.com/nl/eigendommen/villa-79983", html, source="X")
    assert listing is not None and listing.price is None


def test_van_in_the_title_does_not_hide_a_rent():
    # "van" is a range word only right before the amount; in a title it is a
    # preposition (entree.be "appartement aan het Van Duyseplein € 1.500").
    html = ("<html><body><h2>Hoogwaardig afgewerkt appartement aan het Van Duyseplein</h2>"
            "<p>€ 1.500</p><p>2 slaapkamers</p></body></html>")
    assert extract_listing("https://entree.be/pand/eekhout-2-9000-gent/1", html, source="Entree") is None
