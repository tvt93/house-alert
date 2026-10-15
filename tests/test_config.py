from pathlib import Path

from house_alert.config import load_agencies, load_criteria, save_agencies_platforms

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_shipped_criteria_parses():
    criteria = load_criteria(CONFIG / "subscriptions" / "default.yaml")
    assert "house" in criteria.property_types
    assert "garagebox" in criteria.exclude_keywords
    # Placeholders are intentionally open-ended until filled in.
    assert criteria.price.is_open
    assert criteria.postcodes == []


def test_shipped_registry_parses():
    agencies = load_agencies(CONFIG / "agencies.yaml")
    assert len(agencies) > 60
    assert {a.region for a in agencies} == {"gent", "kortrijk"}
    assert all(a.url.startswith("https://") for a in agencies)
    assert all(not a.url.endswith("/") for a in agencies)
    # No duplicate hosts.
    hosts = [a.url for a in agencies]
    assert len(hosts) == len(set(hosts))


def test_region_and_name_filters():
    gent = load_agencies(CONFIG / "agencies.yaml", regions=["gent"])
    assert gent and all(a.region == "gent" for a in gent)
    only = load_agencies(CONFIG / "agencies.yaml", only=["Fleximmo"])
    assert [a.name for a in only] == ["Fleximmo"]


def test_platform_writeback_preserves_comments(tmp_path):
    src = (CONFIG / "agencies.yaml").read_text(encoding="utf-8")
    target = tmp_path / "agencies.yaml"
    target.write_text(src, encoding="utf-8")

    changed = save_agencies_platforms({"https://www.fleximmo.be": "whise"}, target)
    assert changed == 1

    out = target.read_text(encoding="utf-8")
    assert "# Registry of estate agencies to scan." in out
    agencies = load_agencies(target)
    by_name = {a.name: a for a in agencies}
    assert by_name["Fleximmo"].platform == "whise"
    assert by_name["Immotion"].platform == "auto"
