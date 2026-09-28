"""Explicit registry of trusted mailbox backends, independent of email templates."""

from collections.abc import Callable
from dataclasses import dataclass

from ...mail import Mailbox, OAuthCredential, OAuthProvider
from .mailcom import MailComClient
from .outlook import OutlookGraphClient, OutlookOAuthProvider


@dataclass(frozen=True)
class MailBackendPlugin:
    id: str
    name: str
    create: Callable[[str, str, OAuthCredential | None], Mailbox]
    oauth_provider: OAuthProvider | None = None


MAIL_BACKENDS = {
    "mailcom": MailBackendPlugin("mailcom", "mail.com", MailComClient),
    "outlook": MailBackendPlugin(
        "outlook", "Outlook", OutlookGraphClient, OutlookOAuthProvider()
    ),
}
