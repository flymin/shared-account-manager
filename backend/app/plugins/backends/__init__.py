"""Explicit registry of trusted mailbox backends, independent of email templates."""

from collections.abc import Callable
from dataclasses import dataclass

from ...mail import Mailbox
from .mailcom import MailComClient


@dataclass(frozen=True)
class MailBackendPlugin:
    id: str
    name: str
    create: Callable[[str, str], Mailbox]


MAIL_BACKENDS = {
    "mailcom": MailBackendPlugin("mailcom", "mail.com", MailComClient),
}
