"""Category pages must not be mistaken for a single property.

landbergh.be publishes /te-koop/woning-te-koop-astene as a grid of Astene
properties. An earlier version scraped it into one "listing" priced at some
other house's asking price.
"""

from house_alert.extract import extract_listing


def index_page(n: int = 10) -> str:
    cards = "".join(
        f'<a href="/te-koop/woning-{i}"><h3>Woning in Plaats{i}</h3>'
        f"<span>€ {300 + i}.000</span></a>"
        for i in range(n)
    )
    return f"""
    <html><head><meta property="og:title" content="Woning te koop in Astene"></head>
    <body><h1>Woning te koop in Astene</h1>
      <p>Op zoek naar een woning in Astene? Ontdek het aanbod van Landbergh.</p>
      {cards}
    </body></html>
    """


DETAIL_PAGE = """
<html><head><meta property="og:title" content="Rijwoning met tuin"></head><body>
  <h1>Rijwoning met tuin</h1>
  <div class="banner">€ 395.000</div>
  <ul><li>3 slaapkamers</li><li>Bewoonbare oppervlakte: 168 m²</li></ul>
  <aside>
    <h4>Gelijkaardige panden</h4>
    <a href="/te-koop/woning-a">Woning A € 410.000</a>
    <a href="/te-koop/woning-b">Woning B € 375.000</a>
    <a href="/te-koop/woning-c">Woning C € 520.000</a>
  </aside>
</body></html>
"""

LABELLED_PAGE = """
<html><head><meta property="og:title" content="Villa"></head><body>
  <h1>Villa</h1>
  <p>Vraagprijs: € 675.000</p>
  <ul><li>4 slaapkamers</li></ul>
""" + "".join(
    f'<a href="/te-koop/ander-{i}">Ander pand € {200 + i * 7}.000</a>' for i in range(12)
) + "</body></html>"


# The real landbergh.be category page renders most card prices outside any
# anchor, so stripping priced links is not enough on its own.
UNLINKED_GRID = """
<html><head><meta property="og:title" content="Woning te koop in Astene"></head>
<body><h1>Woning te koop in Astene</h1>
  <div><span>Woning in Astene</span><span>€ 320.000</span></div>
  <div><span>Woning in Deinze</span><span>€ 349.000</span></div>
  <div><span>Woning in Vinkt</span><span>€ 429.500</span></div>
  <div><span>Woning in Grammene</span><span>€ 604.987</span></div>
  <div><span>Woning in Zeveren</span><span>€ 680.000</span></div>
</body></html>
"""


def test_grid_of_unlinked_prices_is_rejected():
    assert extract_listing("https://www.landbergh.be/te-koop/woning-te-koop-astene",
                           UNLINKED_GRID, source="X") is None


def test_category_page_is_rejected():
    assert extract_listing("https://x.be/te-koop/woning-te-koop-astene",
                           index_page(), source="X") is None


def test_detail_page_with_a_similar_properties_sidebar_survives():
    listing = extract_listing("https://x.be/te-koop/woning-1", DETAIL_PAGE, source="X")
    assert listing is not None
    assert listing.price == 395000
    assert listing.bedrooms == 3


def test_a_labelled_price_always_wins_over_the_index_heuristic():
    listing = extract_listing("https://x.be/te-koop/villa-1", LABELLED_PAGE, source="X")
    assert listing is not None
    assert listing.price == 675000
