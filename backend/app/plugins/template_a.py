"""Template A: configurable sender/subject and one six-digit body code."""

import os
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from .html import HTML, visible_text


@dataclass(frozen=True)
class TemplateA:
    sender: str
    subject_keyword: str

    @classmethod
    def from_environment(cls):
        sender = os.getenv("MAIL_CODE_SENDER", "").strip().casefold()
        subject = os.getenv("MAIL_CODE_SUBJECT_KEYWORD", "").strip().casefold()
        if (
            not re.fullmatch(r"[^@\s<>;,]+@[^@\s<>;,]+", sender)
            or len(sender) > 254
            or not subject
            or len(subject) > 200
            or any(char in subject for char in "\r\n")
        ):
            return None
        return cls(sender, subject)

    def matches(self, sender, subject):
        return (
            parseaddr(str(sender or ""))[1].casefold() == self.sender
            and self.subject_keyword in str(subject or "").casefold()
        )

    def extract_code(self, raw):
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not self.matches(message.get("From", ""), message.get("Subject", "")):
            return None
        codes = set()

        def body_parts(part):
            # walk() would descend into attached .eml messages even after skipping
            # their attachment container. Never inspect attachment subtrees.
            if (
                part.get_content_disposition() == "attachment"
                or part.get_content_maintype() == "message"
            ):
                return
            if part.is_multipart():
                for child in part.iter_parts():
                    yield from body_parts(child)
            else:
                yield part

        for part in body_parts(message):
            if (
                part.get_content_disposition() == "attachment"
                or part.get_content_type() not in {"text/plain", "text/html"}
            ):
                continue
            try:
                content = part.get_content()
            except (LookupError, UnicodeError, ValueError):
                continue
            if part.get_content_type() == "text/html":
                root = HTML(content).root

                content = visible_text(root)
            content = re.sub(r"https?://\S+", " ", content)
            codes.update(re.findall(r"(?<![\w])([0-9]{6})(?![\w])", content))
        return next(iter(codes)) if len(codes) == 1 else None
