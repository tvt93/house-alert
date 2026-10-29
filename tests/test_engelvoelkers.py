"""engelvoelkers.com: one Next.js site for the whole country.

The page text misled the generic reader (a "similar homes" title, the plot
as living area, no town); the facts live in the ``__NEXT_DATA__`` blob.
The fixture is the shape of the Merelbeke villa that prompted this.
"""

import json

from house_alert.geo import postcodes_for_place
from house_alert.models import Agency
from house_alert.platforms import scraper_for
from house_alert.scan import in_region

AGENCY = Agency(
    name="Engel & Völkers",
    url="https://www.engelvoelkers.com/be/nl",
    platform="engelvoelkers",
    postcodes=["8", "9"],
    index_urls=["https://www.engelvoelkers.com/be/nl/eigendom/res/koop/huis/vlaams-gewest"],
)
URL = "https://www.engelvoelkers.com/be/nl/exposes/f7dcaddb-76b2-5b7a-9cd7-0dee9a29bc78"


def record(**overrides):
    base = {
        "id": "f7dcaddb-76b2-5b7a-9cd7-0dee9a29bc78",
        "profile": {"title": "Elegant en royaal wonen te Merelbeke",
                    "description": "In de Burgemeester Maenhautstraat te Merelbeke bevindt zich\n\ndeze ruime gezinswoning."},
        "propertyImages": [{"id": "c1294597-7a5c-4150-8fa9-087ba543a049", "main": True, "position": 0}],
        "area": {"plotSurface": {"max": 1070, "min": 1070}, "usableSurface": {"max": 443, "min": 443},
                 "livingSurface": {"max": 315, "min": 315}},
        "hasPriceOnRequest": False,
        "price": {"salesPrice": {"max": 845000, "min": 845000}},
        "propertyType": "house", "propertySubType": "villa",
        "propertyMarketingType": "sale", "businessArea": "residential",
        "statusNormalized": "ACTIVE",
        "fuzzyMoveByMeter": 500, "displayLat": 50.987549944192835, "displayLng": 3.771917891089891,
        "countryAlpha2": "BE",
        "shopAddressStreet": "Vlaanderenstraat 33", "shopAddressCity": "Gent", "shopAddressPostalCode": "9000",
        "constructionYear": {"max": 2004, "min": 2004},
        "bedrooms": {"max": 3, "min": 3}, "bathrooms": {"max": 2, "min": 2},
        "addressComponents": [
            {"placeType": "country", "text": "België"},
            {"placeType": "administrative_area_level_1", "text": "Vlaanderen"},
            {"placeType": "locality", "text": "Merelbeke-Melle"},
            {"placeType": "sublocality_level_1", "text": "Lemberge"},
        ],
    }
    base.update(overrides)
    return base


def page(listing_record, similar=None):
    """The page as the site serves it: a title that is a slogan, a strip of
    similar homes with their own prices, and the data blob."""
    data = {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["listing", listing_record["id"], "nl"],
         "state": {"data": {"status": "OK", "listing": listing_record}}},
        {"queryKey": ["similar-listings", listing_record["id"]],
         "state": {"data": similar or [{"displayId": "W-1", "propertyType": "house"}]}},
    ]}}}}
    return (
        '<html><head><title>Engel &amp; Völkers</title>'
        '<meta property="og:title" content="Elegant en royaal wonen te Merelbeke"/>'
        '<meta property="og:image" content="https://uploadcare.engelvoelkers.com/c1294597-7a5c-4150-8fa9-087ba543a049/"/>'
        '</head><body><p>Lemberge, Merelbeke-Melle</p><h1>Elegant en royaal wonen te Merelbeke</h1>'
        '<p>€ 845.000</p><p>3 Slaapkamers</p><p>~315 m² Woonoppervlakte</p><p>~1.070 m² Perceeloppervlakte</p>'
        '<p>Bouwjaar 2004</p>'
        '<h2>Huizen in de buurt</h2><a href="/be/nl/exposes/11111111-2222-3333-4444-555555555555">'
        'Riante, instapklare villa op prachtig perceel in Heusden € 1.250.000</a>'
        '<footer>Engel &amp; Völkers Gent Centrum Vlaanderenstraat 33 9000 Gent</footer>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        '</body></html>'
    )


def test_expose_urls_are_listings_and_index_pages_are_not():
    scraper = scraper_for(AGENCY)
    assert scraper.is_listing_url(URL)
    assert scraper.is_listing_url(URL + "?secretAccessId=37d1a6e8&utm_source=newsletter")
    assert scraper.canonical(URL + "?secretAccessId=37d1a6e8&utm_source=newsletter") == URL
    assert not scraper.is_listing_url("https://www.engelvoelkers.com/be/nl/eigendom/res/koop/huis/vlaams-gewest")
    assert not scraper.is_listing_url("https://www.engelvoelkers.com/be/nl/eigendom/res/koop/huis/vlaams-gewest/gent")
    assert not scraper.is_listing_url("https://www.engelvoelkers.com/be/en/exposes/f7dcaddb-76b2-5b7a-9cd7-0dee9a29bc78")


def test_facts_come_from_the_data_blob_not_the_page_text():
    listing = scraper_for(AGENCY).parse(URL, page(record()), AGENCY.name)
    assert listing is not None
    assert listing.title == "Elegant en royaal wonen te Merelbeke"
    assert listing.price == 845000
    assert listing.property_type == "house"
    assert listing.bedrooms == 3
    assert listing.living_area_m2 == 315
    assert listing.plot_area_m2 == 1070
    assert listing.city == "Merelbeke-Melle"
    assert listing.postal_code == "9820"          # Lemberge is one postcode
    assert listing.street == ""                   # never printed; the office's is not the house's
    assert listing.image_url == "https://uploadcare.engelvoelkers.com/c1294597-7a5c-4150-8fa9-087ba543a049/"
    assert listing.description.startswith("In de Burgemeester Maenhautstraat te Merelbeke bevindt zich deze")
    assert (listing.lat, listing.lon, listing.geo) == (50.987549944192835, 3.771917891089891, "approx")
    assert in_region(AGENCY, listing)


def test_price_on_request_and_ranges_stay_unknown():
    scraper = scraper_for(AGENCY)
    on_request = scraper.parse(URL, page(record(hasPriceOnRequest=True, price={})), AGENCY.name)
    assert on_request is not None and on_request.price is None
    project = scraper.parse(URL, page(record(
        isDevelopmentProject=True,
        price={"salesPrice": {"min": 325000, "max": 610000}},
        bedrooms={"min": 1, "max": 3},
        area={"livingSurface": {"min": 60, "max": 140}, "plotSurface": {"min": 2400, "max": 2400}})), AGENCY.name)
    assert project is not None
    assert project.price is None and project.bedrooms is None and project.living_area_m2 is None
    assert project.plot_area_m2 == 2400
    # Nothing but ranges is a project brochure, not a house.
    assert scraper.parse(URL, page(record(
        price={"salesPrice": {"min": 325000, "max": 610000}},
        bedrooms={"min": 1, "max": 3},
        area={"livingSurface": {"min": 60, "max": 140}})), AGENCY.name) is None


def test_rentals_sold_and_foreign_pages_are_not_listings():
    scraper = scraper_for(AGENCY)
    assert scraper.parse(URL, page(record(propertyMarketingType="rent")), AGENCY.name) is None
    assert scraper.parse(URL, page(record(statusNormalized="SOLD")), AGENCY.name) is None
    assert scraper.parse(URL, page(record(countryAlpha2="ES")), AGENCY.name) is None
    assert scraper.parse(URL, "<html><body>404</body></html>", AGENCY.name) is None


def test_status_is_read_from_the_blob():
    scraper = scraper_for(AGENCY)
    assert scraper.status(page(record()), URL) == "available"
    assert scraper.status(page(record(statusNormalized="SOLD")), URL) == "sold"
    assert scraper.status(page(record(statusNormalized="RESERVED")), URL) == "option"


def test_postcode_only_when_the_place_name_means_one_code():
    assert postcodes_for_place("Lemberge") == ["9820"]
    assert postcodes_for_place("Gent") == ["9000"]
    assert len(postcodes_for_place("Antwerpen")) > 1
    # A merged municipality is unknown by its new name; its parts are not.
    assert set(postcodes_for_place("Merelbeke-Melle")) == {"9820", "9090"}
    assert postcodes_for_place("Nergenshuizen") == []
    listing = scraper_for(AGENCY).parse(URL, page(record(addressComponents=[
        {"placeType": "locality", "text": "Antwerpen"}])), AGENCY.name)
    assert listing is not None
    assert listing.city == "Antwerpen" and listing.postal_code == ""


def test_region_guard_uses_the_town_when_the_postcode_is_unknown():
    scraper = scraper_for(AGENCY)
    antwerp = scraper.parse(URL, page(record(addressComponents=[
        {"placeType": "locality", "text": "Antwerpen"}])), AGENCY.name)
    assert antwerp is not None and not in_region(AGENCY, antwerp)
    merged = scraper.parse(URL, page(record(addressComponents=[
        {"placeType": "locality", "text": "Merelbeke-Melle"}])), AGENCY.name)
    assert merged is not None and merged.postal_code == "" and in_region(AGENCY, merged)
    unknown = scraper.parse(URL, page(record(addressComponents=[
        {"placeType": "locality", "text": "Nergenshuizen"}])), AGENCY.name)
    assert unknown is not None and in_region(AGENCY, unknown)


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get(self, url):
        self.asked.append(url)
        return self.pages.get(url)

    def sitemaps_from_robots(self, origin):
        raise AssertionError("the sitemap holds no listings and must not be read")


def test_discovery_reads_only_the_index_pages():
    index = AGENCY.index_urls[0]
    html = ('<html><body>'
            '<a href="/be/nl/exposes/0d634d60-5466-58f0-b1de-b4f7e69d1ea4">A</a>'
            '<a href="/be/nl/exposes/0d634d60-5466-58f0-b1de-b4f7e69d1ea4?x=1">A again</a>'
            '<a href="/be/nl/eigendom/res/koop/huis/vlaams-gewest/gent">Gent</a>'
            '<a href="https://www.engelvoelkers.com/be/nl/exposes/115fd351-3efe-5afa-b004-3fbbdc153749">B</a>'
            '</body></html>')
    fetcher = _Fetcher({index: html, AGENCY.url: "<html><body>home</body></html>"})
    found = scraper_for(AGENCY).discover(AGENCY, fetcher)
    assert found == [
        "https://www.engelvoelkers.com/be/nl/exposes/0d634d60-5466-58f0-b1de-b4f7e69d1ea4",
        "https://www.engelvoelkers.com/be/nl/exposes/115fd351-3efe-5afa-b004-3fbbdc153749",
    ]
    assert fetcher.asked == [index]
