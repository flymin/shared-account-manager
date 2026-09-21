"""Public API for configured mailbox tools and their trusted plugin registries."""

from .backends import MAIL_BACKENDS, MailBackendPlugin
from .templates import EMAIL_TEMPLATES, EmailTemplatePlugin
from .tools import default_mail_tool, get_mail_tool, mail_tool_options

__all__ = [
    "MAIL_BACKENDS",
    "MailBackendPlugin",
    "EMAIL_TEMPLATES",
    "EmailTemplatePlugin",
    "default_mail_tool",
    "get_mail_tool",
    "mail_tool_options",
]
