"""Explicit registry of bundled, trusted plugins. No user-supplied import paths."""

from collections.abc import Callable
from dataclasses import dataclass

from ..mail import EmailTemplate, Mailbox
from .mailcom import MailComClient
from .template_a import TemplateA


@dataclass(frozen=True)
class MailBackendPlugin:
    id: str
    name: str
    create: Callable[[str, str], Mailbox]


@dataclass(frozen=True)
class EmailTemplatePlugin:
    id: str
    name: str
    load: Callable[[], EmailTemplate | None]


MAIL_BACKENDS = {
    "mailcom": MailBackendPlugin("mailcom", "mail.com", MailComClient),
}
EMAIL_TEMPLATES = {
    "template_a": EmailTemplatePlugin(
        "template_a", "Template A", TemplateA.from_environment
    ),
}
DEFAULT_MAIL_BACKEND = "mailcom"


def get_mail_backend(identity: str | None) -> MailBackendPlugin | None:
    return MAIL_BACKENDS.get(identity)


def mail_backend_options():
    return {
        "default": DEFAULT_MAIL_BACKEND
        if get_mail_backend(DEFAULT_MAIL_BACKEND)
        else None,
        "items": [{"id": p.id, "name": p.name} for p in MAIL_BACKENDS.values()],
    }


def configured_templates() -> tuple[EmailTemplate, ...]:
    return tuple(t for p in EMAIL_TEMPLATES.values() if (t := p.load()) is not None)
