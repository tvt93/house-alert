"""Mail settings come from the environment, and GitHub Actions passes an
empty string for every secret that was never set. Run #58 (2026-11-02) died on
int("") because SMTP_PORT was empty, so "" must mean "use the default"."""

import pytest

from house_alert.mail.send import MailConfigError, MailSettings


def _base_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("MAIL_TO", "me@example.com")


def test_empty_optional_secrets_fall_back_to_defaults(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("MAIL_FROM", "")

    settings = MailSettings.from_env()

    assert settings.host == "smtp.gmail.com"
    assert settings.port == 587
    assert settings.sender == "bot@example.com"
    assert settings.recipients == ["me@example.com"]


def test_explicit_optional_settings_are_used(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "mail.example.org")
    monkeypatch.setenv("SMTP_PORT", " 465 ")
    monkeypatch.setenv("MAIL_FROM", "alerts@example.org")

    settings = MailSettings.from_env()

    assert (settings.host, settings.port, settings.sender) == (
        "mail.example.org", 465, "alerts@example.org")


def test_non_numeric_port_is_a_config_error_not_a_crash(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "five-eight-seven")

    with pytest.raises(MailConfigError, match="SMTP_PORT"):
        MailSettings.from_env()


def test_missing_required_settings_are_named(monkeypatch):
    for name in ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"):
        monkeypatch.setenv(name, "")

    with pytest.raises(MailConfigError, match="SMTP_USER, SMTP_PASSWORD, MAIL_TO"):
        MailSettings.from_env()
