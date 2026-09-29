"""Explicit registry of trusted mailbox backends, independent of email templates."""

from collections.abc import Callable
from dataclasses import dataclass
import os

from ...mail import Mailbox, OAuthCredential, OAuthProvider
from .mailcom import MailComClient
from .outlook import (
    OutlookGraphClient,
    OutlookManualOAuthProvider,
    OutlookOAuthProvider,
)


@dataclass(frozen=True)
class MailBackendPlugin:
    id: str
    name: str
    create: Callable[[str, str, OAuthCredential | None], Mailbox]
    oauth_provider: OAuthProvider | None = None
    available: Callable[[], bool] | None = None


MAIL_BACKENDS = {
    "mailcom": MailBackendPlugin("mailcom", "mail.com", MailComClient),
    "outlook": MailBackendPlugin(
        "outlook", "Outlook", OutlookGraphClient, OutlookOAuthProvider()
    ),
    "outlook_manual": MailBackendPlugin(
        "outlook_manual",
        "Outlook manual",
        OutlookGraphClient,
        OutlookManualOAuthProvider(),
        lambda: bool(os.getenv("OUTLOOK_MANUAL_CLIENT_ID", "").strip()),
    ),
}
