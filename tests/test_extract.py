from house_alert.extract import (
    _num,
    extract_listing,
    address_title,
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


def test_garage_sold_on_its_own_is_parking():
    for title in ("Inpandige autostaanplaats nabij station Gent-Sint-Pieters",
                  "Gunstig gelegen afgesloten garagebox in centrum Bredene-Bad",
                  "Topgelegen grote dubbele garagebox met sectionaalpoort",
                  "Afgesloten garagebox in ondergronds garagecomplex met inrit via Dienst",
                  "Staanplaats te koop", "Carport", "Parking te koop in Gent"):
        assert guess_property_type(title, "") == "parking", title
    assert guess_property_type("", "https://x.be/te-koop/garage-gent-centrum-12") == "parking"
    # The description never makes a parking space: it describes what comes with the house.
    assert guess_property_type("Te koop", "", "Woning met ruime garage") == "house"


def test_garage_that_comes_with_a_home_is_not_parking():
    for title in ("Woning met 3 slpks, garage, tuin en zonnepanelen",
                  "Instapklare duplex met 3 slpks, garage en terras",
                  "Charmante woning met 3 slpkrs, tuin en 4 staanplaatsen",
                  "Ruime woning met GARAGEBOX EN UITWEG!",
                  "Loodsencomplex met parking, op een centrale ligging",
                  "Nieuwbouw met 2 slaapkamers en carport",
                  "Rustig gelegen bungalow met ruime tuin, garage en oprit",
                  "Vrijstaande villa op 733m² met 4 slprs en losstaande garage",
                  "Prachtig penthouse incl. dubbele garage",
                  "Nieuwbouw met 3 slpks, garage, tuin",
                  "Centraal gelegen loft met 3 slaapkamers (meer mogelijk), garage voor 4 wagens",
                  "Maison avec 3 chambres et double garage à Roulers | Coventa",
                  "Spacieuse maison familiale avec 4 chambres, garage et jardin à Roeselare"):
        assert guess_property_type(title, "") != "parking", title
    assert guess_property_type(
        "", "https://x.be/te-koop/instapklare-woning-met-dubbele-garage-te-koop-in-kuurne") == "house"


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


ENTREE_SLOGAN = """<html><head><meta property="og:title" content="{title}"><title>{title} | Entree</title></head>
<body><h1>Dendermondesteenweg 545, 9070 DESTELBERGEN</h1>
<h2>Interesse<br> in dit pand?</h2>
<h2>op te frissen handelswoning met gekoppelde woonfunctie op TOP-locatie in Destelbergen centrum</h2>
<p>€ 215.000</p><p>3 slaapkamers</p>
<h2>Niet gevonden waar je naar op zoek bent?</h2>
<footer>Aanbod <a href="/te-koop">Te koop</a></footer></body></html>"""


def test_site_slogan_title_is_replaced_by_the_property_heading():
    for slogan in ("Maak uw entree", "Array"):
        listing = extract_listing("https://entree.be/pand/dendermondesteenweg-545-9070-destelbergen/134741",
                                  ENTREE_SLOGAN.format(title=slogan), source="Entree")
        assert listing is not None
        assert listing.title.startswith("op te frissen handelswoning")
        assert listing.property_type != "unknown"


def test_poetic_title_is_not_replaced_by_a_section_heading():
    # A real title without a property word stays, even when a "similar
    # properties" heading further down has one.
    html = ("<html><head><meta property=\"og:title\" content=\"Charmant pareltje in hartje Gent\"></head>"
            "<body><h1>Charmant pareltje in hartje Gent</h1><p>€ 325.000</p><p>2 slaapkamers</p>"
            "<h2>Gelijkaardige woningen in Gent</h2><h2>Andere woningen te koop</h2></body></html>")
    listing = extract_listing("https://x.be/pand/1", html, source="X")
    assert listing is not None
    assert listing.title == "Charmant pareltje in hartje Gent"


def test_slogan_title_falls_back_to_the_heading_above_the_price():
    # entree.be "wonen in werelderfgoed Begijnhof": no property word in any
    # heading, but the price sits right under the real title.
    html = ("<html><head><meta property=\"og:title\" content=\"Maak uw entree\"></head>"
            "<body><h1>Lange Violettestraat 201, 9000 GENT</h1><h2>Interesse<br> in dit pand?</h2>"
            "<h2>wonen in werelderfgoed Begijnhof</h2><p>€ 710.000</p><p>120 m²</p><p>3 slaapkamers</p>"
            "</body></html>")
    listing = extract_listing("https://entree.be/pand/lange-violettestraat-201-9000-gent/50937", html, source="Entree")
    assert listing is not None
    assert listing.title == "wonen in werelderfgoed Begijnhof"
    assert listing.price == 710000


def test_only_site_names_and_array_are_generic_titles():
    from house_alert.extract import generic_title
    assert generic_title("Maak uw entree", "Entree", "https://entree.be/pand/x/1")
    assert generic_title("Maak uw entree", "", "https://entree.be/pand/x/1")
    assert generic_title("CdF - Vastgoedmakelaars", "", "https://www.cdf.be/te-koop/x/")
    assert generic_title("Array", "", "https://entree.be/pand/x/1")
    # The agency chose these; they stay even without a property word.
    for title in ("Veelzijdig industriegebouw op toplocatie in Ronse.",
                  "Prachtig historische eigendom",
                  "Residentie Flandrien – Wonen aan de voet van de Kluisberg",
                  "Grond  te koop in Ingelmunster"):
        assert not generic_title(title, "Immo X", "https://www.immo-x.be/aanbod/1"), title


def test_call_to_action_headings_never_become_the_title():
    html = ("<html><head><meta property=\"og:title\" content=\"Maak uw entree\"></head>"
            "<body><h1>Kerkstraat 5, 9000 GENT</h1>"
            "<h2>Heb je een vraag? Of wil je jouw woning verkopen?</h2>"
            "<h2>Beschrijf je droomhuis</h2><h3>deel deze woning</h3>"
            "<h2>Wilt u ook uw kantoor verkopen?</h2><h2>Uw ideale woning stond er niet tussen?</h2>"
            "<p>3 slaapkamers</p></body></html>")
    listing = extract_listing("https://entree.be/pand/kerkstraat-5-9000-gent/1", html, source="Entree")
    assert listing is not None
    assert listing.title == "Maak uw entree"


def test_compound_garage_word_is_parking_but_a_garage_door_is_not():
    assert guess_property_type("Privégarage te koop in Wevelgem", "") == "parking"
    assert guess_property_type("Dubbelgarage in centrum Kortrijk", "") == "parking"
    assert guess_property_type("Woning met privégarage", "") == "house"
    assert guess_property_type("Nieuwe garagepoort te koop", "") == "unknown"


MVASTGOED = """<html><head><title>Te koop: Afgesloten garage te koop in de Hoveniersstraat te Kortrijk !  - Kortrijk 8500</title>
<script type="application/ld+json">{"@context":"http://schema.org","@type":"Residence",
"name":"Kortrijk Hoveniersstraat 40","description":"Bent u op zoek naar een veilige garage in Kortrijk?",
"geo":{"@type":"GeoCoordinates","latitude":"50.82286","longitude":"3.26555",
"address":{"@type":"PostalAddress","streetAddress":"Hoveniersstraat 40","postalCode":"8500","addressRegion":"Kortrijk","addressCountry":"BE"}}}</script>
</head><body><header><nav><a href="/">Home</a></nav></header>
<h1 class="wrap__heading"><span>Afgesloten garage te koop in de Hoveniersstraat te Kortrijk ! </span></h1>
<h2 class="detail__price">€ 38 500</h2>
<h2 class="wrap__heading">Over deze woning</h2>
<p>Bent u op zoek naar een veilige garage in Kortrijk?</p>
<footer>Luxemburgstraat 16B, 9140 Temse</footer></body></html>"""


def test_address_only_title_yields_to_the_h1():
    # Skarabee structured data names the listing by its address and the page
    # has no og:title; only the h1 says a garage is for sale.
    listing = extract_listing("https://www.mvastgoed.be/nl/kortrijk-hoveniersstraat-40/7674049",
                              MVASTGOED, source="M Vastgoed")
    assert listing is not None
    assert listing.title.startswith("Afgesloten garage te koop")
    assert listing.property_type == "parking"
    assert listing.street == "Hoveniersstraat 40"


def test_address_only_title_stays_when_the_h1_names_nothing():
    html = MVASTGOED.replace("Afgesloten garage te koop in de Hoveniersstraat te Kortrijk ! ",
                             "Kortrijk Hoveniersstraat 40")
    listing = extract_listing("https://www.mvastgoed.be/nl/kortrijk-hoveniersstraat-40/7674049",
                              html, source="M Vastgoed")
    assert listing is not None
    assert listing.title == "Kortrijk Hoveniersstraat 40"


def test_address_title_detector():
    assert address_title("Kortrijk Hoveniersstraat 40")
    assert address_title("Harelbeke Overleiestraat 75/A 12")
    assert address_title("Kortrijk Koning Leopold III-laan 6 1")
    assert not address_title("Charmant pareltje in hartje Gent")
    assert not address_title("Te koop Kerkstraat 28 Destelbergen")
    assert not address_title("Woning Kerkstraat 28")
