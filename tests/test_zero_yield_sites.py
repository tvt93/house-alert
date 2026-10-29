"""Regression tests for the sites that contributed nothing in October 2026.

Each case uses the URL or markup shape that actually caused the miss.
"""

from house_alert.extract import extract_listing, looks_like_listing_url
from house_alert.models import Agency, Listing, listing_id
from house_alert.platforms import scraper_for
from house_alert.scan import in_region, in_region_url


# ------------------------------------------------------------- URL shapes

def test_axellenaerts_sells_under_kopen_and_lists_documents_under_assets():
    assert looks_like_listing_url(
        "https://www.axellenaerts.be/nl/kopen/28746-charmante-halfopen-bebouwing-vol-karakter")
    assert not looks_like_listing_url(
        "https://www.axellenaerts.be/assets/uploads/properties/28909/E2639286303705912668")


def test_oximo_and_cannoodt_url_shapes():
    assert looks_like_listing_url(
        "https://www.oximo.be/nl/immo-te-koop/huis/destelbergen/42166162")
    assert looks_like_listing_url("https://www.cannoodt.be/huis/gent/wespstraat-5-0")


def test_foreign_language_mirrors_are_skipped():
    assert not looks_like_listing_url(
        "https://www.dewaele.com/en/for-sale/8800-roeselare/huis/office-of-approx-307m2")
    assert looks_like_listing_url(
        "https://www.dewaele.com/nl/te-koop/8800-roeselare/huis/kantoor-te-koop-van-ca-307-m2")


def test_omnicasa_identity_lives_in_the_query_string():
    scraper = scraper_for(Agency(name="LD", url="https://www.ld-vastgoed.be", platform="omnicasa"))
    detail = "https://www.ld-vastgoed.be/nl/te-koop?view=detail&amp;id=795".replace("&amp;", "&")
    assert scraper.is_listing_url(detail)
    assert not scraper.is_listing_url("https://www.ld-vastgoed.be/nl/te-koop")
    assert scraper.canonical(detail) == "https://www.ld-vastgoed.be/nl/te-koop?view=detail&id=795"
    ids = {listing_id(scraper.canonical(detail)),
           listing_id("https://www.ld-vastgoed.be/nl/te-koop?view=detail&id=786"),
           listing_id("https://www.ld-vastgoed.be/nl/te-koop")}
    assert len(ids) == 3
    # The Joomla component route serves the same property; the path is not identity.
    assert listing_id("https://www.ld-vastgoed.be/nl/component/properties/?view=detail&id=795") \
        == listing_id(scraper.canonical(detail))
    # Without view=detail a numeric id still belongs to its path.
    assert listing_id("https://x.be/a?id=1") != listing_id("https://x.be/b?id=1")
    # Tracking parameters still don't change the identity.
    assert listing_id("https://x.be/te-koop/woning-1?utm_source=mail") == listing_id("https://x.be/te-koop/woning-1")


def test_skarabee_detail_pages_end_in_the_property_number():
    scraper = scraper_for(Agency(name="M", url="https://www.mvastgoed.be", platform="skarabee"))
    assert scraper.is_listing_url("https://www.mvastgoed.be/nl/kortrijk-moeskroensesteenweg-14201/7542404")
    assert not scraper.is_listing_url("https://www.mvastgoed.be/nl/m-services/m-renovatie/118012")
    assert not scraper.is_listing_url("https://www.mvastgoed.be/nl/te-koop")


# ------------------------------------------------------------- extraction

OMNICASA = """
<html><head><meta property="og:title" content="Appartement te koop te WEVELGEM (8560)">
<title>Appartement te koop te WEVELGEM (8560) | Glorieux Vastgoed</title></head>
<body><h2>Huis te WEVELGEM (8560)</h2><div>€ 165.000</div>
<p>Omschrijving Rocket Sale: minimum bod van 165.000</p>
<ul><li>Aantal slaapkamers : 2</li><li>Adres : 8560 WEVELGEM</li>
<li>Bewoonbare opp. : 251 m²</li><li>Bouwjaar : 1930</li></ul>
<p>Foto's en tekst copyright Glorieux</p></body></html>
"""


def test_omnicasa_title_gives_city_and_postcode_not_the_construction_year():
    listing = extract_listing("https://www.vastgoedglorieux.be/nl/te-koop?view=detail&id=265",
                              OMNICASA, source="Glorieux")
    assert listing is not None
    assert (listing.city, listing.postal_code) == ("Wevelgem", "8560")
    assert listing.price == 165000


def test_a_construction_year_before_a_capitalised_word_is_not_a_postcode():
    html = """<html><head><title>Instapklare rijwoning</title></head><body>
    <div>€ 349 000</div><p>Type Opbrengsteigendom Bouwjaar 1930 Opp. bewoonbaar 132 m²</p>
    <p>4 slaapkamers</p></body></html>"""
    listing = extract_listing("https://www.oranjeberg.be/te-koop/rijwoning/4486383", html, source="O")
    assert listing is not None
    assert listing.postal_code == ""
    assert listing.city == ""


def test_richtprijs_is_a_labelled_price_despite_a_cost_breakdown():
    html = """<html><head><title>Charmante halfopen bebouwing</title></head><body>
    <h1>Charmante halfopen bebouwing vol karakter</h1>
    <p>Filips Benthuysstraat 3, 9040 SINT-AMANDSBERG</p>
    <p>BELANGRIJK: Richtprijs: € 545.000</p>
    <table><tr><td>Registratierechten</td><td>€ 65.400</td></tr>
    <tr><td>Notariskosten</td><td>€ 3.167</td></tr>
    <tr><td>Totaal</td><td>€ 613.567</td></tr></table>
    <p>3 slaapkamers</p></body></html>"""
    listing = extract_listing("https://www.axellenaerts.be/nl/kopen/28746-charmante", html, source="AL")
    assert listing is not None
    assert listing.price == 545000


ROSSEEL = """
<html><head><meta property="og:title" content="Te koop in Zwijnaarde: Te renoveren villa">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
 "name":"Te renoveren villa in Zwijnaarde",
 "offers":{"@type":"Offer","price":"330000","priceCurrency":"EUR"}}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"ItemList",
 "itemListElement":[{"@type":"ListItem","position":1,"item":{"@type":"House","name":"Other",
 "offers":{"@type":"Offer","price":"999000"}}}]}</script></head>
<body><h1>Te renoveren villa in Zwijnaarde</h1>
<p>Details Kelderijstraat 1, 9052 Zwijnaarde</p><p>€ 330.000</p><p>Opp. bewoonbaar 189 m²</p>
<h2>Soortgelijk aanbod</h2>
<div class="estate_result" data-price="265000"><a href="/te-koop/oosterzele/huizen/x">Woning</a>
 <p class="price">€ 265.000</p></div>
<div class="estate_result" data-price="298000"><a href="/te-koop/gent/huizen/y">Woning</a>
 <p class="price">€ 298.000</p></div>
<div class="estate_result" data-price="395000"><a href="/te-koop/gent/huizen/z">Woning</a>
 <p class="price">€ 395.000</p></div>
</body></html>
"""


def test_similar_listing_cards_do_not_make_a_detail_page_an_index():
    listing = extract_listing("https://rosseel.be/te-koop/zwijnaarde/huizen/villa", ROSSEEL, source="R")
    assert listing is not None
    assert listing.price == 330000          # the Product, not an ItemList member
    assert listing.living_area_m2 == 189


MVASTGOED = """
<html><head><title>Te koop: Lichtrijk appartement - Kortrijk 8500</title>
<script type="application/ld+json">{"@context":"http://schema.org","@type":"Apartment",
 "name":"Kortrijk Burgemeester Vercruysselaan 18 21",
 "geo":{"@type":"GeoCoordinates","latitude":"50.8359","longitude":"3.2653",
  "address":{"@type":"PostalAddress","streetAddress":"Burgemeester Vercruysselaan 18",
   "addressLocality":"18 Burgemeester Vercruysselaan","postalCode":"8500","addressRegion":"Kortrijk"}}}
</script></head><body><h1>Lichtrijk 2-slaapkamer appartement</h1><p>€ 155 000</p>
<p>Bewoonbare opp.: 80 m²</p></body></html>
"""


def test_skarabee_address_hangs_off_geo_with_the_street_in_locality():
    listing = extract_listing("https://www.mvastgoed.be/nl/kortrijk-burgemeester-vercruysselaan-18-21/7690879",
                              MVASTGOED, source="M")
    assert listing is not None
    assert (listing.city, listing.postal_code) == ("Kortrijk", "8500")
    assert listing.street == "Burgemeester Vercruysselaan 18"
    assert (listing.lat, listing.lon) == (50.8359, 3.2653)


# ------------------------------------------------------------- region guard

def test_postcode_guard_for_chains():
    chain = Agency(name="Dewaele", url="https://www.dewaele.com", postcodes=["8", "9"])
    local = Agency(name="Local", url="https://x.be")
    assert in_region_url(chain, "https://www.dewaele.com/nl/te-koop/8800-roeselare/huis/x")
    assert not in_region_url(chain, "https://www.dewaele.com/nl/te-koop/3500-hasselt/huis/x")
    assert in_region_url(chain, "https://www.dewaele.com/nl/te-koop/alle/huis")   # no postcode: fetch
    assert in_region_url(local, "https://x.be/te-koop/3500-hasselt/huis")
    assert in_region(chain, Listing(url="u", source="s", postal_code="9000"))
    assert not in_region(chain, Listing(url="u", source="s", postal_code="3500"))
    assert in_region(chain, Listing(url="u", source="s"))                         # unknown passes


# ------------------------------------------------------------- sold in URL

def test_crevits_sold_houses_are_recognised_by_their_slug():
    from house_alert.extract import detect_status
    url = "https://crevits.be/nl/aanbod/2678846/villa-verkochte-panden-in-9840-de-pinte"
    assert not looks_like_listing_url(url)
    assert detect_status("<html><body>Villa, 4 slaapkamers, € 650.000</body></html>", url) == "sold"
    assert detect_status("<html><body>Villa, 4 slaapkamers, € 650.000</body></html>",
                         "https://crevits.be/nl/aanbod/2678846/villa-in-9840-de-pinte") == "available"


def test_fused_municipality_is_not_cut_at_the_hyphen():
    html = """<html><head><title>Woning met zonnige tuin</title></head><body>
    <p>€ 625.000</p><p>Steenweg 12, 9840 Nazareth-De Pinte</p><p>3 slaapkamers</p></body></html>"""
    listing = extract_listing("https://irres.be/pand/9089127/woning-met-zonnige-tuin", html, source="Irres")
    assert listing is not None
    assert listing.city == "Nazareth-De Pinte"


def test_recent_verkocht_menu_item_is_not_a_sold_banner():
    from house_alert.extract import detect_status
    html = """<html><head><title>Immo Da Vinci | Huis | 7917339</title></head><body>
    <nav><a>Recent verkocht</a><a>Buitenland</a></nav>
    <h1>Huis te koop in het centrum van Sint-Denijs-Westrem</h1>
    <p>Referentie 7917339 Prijs € 362.500 Locatie Sint-Dionysiusstraat 54, 9051 Gent</p>
    <div><a href="/x">€ 288.000</a> <a href="/y">€ 405.000</a> <a href="/z">€ 445.000</a></div>
    </body></html>"""
    assert detect_status(html, "https://www.immodavinci.be/residentieel/detail/7917339/huis") == "available"
    listing = extract_listing("https://www.immodavinci.be/residentieel/detail/7917339/huis", html, source="DV")
    assert listing is not None and listing.price == 362500
