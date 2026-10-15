import json

import pytest

from house_alert.config import ConfigError, Criteria, load_subscriptions, resolve_recipient
from house_alert.filters import evaluate
from house_alert.models import Listing


def test_recipient_resolution(monkeypatch):
    monkeypatch.setenv("MAIL_TO", "a@example.com")
    assert resolve_recipient("$MAIL_TO") == "a@example.com"
    assert resolve_recipient("${MAIL_TO}") == "a@example.com"
    assert resolve_recipient("b@example.com") == "b@example.com"
    assert resolve_recipient("$MAIL_TO_NOBODY") is None
    assert resolve_recipient("$MAIL_TO_NOBODY", {"MAIL_TO_NOBODY": "c@example.com"}) == "c@example.com"


def test_load_subscriptions(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("HOUSE_ALERT_SECRETS", json.dumps({"MAIL_TO_PARTNER": "p@example.com"}))
    (tmp_path / "default.yaml").write_text(
        "name: Standaard\nto: [$MAIL_TO]\nprice: {max: 400000}\npostcodes: [9000, '90']\n",
        encoding="utf-8")
    (tmp_path / "partner.yaml").write_text(
        "to:\n  - $MAIL_TO_PARTNER\n  - $MAIL_TO_UNSET\nbedrooms: {min: 3}\n",
        encoding="utf-8")
    (tmp_path / "old.yaml").write_text("enabled: false\nto: [x@example.com]\n", encoding="utf-8")

    subs = load_subscriptions(tmp_path)
    by_name = {s.name: s for s in subs}
    assert set(by_name) == {"Standaard", "partner"}

    default = by_name["Standaard"]
    assert default.recipients == ["a@example.com", "b@example.com"]
    assert default.criteria.price.max == 400000
    assert default.criteria.postcodes == ["9000", "90"]

    partner = by_name["partner"]
    assert partner.recipients == ["p@example.com"]
    assert partner.unresolved == ["$MAIL_TO_UNSET"]
    assert partner.deliverable
    assert partner.criteria.bedrooms.min == 3


def test_missing_directory_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_subscriptions(tmp_path / "nope")


def test_postcode_filter():
    def house(postcode):
        return Listing(url="https://x.be/1", source="X", postal_code=postcode,
                       property_type="house", price=300000)

    criteria = Criteria(postcodes=["9000", "85"])
    assert evaluate(house("9000"), criteria)
    assert evaluate(house("8530"), criteria)
    assert not evaluate(house("9030"), criteria)
    verdict = evaluate(house(""), criteria)
    assert not verdict and "unknown" in verdict.reason
