"""Explicit registry of trusted email templates, independent of mailbox backends."""

from collections.abc import Callable
from dataclasses import dataclass

from ...mail import EmailTemplate
from .six_digit_code import SixDigitCodeTemplate


@dataclass(frozen=True)
class EmailTemplatePlugin:
    id: str
    name: str
    load: Callable[[dict], EmailTemplate | None]


EMAIL_TEMPLATES = {
    "six_digit_code": EmailTemplatePlugin(
        "six_digit_code", "Six-digit code", SixDigitCodeTemplate.from_config
    ),
}
