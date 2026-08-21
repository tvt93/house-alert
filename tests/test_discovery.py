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
