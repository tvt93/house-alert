"""Location extraction, which is where the real-world traps live.

Every case below was taken from an actual page that fooled an earlier version.
"""

def test_postcode_in_slug_beats_office_postcode_in_body():
    # immocasa.be: the form prints the office ("8770 Ingelmunster") on every
    # page, while the URL says where the property is.
    from house_alert.extract import extract_listing, looks_like_listing_url

    page = """<html><body><h1>Appartement met 2 slaapkamers</h1>
      <p>Vraagprijs: € 250.000</p><p>Kantoor Ingelmunster Stationsstraat 4 bus 1, 8770 Ingelmunster</p>
    </body></html>"""
    listing = extract_listing(
        "https://x.be/aanbod/appartement-te-koop-in-8900-ieper", page, source="X")
    assert listing is not None
    assert (listing.postal_code, listing.city) == ("8900", "Ieper")
    assert listing.street == ""
    assert not looks_like_listing_url("https://x.be/aanbod/pand-te-huur-in-8800-roeselare")


def test_body_text_postcode_must_be_real_and_not_the_office():
    from house_alert.extract import extract_listing

    page = """<html><body>
      <header class="site-header">Immo X · Koningsstraat 1, 1000 Brussel</header>
      <h1>Woning</h1><p>Vraagprijs: € 300.000</p>
      <p>Bouwjaar 2024 Energie: EPC 120. Beschikbaar: 1250 Beschikbaar</p>
      <p>Ligging: 9940 Wippelgem</p>
      <footer>Immo X, Koningsstraat 1, 1000 Brussel</footer>
    </body></html>"""
    listing = extract_listing("https://x.be/pand/1", page, source="X")
    assert listing is not None
    assert (listing.postal_code, listing.city) == ("9940", "Wippelgem")


from house_alert.extract import extract_listing

FACTS = """
  <li>Vraagprijs: € 395.000</li>
  <li>3 slaapkamers</li>
  <li>Bewoonbare oppervlakte: 168 m²</li>
"""


def page(title: str, extra: str = "") -> str:
    return f"""
    <html><head><meta property="og:title" content="{title}"></head><body>
      <header>Immo Voorbeeld, Kortrijksesteenweg 1, 9050 Gent</header>
      <h1>{title}</h1>
      <ul>{FACTS}</ul>
      {extra}
      <footer>Immo Voorbeeld &middot; 9050 Gent &middot; BE 0123.456.789</footer>
    </body></html>
    """


def test_office_address_does_not_become_the_property_location():
    listing = extract_listing(
        "https://x.be/te-koop/woning-te-koop-astene",
        page("Woning te koop in Astene"),
        source="X",
    )
    assert listing is not None
    assert listing.city == "Astene"
    # 9050 Gent is the agency's own address and must not be attached.
    assert listing.postal_code == ""


def test_postcode_in_url_path_wins():
    listing = extract_listing(
        "https://kleyo.be/te-koop/9140-Temse/halfopen-gezinswoning-met-tuin/abc",
        page("Halfopen gezinswoning met tuin te koop in Temse"),
        source="X",
    )
    assert listing is not None
    assert listing.postal_code == "9140"
    assert listing.city == "Temse"


def test_city_leading_the_title():
    listing = extract_listing(
        "https://www.immotion.be/nl/te-koop/4409832",
        page("Kortrijk - Charmante te renoveren burgerwoning"),
        source="X",
    )
    assert listing is not None
    assert listing.city == "Kortrijk"


def test_renovation_year_is_not_a_postcode():
    listing = extract_listing(
        "https://www.immotion.be/nl/te-koop/4420728",
        page("Wevelgem - Instapklare halfopen woning",
             extra="<li>Renovatiejaar 1962</li><li>Richting 1998</li>"),
        source="X",
    )
    assert listing is not None
    assert listing.city == "Wevelgem"
    assert listing.postal_code != "1962"
    assert listing.postal_code != "1998"


def test_city_from_slug_when_title_is_unhelpful():
    listing = extract_listing(
        "https://x.be/te-koop/appartementen-in-ruiselede",
        page("Ons aanbod"),
        source="X",
    )
    assert listing is not None
    assert listing.city == "Ruiselede"


def test_multi_word_city_from_title():
    listing = extract_listing(
        "https://x.be/te-koop/123",
        page("Woning te koop in Sint-Martens-Latem"),
        source="X",
    )
    assert listing is not None
    assert listing.city == "Sint-Martens-Latem"
