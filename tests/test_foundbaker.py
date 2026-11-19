"""found-baker.com: a nationwide agency on one Drupal site.

Two traps, both from the Ghent town house that prompted this ("Prijs op
aanvraag", listed as € 1.315.000):

  * the "related" strip prints a neighbour's price beside its link, not
    inside it, so the generic reader took the dearest neighbour's price;
  * half the catalogue sits at /nl/<slug>, which no URL hint recognises; the
    catalogue's own cards say what is for sale.
"""

from house_alert.models import Agency
from house_alert.platforms import scraper_for
from house_alert.scan import in_region

AGENCY = Agency(
    name="Found & Baker",
    url="https://www.found-baker.com",
    platform="foundbaker",
    postcodes=["8", "9"],
)
URL = "https://www.found-baker.com/nl/te-koop/karaktervol-herenhuis"


def card(title, href, town, price, status="for-sale", label=""):
    return (
        f'<div class="views-row"><article class="c-card c-card--publication c-card--{status} c-card--">'
        f'<div class="c-card__top">{f"<div class=c-card__label>{label}</div>" if label else ""}'
        '<div class="c-card__image"><img alt="" src="/sites/default/files/x.jpg"/></div></div>'
        '<div class="c-card__content"><div class="c-card__header">'
        f'<h2 class="c-card__title">{title}</h2>'
        f'<div class="c-card__meta"><span class="c-card__location">{town}</span>'
        f'<span class="c-card__price">{price}</span></div></div></div>'
        f'<a href="{href}" class="c-card__link">Lees meer over {title}</a></article></div>'
    )


RELATED = (
    '<section class="c-property-list--related c-property-list"><h2>Gerelateerde panden</h2>'
    + card("Luxeueze stadswoning", "/nl/luxeueze-stadswoning-w02", "Gent", "€785.000")
    + card("Karaktervolle nieuwbouwwoning", "/nl/te-koop/karaktervolle-nieuwbouwwoning", "Drongen", "€1.315.000")
    + card("Rust, ruimte en natuur", "/nl/rust-ruimte-en-natuur", "Schelderode", "Prijs op aanvraag")
    + '</section>'
)


def page(address="Gent", price="Prijs op aanvraag", label="", title="Karaktervol herenhuis"):
    return (
        '<html><head><title>Karaktervol herenhuis | Found &amp; Baker</title>'
        '<meta name="description" content="Karaktervol herenhuis uit 1861 met zicht op het park, Gent Zuid"/>'
        '<meta property="og:image" content="https://www.found-baker.com/sites/default/files/hero.jpg"/>'
        '</head><body><main><div class="c-publication-hero"><div class="c-publication-hero__copy">'
        f'<h1 class="c-publication-hero__title">{title}</h1>'
        f'{f"<span class=c-publication-hero__label>{label}</span>" if label else ""}'
        '<div class="c-publication-hero__meta">'
        f'<address class="c-publication-hero__address">{address}</address>'
        f'<p class="c-publication-hero__price">{price}</p></div></div></div>'
        '<dl><div><dt>Bewoonbare opp</dt><dd>627m<sup>2</sup></dd></div>'
        '<div><dt>Opp perceel</dt><dd>295m<sup>2</sup></dd></div>'
        '<div><dt>Bouwjaar</dt><dd>1861</dd></div>'
        '<div><dt>Aantal badkamers</dt><dd>3</dd></div>'
        '<div><dt>Aantal slaapkamers</dt><dd>4</dd></div></dl>'
        '<p>Op een gegeerde locatie aan het Gentse Zuid bevindt zich dit herenhuis uit 1861.</p>'
        f'{RELATED}</main>'
        '<footer><address>Amerikaansestraat 179 1050 Elsene</address></footer></body></html>'
    )


def test_price_on_request_does_not_take_a_related_cards_price():
    listing = scraper_for(AGENCY).parse(URL, page(), AGENCY.name)
    assert listing is not None
    assert listing.price is None                  # "Prijs op aanvraag", not the neighbour's 1.315.000
    assert listing.title == "Karaktervol herenhuis"
    assert listing.city == "Gent"
    assert listing.postal_code == "9000"          # the table knows Gent as one postcode
    assert listing.street == ""
    assert listing.bedrooms == 4
    assert listing.living_area_m2 == 627
    assert listing.plot_area_m2 == 295
    assert in_region(AGENCY, listing)


def test_hero_price_and_address_are_the_propertys():
    listing = scraper_for(AGENCY).parse(
        URL, page(address="Bosvijverdreef 3 , Aalter", price="€895.000"), AGENCY.name)
    assert listing is not None
    assert listing.price == 895000
    assert listing.street == "Bosvijverdreef 3"
    assert listing.city == "Aalter"


def test_brussels_and_antwerp_fall_outside_the_region():
    scraper = scraper_for(AGENCY)
    elsene = scraper.parse(URL, page(address="Rue de Stassart 36 , Elsene", price="€600.000"), AGENCY.name)
    assert elsene is not None and elsene.postal_code == "1050"
    assert not in_region(AGENCY, elsene)
    schilde = scraper.parse(URL, page(address="Rozenlaan 3 , Schilde", price="€1.595.000"), AGENCY.name)
    assert schilde is not None and not in_region(AGENCY, schilde)


def test_a_page_without_a_hero_is_not_a_listing():
    catalogue = "<html><body><main>" + RELATED + "</main></body></html>"
    assert scraper_for(AGENCY).parse("https://www.found-baker.com/nl/te-koop", catalogue, AGENCY.name) is None


def test_status_is_the_hero_label_not_a_related_card():
    scraper = scraper_for(AGENCY)
    assert scraper.status(page(), URL) == "available"
    assert scraper.status(page(label="In optie"), URL) == "option"
    assert scraper.status(page(label="Verkocht"), URL) == "sold"
    related_sold = page().replace("</section>", card(
        "Herenhoeve", "/nl/te-koop/herenhoeve", "Gent", "", status="sold", label="Verkocht") + "</section>")
    assert scraper.status(related_sold, URL) == "available"


class PagedSite:
    """Two catalogue pages, then an empty one."""

    def __init__(self):
        self.asked = []
        self.pages = {
            "https://www.found-baker.com/nl/te-koop": (
                card("Karaktervol herenhuis", "/nl/te-koop/karaktervol-herenhuis", "Gent", "Prijs op aanvraag")
                + card("Landhuis", "/nl/landhuis-0", "Sint-Martens-Latem", "Prijs op aanvraag",
                       status="option-for-sale", label="In optie")
                + card("Rust, ruimte en natuur", "/nl/rust-ruimte-en-natuur", "Schelderode", "Prijs op aanvraag")
                + card("Herenhoeve", "/nl/te-koop/herenhoeve", "Gent", "", status="sold", label="Verkocht")),
            "https://www.found-baker.com/nl/te-koop?page=1": (
                card("Charmant hotelpand", "/nl/charmant-hotelpand", "Brugge", "€1.250.000")),
            "https://www.found-baker.com/nl/te-koop?page=2": "<p>Geen resultaten</p>",
        }

    def get(self, url):
        self.asked.append(url)
        body = self.pages.get(url)
        return f"<html><body>{body}</body></html>" if body is not None else None


def test_discovery_reads_every_catalogue_page_and_only_the_for_sale_cards():
    site = PagedSite()
    urls = scraper_for(AGENCY).discover(AGENCY, site)
    assert urls == [
        "https://www.found-baker.com/nl/te-koop/karaktervol-herenhuis",
        "https://www.found-baker.com/nl/rust-ruimte-en-natuur",       # no /te-koop/ in the path
        "https://www.found-baker.com/nl/charmant-hotelpand",          # from page two
    ]
    assert site.asked[-1].endswith("?page=2")                         # stopped at the empty page
