"""Addresses sealed by the overview page and opened by the mailing run."""

import pytest

from house_alert import sealed
from house_alert.config import load_subscriptions, resolve_recipient


def test_round_trip():
    public, private = sealed.keygen()
    token = sealed.seal("thijs@example.com", public)
    assert token.startswith("enc:")
    assert "example" not in token
    assert sealed.open_sealed(token, private) == "thijs@example.com"
    # every seal uses a fresh ephemeral key, so the same address never repeats
    assert sealed.seal("thijs@example.com", public) != token


def test_wrong_key_and_damaged_token():
    public, private = sealed.keygen()
    _, other = sealed.keygen()
    token = sealed.seal("a@example.com", public)
    with pytest.raises(sealed.SealError):
        sealed.open_sealed(token, other)
    with pytest.raises(sealed.SealError):
        sealed.open_sealed(token[:-6] + "AAAAAA", private)
    with pytest.raises(sealed.SealError):
        sealed.open_sealed("enc:not base64!", private)
    with pytest.raises(sealed.SealError):
        sealed.open_sealed("enc:AAAA", private)
    with pytest.raises(sealed.SealError):
        sealed.open_sealed("plain@example.com", private)


def test_browser_token_shape():
    """The page builds the token itself (tweetnacl): ephemeral public key,
    nonce, then crypto_box output. Check the layout we document."""
    import base64
    public, private = sealed.keygen()
    raw = base64.b64decode(sealed.seal("x@y.be", public)[4:])
    assert len(raw) == 32 + 24 + 16 + len("x@y.be")


def test_resolve_sealed_recipient(monkeypatch):
    public, private = sealed.keygen()
    token = sealed.seal("  sealed@example.com ", public)

    monkeypatch.delenv("MAIL_KEY", raising=False)
    assert resolve_recipient(token) is None                       # no key: unresolved, never guessed
    assert resolve_recipient(token, {"MAIL_KEY": private}) == "sealed@example.com"

    monkeypatch.setenv("MAIL_KEY", private)
    assert resolve_recipient(token) == "sealed@example.com"

    _, other = sealed.keygen()
    monkeypatch.setenv("MAIL_KEY", other)
    assert resolve_recipient(token) is None


def test_subscription_with_sealed_recipient(tmp_path, monkeypatch):
    public, private = sealed.keygen()
    token = sealed.seal("page@example.com", public)
    (tmp_path / "zuid-gent.yaml").write_text(
        f"name: Zuid Gent\nto:\n  - '{token}'\nbedrooms: {{min: 4}}\n", encoding="utf-8")

    monkeypatch.delenv("MAIL_KEY", raising=False)
    [sub] = load_subscriptions(tmp_path)
    assert sub.recipients == []
    assert not sub.deliverable
    assert sub.unresolved == ["enc:… (needs MAIL_KEY)"]

    monkeypatch.setenv("MAIL_KEY", private)
    [sub] = load_subscriptions(tmp_path)
    assert sub.recipients == ["page@example.com"]
    assert sub.criteria.bedrooms.min == 4
