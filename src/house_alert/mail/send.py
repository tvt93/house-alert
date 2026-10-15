"""SMTP delivery.

Configured entirely from the environment so the same code runs from a local
.env and from GitHub Actions secrets with nothing to switch over:

  SMTP_HOST      default smtp.gmail.com
  SMTP_PORT      default 587 (STARTTLS); use 465 for implicit TLS
  SMTP_USER      the sending mailbox
  SMTP_PASSWORD  a Gmail app password, not the account password
  MAIL_FROM      defaults to SMTP_USER
  MAIL_TO        comma-separated recipients
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate


class MailConfigError(RuntimeError):
    pass


@dataclass
class MailSettings:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: list[str]

    @classmethod
    def from_env(cls, require_recipients: bool = True) -> "MailSettings":
        user = os.environ.get("SMTP_USER", "").strip()
        password = os.environ.get("SMTP_PASSWORD", "").strip()
        recipients = [
            r.strip() for r in os.environ.get("MAIL_TO", "").split(",") if r.strip()
        ]
        missing = [
            name for name, value in (
                ("SMTP_USER", user),
                ("SMTP_PASSWORD", password),
                ("MAIL_TO", recipients if require_recipients else True),
            ) if not value
        ]
        if missing:
            raise MailConfigError(
                "missing mail settings: " + ", ".join(missing)
                + " (copy .env.example to .env, or set them as Actions secrets)"
            )
        return cls(
            host=os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=user,
            password=password,
            sender=os.environ.get("MAIL_FROM", user).strip(),
            recipients=recipients,
        )


def build_message(subject: str, html: str, text: str, settings: MailSettings,
                  recipients: list[str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("house-alert", settings.sender))
    msg["To"] = ", ".join(recipients or settings.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send(subject: str, html: str, text: str, settings: MailSettings | None = None,
         recipients: list[str] | None = None) -> None:
    """Deliver one digest. `recipients` overrides MAIL_TO; subscriptions use it."""
    settings = settings or MailSettings.from_env(require_recipients=not recipients)
    if not (recipients or settings.recipients):
        raise MailConfigError("no recipients")
    msg = build_message(subject, html, text, settings, recipients)

    if settings.port == 465:
        with smtplib.SMTP_SSL(settings.host, settings.port, timeout=30) as smtp:
            smtp.login(settings.user, settings.password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.user, settings.password)
        smtp.send_message(msg)
