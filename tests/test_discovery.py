from house_alert.extract import looks_like_listing_url
from house_alert.platforms.base import normalize_url


def test_index_pages_are_not_detail_pages():
    assert not looks_like_listing_url("https://www.immotion.be/nl/te-koop/")
    assert not looks_like_listing_url("https://www.immotion.be/nl/te-koop")
    assert looks_like_listing_url("https://www.immotion.be/nl/te-koop/4409832")


def test_normalize_repairs_doubled_locale_segment():
    # Exactly what immotion.be's sitemap emits.
    assert (
        normalize_url("https://www.immotion.be/nl//nl/te-koop/4420728")
        == "https://www.immotion.be/nl/te-koop/4420728"
    )


def test_normalize_collapses_slashes_and_drops_query():
    assert (
        normalize_url("https://x.be//te-koop///woning-1?utm_source=mail#foto")
        == "https://x.be/te-koop/woning-1"
    )


def test_normalize_leaves_clean_urls_alone():
    url = "https://kleyo.be/te-koop/9140-Temse/halfopen-woning/abc-123"
    assert normalize_url(url) == url


def test_dewaele_landing_and_category_pages_are_not_detail_pages():
    # dewaele.com: 1500+ SEO landing pages and city filters sat in the store
    # with the agency logo as photo, no price and the city "Alle".
    for url in (
        "https://www.dewaele.com/nl/te-koop/alle",
        "https://www.dewaele.com/nl/te-koop/alle/huis",
        "https://www.dewaele.com/nl/te-koop/alle/huis/herenhuis",
        "https://www.dewaele.com/nl/te-koop/alle/huis/lk-huis-te-koop-ruiselede-4-slaapkamers",
        "https://www.dewaele.com/nl/te-koop/luxevastgoed/lk-luxe-appartement-te-koop-gent",
        "https://www.dewaele.com/nl/te-koop/9000-gent",
        "https://www.dewaele.com/nl/te-koop/9000-gent/huis",
        "https://www.dewaele.com/nl/te-koop/8400-oostende/appartement/",
    ):
        assert not looks_like_listing_url(url), url
    assert looks_like_listing_url(
        "https://www.dewaele.com/nl/te-koop/8520-kuurne/huis/"
        "instapklare-woning-met-dubbele-garage-te-koop-in-kuurne-d8500-26326-268659")
    # A type word inside the slug, or a city whose name ends like one, is fine.
    assert looks_like_listing_url("https://kleyo.be/te-koop/9140-Temse/halfopen-woning/abc-123")
    assert looks_like_listing_url("https://x.be/te-koop/villa-met-zwembad-in-sint-martens-latem")
    assert looks_like_listing_url("https://x.be/te-koop/huis/9000-gent/kerkstraat-12")
    # cdf.be puts a reference number where dewaele.com puts the postcode.
    assert looks_like_listing_url("https://www.cdf.be/te-koop/2335-stijlvol-appartement-in-burgerwoning/")
    assert looks_like_listing_url("https://www.cdf.be/te-koop/2250-charmante-villa-met-mooie-tuin-te-melle/")
