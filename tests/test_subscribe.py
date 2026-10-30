"""A daily mail requested through a GitHub issue opened by the overview page."""

import json

import pytest
import yaml

from house_alert import sealed
from house_alert.config import criteria_from_dict, load_subscriptions
from house_alert.subscribe import (
    SubscribeError, from_issue, payload_from_issue, slugify, to_yaml, validate,
    write_subscription,
)

PUBLIC, PRIVATE = sealed.keygen()


def request(**over):
    base = {
        "name": "Zuid Gent / villa's", "to": sealed.seal("page@example.com", PUBLIC),
        "property_types": ["house", "land"],
        "price": {"min": None, "max": 750000, "include_unknown": True},
        "bedrooms": {"min": 4, "max": None, "include_unknown": True},
        "living_area_m2": {"min": 200, "max": None, "include_unknown": False},
        "plot_area_m2": {"min": 600, "max": None, "include_unknown": True},
        "postcodes": ["9000", "98", "9000"],
    }
    base.update(over)
    return base


def issue_body(payload):
    return ("### Zuid Gent\n\n- Type: woning, grond\n- Prijs: tot 750.000\n\n"
            "<!-- house-alert " + json.dumps(payload) + " -->\n")


def test_payload_is_read_from_the_hidden_comment():
    assert payload_from_issue(issue_body({"name": "x"})) == {"name": "x"}
    with pytest.raises(SubscribeError):
        payload_from_issue("Hallo, ik wil graag een mail")
    with pytest.raises(SubscribeError):
        payload_from_issue("<!-- house-alert {not json} -->")


def test_validate_normalises():
    r = validate(request())
    assert r.name == "Zuid Gent / villa's"
    assert r.slug == "zuid-gent-villa-s"
    assert r.property_types == ["house", "land"]
    assert r.postcodes == ["9000", "98"]
    assert r.ranges["price"].max == 750000 and r.ranges["price"].min is None
    assert r.ranges["living_area_m2"].include_unknown is False
    assert slugify("Thuis bij Thijs & Él") == "thuis-bij-thijs-el"
    assert slugify("///") == "filter"


@pytest.mark.parametrize("bad, fragment", [
    ({"to": "page@example.com"}, "versleuteld"),
    ({"name": "   "}, "naam"),
    ({"property_types": ["castle"]}, "castle"),
    ({"price": {"min": 500000, "max": 100000}}, "minimum"),
    ({"postcodes": ["90000"]}, "cijfers"),
    ({"bedrooms": {"min": "abc"}}, "getal"),
    ({"postcodes": [str(i) for i in range(60)]}, "hoogstens"),
])
def test_validate_refuses(bad, fragment):
    with pytest.raises(SubscribeError) as exc:
        validate(request(**bad))
    assert fragment in str(exc.value)


def test_yaml_round_trips_through_the_loader(tmp_path, monkeypatch):
    req = validate(request())
    text = to_yaml(req)
    assert "page@example.com" not in text
    raw = yaml.safe_load(text)
    assert raw["name"] == "Zuid Gent / villa's"
    c = criteria_from_dict(raw)
    assert c.property_types == ["house", "land"]
    assert c.price.max == 750000 and c.bedrooms.min == 4 and c.plot_area_m2.min == 600
    assert c.postcodes == ["9000", "98"]
    assert c.living_area_m2.include_unknown is False
    assert "garagebox" in c.exclude_keywords

    path = write_subscription(req, tmp_path)
    assert path.name == "zuid-gent-villa-s.yaml"
    monkeypatch.setenv("MAIL_KEY", PRIVATE)
    [sub] = load_subscriptions(tmp_path)
    assert sub.recipients == ["page@example.com"]
    assert sub.name == "Zuid Gent / villa's"


def test_same_name_twice_and_the_cap(tmp_path):
    req = validate(request())
    assert write_subscription(req, tmp_path).name == "zuid-gent-villa-s.yaml"
    assert write_subscription(req, tmp_path).name == "zuid-gent-villa-s-2.yaml"
    assert write_subscription(req, tmp_path).name == "zuid-gent-villa-s-3.yaml"
    with pytest.raises(SubscribeError):
        write_subscription(req, tmp_path, limit=3)


def test_from_issue_end_to_end(tmp_path):
    path = from_issue(issue_body(request(name="Kortrijk")), tmp_path)
    assert path.name == "kortrijk.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["property_types"] == ["house", "land"]
