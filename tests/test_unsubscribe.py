"""Stopping a mail from the overview page, and the overview the page shows.

The repository is public and anyone can open an issue, so a stop request is
only honoured when the sealed address it carries is the mail's recipient.
"""

import json

import pytest

from house_alert import sealed
from house_alert.subscribe import (
    SubscribeError, export_overview, handle_issue, overview, unsubscribe,
)

PUBLIC, PRIVATE = sealed.keygen()
SECRETS = {"MAIL_KEY": PRIVATE, "MAIL_TO": "owner@example.com"}


def _write(directory, slug, to, created="2026-10-30", **extra):
    lines = [f"# Dagelijkse mail, ingesteld vanaf de overzichtspagina op {created}.",
             f"name: '{extra.get('name', slug.title())}'", "to:", f"  - '{to}'", "enabled: true",
             "property_types:", "  - house", "  - land",
             "price:", "  min: ", "  max: 750000", "  include_unknown: true",
             "bedrooms:", "  min: 4", "  max: ", "  include_unknown: false",
             "postcodes:", "  - '9000'"]
    (directory / f"{slug}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stop_body(slug, address):
    payload = {"action": "unsubscribe", "slug": slug, "to": sealed.seal(address, PUBLIC)}
    return "### x\n\nStoppen.\n\n<!-- house-alert " + json.dumps(payload) + " -->\n"


def test_overview_shows_filters_but_no_recipients(tmp_path):
    _write(tmp_path, "huis-alert", sealed.seal("wife@example.com", PUBLIC))
    _write(tmp_path, "standaard", "$MAIL_TO", created=None, name="Standaard")
    (tmp_path / "standaard.yaml").write_text(
        (tmp_path / "standaard.yaml").read_text(encoding="utf-8").replace("op None.", "by hand"),
        encoding="utf-8")

    records = overview(tmp_path)

    assert [r["slug"] for r in records] == ["huis-alert", "standaard"]
    huis = records[0]
    assert huis["name"] == "Huis-Alert"
    assert huis["created"] == "2026-10-30"
    assert huis["property_types"] == ["house", "land"]
    assert huis["price"] == {"max": 750000}
    assert huis["bedrooms"] == {"min": 4}
    assert huis["postcodes"] == ["9000"]
    assert huis["include_unknown"] is False
    assert records[1]["created"] is None
    dumped = json.dumps(records)
    for forbidden in ("enc:", "wife@", "MAIL_TO", "to"):
        assert f'"{forbidden}"' not in dumped and "wife@example.com" not in dumped


def test_export_overview_skips_disabled_and_writes_json(tmp_path):
    _write(tmp_path, "actief", sealed.seal("a@example.com", PUBLIC))
    _write(tmp_path, "uit", sealed.seal("b@example.com", PUBLIC))
    text = (tmp_path / "uit.yaml").read_text(encoding="utf-8").replace("enabled: true", "enabled: false")
    (tmp_path / "uit.yaml").write_text(text, encoding="utf-8")

    target = export_overview(tmp_path, tmp_path / "docs" / "subscriptions.json")

    data = json.loads(target.read_text(encoding="utf-8"))
    assert [s["slug"] for s in data["subscriptions"]] == ["actief"]
    assert data["generated"]


def test_stop_with_the_right_address_removes_the_file(tmp_path):
    _write(tmp_path, "huis-alert", sealed.seal("Wife@Example.com", PUBLIC))

    action, path = handle_issue(_stop_body("huis-alert", "wife@example.com"), tmp_path, SECRETS)

    assert action == "unsubscribe"
    assert path.name == "huis-alert.yaml" and not path.exists()


def test_stop_matches_a_secret_recipient_too(tmp_path):
    _write(tmp_path, "standaard", "$MAIL_TO")

    unsubscribe({"action": "unsubscribe", "slug": "standaard",
                 "to": sealed.seal("owner@example.com", PUBLIC)}, tmp_path, SECRETS)

    assert not (tmp_path / "standaard.yaml").exists()


def test_stop_with_another_address_is_refused_and_keeps_the_file(tmp_path):
    _write(tmp_path, "huis-alert", sealed.seal("wife@example.com", PUBLIC))

    with pytest.raises(SubscribeError, match="komt niet overeen"):
        handle_issue(_stop_body("huis-alert", "stranger@example.com"), tmp_path, SECRETS)
    assert (tmp_path / "huis-alert.yaml").exists()


@pytest.mark.parametrize("slug, fragment", [
    ("nope", "bestaat niet"),
    ("../secrets", "ongeldig"),
    ("", "ontbreekt"),
])
def test_stop_unknown_or_bad_slug(tmp_path, slug, fragment):
    _write(tmp_path, "huis-alert", sealed.seal("wife@example.com", PUBLIC))
    with pytest.raises(SubscribeError, match=fragment):
        handle_issue(_stop_body(slug, "wife@example.com"), tmp_path, SECRETS)


def test_stop_without_the_key_is_refused(tmp_path):
    _write(tmp_path, "huis-alert", sealed.seal("wife@example.com", PUBLIC))
    with pytest.raises(SubscribeError, match="sleutel"):
        handle_issue(_stop_body("huis-alert", "wife@example.com"), tmp_path, {})
    assert (tmp_path / "huis-alert.yaml").exists()


def test_stop_needs_a_sealed_address(tmp_path):
    _write(tmp_path, "huis-alert", sealed.seal("wife@example.com", PUBLIC))
    with pytest.raises(SubscribeError, match="niet versleuteld"):
        unsubscribe({"slug": "huis-alert", "to": "wife@example.com"}, tmp_path, SECRETS)


def test_a_subscribe_body_still_subscribes(tmp_path):
    payload = {"name": "Nieuw", "to": sealed.seal("x@example.com", PUBLIC), "property_types": ["house"]}
    body = "<!-- house-alert " + json.dumps(payload) + " -->"

    action, path = handle_issue(body, tmp_path, SECRETS)

    assert action == "subscribe" and path.name == "nieuw.yaml" and path.exists()
