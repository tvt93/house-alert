"""Is the house still for sale? Detection, re-checking, and what the mail covers."""

from collections import Counter
from datetime import date, timedelta

from house_alert.extract import detect_status
from house_alert.models import Listing
from house_alert.net import Fetched
from house_alert.scan import recheck_status
from house_alert.store import ListingStore


def page(title: str, body: str, chrome: str = "") -> str:
    return (f'<html><head><meta property="og:title" content="{title}"><title>{title}</title></head>'
            f'<body><header class="site-header">{chrome}</header><h1>{title}</h1>{body}'
            f'<footer>{chrome}</footer></body></html>')


def test_detect_status_from_banner_and_title():
    assert detect_status(page("Rijwoning te koop", "<p>Vraagprijs: € 300.000</p>")) == "available"
    assert detect_status(page("VERKOCHT - Rijwoning", "<p>Vraagprijs: € 300.000</p>")) == "sold"
    assert detect_status(page("Rijwoning", "<span class='ribbon'>Verkocht</span><p>€ 300.000</p>")) == "sold"
    assert detect_status(page("Rijwoning", "<h2>Compromis in opmaak</h2><p>€ 300.000</p>")) == "option"
    assert detect_status(page("Rijwoning", "<p>Status: onder optie</p>")) == "option"
    assert detect_status(page("Rijwoning", "<p>Verkocht onder voorbehoud</p>")) == "option"
    assert detect_status(page("Rijwoning", "<p>Onder bod</p>")) == "option"


def test_detect_status_ignores_chrome_and_feature_lists():
    # "Recent verkocht" in the site navigation is about other houses.
    assert detect_status(page("Rijwoning", "<p>Vraagprijs: € 300.000</p>",
                              chrome="Te koop | Recent verkocht | Contact")) == "available"
    # A list of options is not an option on the house.
    assert detect_status(page("Rijwoning", "<p>Opties: garage, zonnepanelen. Prijs € 300.000</p>")) == "available"


class FakeFetcher:
    def __init__(self, answers: dict[str, Fetched]):
        self.answers = answers

    def fetch(self, url: str) -> Fetched:
        return self.answers[url]


def test_recheck_status_tracks_sold_gone_and_unreachable():
    urls = [f"https://x.be/te-koop/{i}" for i in range(5)]
    listings = [Listing(url=u, source="X", status="available") for u in urls]
    fetcher = FakeFetcher({
        urls[0]: Fetched(urls[0], 200, page("Rijwoning", "<p>€ 300.000</p>"), urls[0]),
        urls[1]: Fetched(urls[1], 200, page("Rijwoning", "<p>Verkocht</p>"), urls[1]),
        urls[2]: Fetched(urls[2], 404, None, urls[2]),
        urls[3]: Fetched(urls[3], 200, page("Te koop", ""), "https://x.be/te-koop"),   # bounced to the index
        urls[4]: Fetched(urls[4], None, None),                                          # network failure
    })
    today = date(2026, 10, 16)
    changes = recheck_status(listings, fetcher, today, workers=2)  # type: ignore[arg-type]

    assert [l.status for l in listings] == ["available", "sold", "gone", "gone", "available"]
    assert [bool(l.checked) for l in listings] == [True, True, True, True, False]
    assert changes == Counter({"checked": 4, "available->sold": 1, "available->gone": 2, "unreachable": 1})


def test_redirect_to_a_sibling_url_is_not_gone():
    assert not Fetched("https://x.be/nl/pand/1", 200, "", "https://x.be/nl-be/pand/1").redirected_away
    assert not Fetched("https://x.be/pand/1", 200, "", "https://x.be/pand/1/mooie-woning").redirected_away
    assert Fetched("https://x.be/pand/1", 200, "", "https://x.be/").redirected_away
    assert Fetched("https://x.be/te-koop/woning-1", 200, "", "https://x.be/te-koop").redirected_away


def test_store_due_for_check_unmailed_and_prune(tmp_path):
    store = ListingStore(path=tmp_path / "l.json", listings={})
    today = date(2026, 10, 16)

    def add(n, **kw):
        l = Listing(url=f"https://x.be/{n}", source="X", **kw)
        store.add(l, today=today)
        return l

    never = add(1)
    fresh = add(2, checked=today.isoformat())
    stale = add(3, checked=(today - timedelta(days=3)).isoformat())
    option = add(4, status="option", checked=(today - timedelta(days=5)).isoformat())
    sold = add(5, status="sold", checked=(today - timedelta(days=40)).isoformat())
    gone_recent = add(6, status="gone", checked=today.isoformat())

    due = store.due_for_check(today, every_days=2, limit=10)
    assert [l.url[-1] for l in due] == ["1", "4", "3"]         # never checked first, then oldest
    assert store.due_for_check(today, every_days=2, limit=1) == [never]

    # Only available listings go in the mail, and only once.
    assert {l.url[-1] for l in store.unmailed()} == {"1", "2", "3"}
    store.mark_mailed([never, fresh])
    assert [l.url[-1] for l in store.unmailed()] == ["3"]

    # Sold a month ago: forgotten. Gone yesterday: still shown.
    assert store.prune(today=today) == 1
    assert sold.id not in store.listings and gone_recent.id in store.listings

    store.save()
    reloaded = ListingStore.load(tmp_path / "l.json")
    assert reloaded.listings[option.id].status == "option"
    assert reloaded.listings[never.id].mailed is True
