"""Second homes abroad, sold from a Belgian agency's site, never reach the
store, the page or the mail."""

from house_alert.config import Criteria
from house_alert.extract import in_belgium
from house_alert.filters import evaluate
from house_alert.models import Agency, Listing
from house_alert.scan import in_region
from house_alert.site import export_site
from house_alert.store import ListingStore


def listing(**kw):
    base = dict(url="https://x.be/pand/1", source="X", title="Woning te koop")
    base.update(kw)
    return Listing(**base)


def test_in_belgium_reads_postcode_town_and_hints():
    assert in_belgium(listing(postal_code="9070", city="Destelbergen")) is True
    assert in_belgium(listing(city="Destelbergen")) is True
    # mvastgoed.be: "Denia 2, 03750 Denia"; flame-estate.be: "34449 Lomas"
    assert in_belgium(listing(postal_code="03750", city="Denia", title="Denia Denia 2")) is False
    assert in_belgium(listing(postal_code="34449", city="Lomas")) is False
    # immo-zone.be stamps its Aalst office postcode on a Dubai project.
    assert in_belgium(listing(postal_code="9300", city="Aalst", title="Top investeringseigendom in Dubai!")) is False
    assert in_belgium(listing(title="Residentie Larrosa 43: woning nabij Guardamar")) is False
    assert in_belgium(listing(title="Villa in Javea", url="https://aclea-vastgoed.be/properties/03730-javea/")) is False
    # Nothing to go on is not evidence of abroad.
    assert in_belgium(listing()) is None
    assert in_belgium(listing(city="Techtower")) is None
    # Belgian towns that merely sound like trouble stay.
    assert in_belgium(listing(postal_code="8370", city="Blankenberge", title="Appartement aan de kust")) is True
    # home-vision.eu: "residentie Costa Brava" is a building in Blankenberge,
    # and the URL names the town. entree.be: "Sluis 22" is a street in Berlare.
    assert in_belgium(listing(postal_code="8370", city="Blankenberge", title="Appartement in residentie Costa Brava",
                              url="https://home-vision.eu/nl/aanbod/1/appartement-te-koop-in-8370-blankenberge")) is True
    assert in_belgium(listing(postal_code="9290", city="Berlare", title="Starterswoning te Berlare",
                              url="https://entree.be/pand/sluis-22-9290-berlare/8525513")) is True
    assert in_belgium(listing(postal_code="7700", city="Moeskroen")) is True


def test_abroad_is_kept_out_everywhere(tmp_path):
    agency = Agency(name="X", url="https://x.be")
    abroad = listing(url="https://x.be/pand/2", postal_code="03750", city="Denia", title="Denia Denia 2")
    home = listing(postal_code="9070", city="Destelbergen")
    assert in_region(agency, abroad) is False
    assert in_region(agency, home) is True

    verdict = evaluate(abroad, Criteria())
    assert verdict.passed is False and verdict.reason == "outside Belgium"
    assert evaluate(home, Criteria()).passed is True

    store = ListingStore(path=tmp_path / "s.json", listings={})
    store.add_all([abroad, home])
    target = export_site(store, tmp_path)
    assert '"Denia"' not in target.read_text(encoding="utf-8")
    assert '"Destelbergen"' in target.read_text(encoding="utf-8")
