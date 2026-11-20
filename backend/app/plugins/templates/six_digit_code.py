"""Six-digit code: configurable sender/subject and one six-digit body code."""

import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses

from ..common.html import HTML, visible_text


DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
MAILBOX_LOCAL = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"


def sender_pattern(sender):
    if len(sender) > 254 or not re.fullmatch(r"[^@\s<>;,]+@[^@\s<>;,]+", sender):
        return None
    local, domain = sender.rsplit("@", 1)
    labels = domain.split(".")
    if "*" in local and local != "*":
        return None
    if "*" in domain:
        if (
            domain.count("*") != 1
            or len(labels) < 3
            or not labels[0].endswith("*")
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}", labels[0][:-1])
            or any(not DNS_LABEL.fullmatch(label) for label in labels[1:])
        ):
            return None
    elif local == "*" and (
        len(labels) < 2 or any(not DNS_LABEL.fullmatch(label) for label in labels)
    ):
        return None
    local_pattern = MAILBOX_LOCAL if local == "*" else re.escape(local)
    domain_pattern = re.escape(domain).replace(r"\*", "[a-z0-9-]*")
    return re.compile(local_pattern + "@" + domain_pattern)


@dataclass(frozen=True)
class SixDigitCodeTemplate:
    sender: str
    subject_keyword: str

    @classmethod
    def from_config(cls, options):
        if set(options) != {"sender", "subject_keyword"} or not all(
            isinstance(value, str) for value in options.values()
        ):
            return None
        sender = options["sender"].strip().casefold()
        subject = options["subject_keyword"].strip().casefold()
        if (
            sender_pattern(sender) is None
            or not subject
            or len(subject) > 200
            or any(char in subject for char in "\r\n")
        ):
            return None
        return cls(sender, subject)

    def matches(self, sender, subject):
        addresses = getaddresses([str(sender or "")])
        if len(addresses) != 1:
            return False
        address = addresses[0][1].casefold()
        pattern = sender_pattern(self.sender)
        if pattern is None or len(address) > 254 or not pattern.fullmatch(address):
            return False
        if "*" in self.sender:
            local, domain = address.rsplit("@", 1)
            if self.sender.startswith("*@") and len(local) > 64:
                return False
            if any(not DNS_LABEL.fullmatch(label) for label in domain.split(".")):
                return False
        return self.subject_keyword in str(subject or "").casefold()

    def extract_code(self, raw):
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if (
            len(message.get_all("From", [])) != 1
            or len(message.get_all("Subject", [])) != 1
        ):
            return None
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
