"""Mailbox/template contracts and provider-independent verification orchestration."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class MailError(Exception):
    """Only a public error key crosses this boundary; never provider responses."""

    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class OAuthCredential:
    """Decrypted, short-lived-in-memory credential passed to a backend plugin."""

    backend_id: str
    client_id: str
    client_secret: str | None
    tenant: str
    refresh_token: str
    version: int


@dataclass(frozen=True)
class MailboxCredentials:
    """Account mailbox credentials; the password is never overloaded with OAuth."""

    email: str
    password: str
    oauth: OAuthCredential | None = None


class OAuthProvider(Protocol):
    """Provider capability used by the account-scoped OAuth lifecycle."""

    backend_id: str
    mode: str

    @staticmethod
    def validate_config(client_id, client_secret, tenant): ...

    @staticmethod
    def pkce_pair(): ...

    def authorization_url(
        self, *, client_id, tenant, redirect_uri, state, code_challenge=None
    ): ...

    def exchange_code(
        self,
        *,
        code,
        code_verifier=None,
        client_id,
        client_secret,
        tenant,
        redirect_uri,
    ): ...


@dataclass(frozen=True)
class Message:
    message_id: str
    sender: str
    subject: str
    received_at: datetime


@dataclass(frozen=True)
class Candidate:
    message_id: str
    code: str
    received_at: datetime


class Mailbox(Protocol):
    def set_refresh_token_callback(self, callback: Callable[[str], None]) -> None:
        """Receive rotated OAuth refresh tokens before mailbox work continues."""
        ...

    def iter_unread(self, since: datetime, deadline: datetime) -> Iterator[Message]:
        """Newest first, receipt time in UTC; start a bounded polling cycle."""
        ...

    def read_raw(self, message_id: str) -> bytes:
        """RFC 5322 bytes, within that cycle's budget; must not mark as read."""
        ...

    def mark_read(
        self,
        message_id: str,
        *,
        before_write: Callable[[], bool],
        deadline: datetime,
    ) -> None:
        """Fence each write with before_write; verify read state before returning."""
        ...

    def close(self) -> None:
        """Release connections and in-memory credentials."""
        ...


class EmailTemplate(Protocol):
    def matches(self, sender: str, subject: str) -> bool:
        """Cheap metadata prefilter; no network or persistent state."""
        ...

    def extract_code(self, raw: bytes) -> str | None:
        """Revalidate raw headers and return one unambiguous code, or None."""
        ...


def valid_code(code):
    # Length/format belongs to each template. Core only bounds display/storage.
    return (
        isinstance(code, str)
        and 1 <= len(code) <= 128
        and code.isprintable()
        and code == code.strip()
    )


def find_candidate(
    client: Mailbox,
    templates: Sequence[EmailTemplate],
    since: datetime,
    deadline: datetime,
    excluded: set[str],
) -> Candidate | None:
    if not templates:
        raise MailError("configuration")
    for index, message in enumerate(client.iter_unread(since, deadline)):
        if index >= 2000:
            raise MailError("protocol")
        if not message.received_at.tzinfo:
            raise MailError("received_time")
        if message.message_id in excluded or not since <= message.received_at <= min(
            datetime.now(timezone.utc), deadline
        ):
            continue
        matching = [t for t in templates if t.matches(message.sender, message.subject)]
        if not matching:
            continue
        raw = client.read_raw(message.message_id)
        if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
            raise MailError("protocol")
        codes = {t.extract_code(raw) for t in matching} - {None}
        # Overlapping templates must agree; never guess between different codes.
        if len(codes) == 1:
            code = codes.pop()
            if not valid_code(code):
                raise MailError("protocol")
            return Candidate(message.message_id, code, message.received_at)
    return None
