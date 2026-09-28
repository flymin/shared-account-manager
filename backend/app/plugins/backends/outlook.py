"""Microsoft Graph mailbox backend and OAuth provider.

OAuth application credentials and refresh tokens are supplied per account by the
core credential layer. This module never reads application secrets from process
environment variables and never treats a mailbox password as a token.
"""

import base64
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import formataddr
from urllib.parse import quote, urlencode, urlsplit

import httpx

from ...mail import MailError, Message, OAuthCredential

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_BASE = "https://login.microsoftonline.com"
GRAPH_HOST = "graph.microsoft.com"
TOKEN_HOST = "login.microsoftonline.com"
UA = "AccountManagerMailbox/1.0"
MAX_BODY = 2 * 1024 * 1024
SCOPES = "https://graph.microsoft.com/Mail.ReadWrite User.Read offline_access"
TENANT_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def received_time(value):
    if not isinstance(value, str) or not value:
        raise MailError("received_time")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise MailError("received_time") from None
    if stamp.tzinfo is None:
        raise MailError("received_time")
    return stamp.astimezone(timezone.utc)


def validate_tenant(value: str) -> str:
    value = str(value or "").strip()
    if not TENANT_PATTERN.fullmatch(value):
        raise MailError("configuration")
    return value


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@dataclass(frozen=True)
class OAuthToken:
    refresh_token: str
    authorized_email: str


class OutlookOAuthProvider:
    """OAuth authorization-code + PKCE implementation for Microsoft Graph."""

    backend_id = "outlook"
    pkce_pair = staticmethod(pkce_pair)

    def __init__(self, *, transport=None):
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            logger.handlers = [logging.NullHandler()]
        self.transport = transport

    @staticmethod
    def validate_config(client_id, client_secret, tenant):
        client_id = str(client_id or "").strip()
        if not 1 <= len(client_id) <= 256 or any(c.isspace() for c in client_id):
            raise MailError("configuration")
        client_secret = str(client_secret or "").strip() or None
        if client_secret is not None and len(client_secret) > 512:
            raise MailError("configuration")
        tenant = validate_tenant(tenant or "consumers")
        return client_id, client_secret, tenant

    def authorization_url(
        self, *, client_id, tenant, redirect_uri, state, code_challenge
    ):
        client_id, _, tenant = self.validate_config(client_id, None, tenant)
        parsed = urlsplit(str(redirect_uri))
        if parsed.scheme != "https" or not parsed.netloc or parsed.path != "/api/v1/mail-oauth/callback":
            raise MailError("configuration")
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{TOKEN_BASE}/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"

    @staticmethod
    def safe_url(url):
        parsed = urlsplit(str(url))
        try:
            port = parsed.port
        except ValueError:
            raise MailError("protocol") from None
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.hostname not in {GRAPH_HOST, TOKEN_HOST}
        ):
            raise MailError("protocol")
        return str(url)

    def _request(self, method, url, *, client, **kwargs):
        self.safe_url(url)
        try:
            with client.stream(
                method, url, timeout=12, follow_redirects=False, **kwargs
            ) as raw:
                body = bytearray()
                for chunk in raw.iter_bytes():
                    if len(body) + len(chunk) > MAX_BODY:
                        raise MailError("protocol")
                    body.extend(chunk)
                response = httpx.Response(
                    raw.status_code,
                    headers=raw.headers,
                    content=bytes(body),
                    request=raw.request,
                )
        except httpx.HTTPError:
            raise MailError("network", True) from None
        if response.is_redirect:
            raise MailError("protocol")
        return response

    def exchange_code(
        self,
        *,
        code,
        code_verifier,
        client_id,
        client_secret,
        tenant,
        redirect_uri,
    ):
        client_id, client_secret, tenant = self.validate_config(
            client_id, client_secret, tenant
        )
        if not isinstance(code, str) or not code or not isinstance(code_verifier, str):
            raise MailError("configuration")
        with httpx.Client(headers={"User-Agent": UA}, transport=self.transport) as client:
            data = {
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": SCOPES,
            }
            if client_secret:
                data["client_secret"] = client_secret
            response = self._request(
                "POST",
                f"{TOKEN_BASE}/{tenant}/oauth2/v2.0/token",
                client=client,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                raise MailError("authentication")
            try:
                payload = response.json()
                access_token = payload["access_token"]
                refresh_token = payload["refresh_token"]
            except (ValueError, TypeError, KeyError):
                raise MailError("authentication") from None
            if not isinstance(access_token, str) or not access_token:
                raise MailError("authentication")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise MailError("authentication")
            response = self._request(
                "GET",
                f"{GRAPH_BASE}/me",
                client=client,
                params={"$select": "mail,userPrincipalName"},
                headers={"Authorization": "Bearer " + access_token},
            )
            if response.status_code in {401, 403}:
                raise MailError("authentication")
            if response.status_code != 200:
                raise MailError("protocol")
            try:
                payload = response.json()
                address = payload.get("mail") or payload.get("userPrincipalName")
            except (ValueError, AttributeError):
                raise MailError("protocol") from None
            if not isinstance(address, str) or not address.strip():
                raise MailError("authentication")
            return OAuthToken(refresh_token, address.strip().lower())


class OutlookGraphClient:
    def __init__(self, email, password, oauth: OAuthCredential | None = None, *, transport=None):
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            logger.handlers = [logging.NullHandler()]
        self.email = email
        # OAuth is the only authentication path; the original mailbox password
        # is intentionally not retained by this client.
        self.password = None
        self.oauth = oauth
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept": "application/json"},
            transport=transport,
        )
        self.refresh_token = oauth.refresh_token if oauth else None
        self.rotated_refresh_token = None
        self._refresh_token_callback = None
        self.access_token = None
        self.access_expires_at = 0.0
        self.budget = 0.0

    def close(self):
        self.client.close()
        self.email = self.password = None
        self.oauth = None
        self._refresh_token_callback = None
        self.refresh_token = None
        self.rotated_refresh_token = None
        self.access_token = None

    def set_refresh_token_callback(self, callback):
        self._refresh_token_callback = callback

    @staticmethod
    def safe_url(url):
        parsed = urlsplit(str(url))
        try:
            port = parsed.port
        except ValueError:
            raise MailError("protocol") from None
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.hostname not in {GRAPH_HOST, TOKEN_HOST}
        ):
            raise MailError("protocol")
        return str(url)

    def _remaining(self):
        remaining = self.budget - time.monotonic()
        if remaining <= 0:
            raise MailError("network", True)
        return min(8, remaining)

    def _request(self, method, url, *, before_send=None, **kwargs):
        self.safe_url(url)
        if before_send is not None and not before_send():
            raise MailError("cancelled")
        try:
            with self.client.stream(
                method, url, timeout=self._remaining(), follow_redirects=False, **kwargs
            ) as raw:
                body = bytearray()
                for chunk in raw.iter_bytes():
                    if len(body) + len(chunk) > MAX_BODY:
                        raise MailError("protocol")
                    self._remaining()
                    body.extend(chunk)
                response = httpx.Response(
                    raw.status_code,
                    headers=raw.headers,
                    content=bytes(body),
                    request=raw.request,
                )
        except httpx.HTTPError:
            raise MailError("network", True) from None
        if response.is_redirect or len(response.content) > MAX_BODY:
            raise MailError("protocol")
        if response.status_code == 429 or response.status_code >= 500:
            raise MailError("network", True)
        return response

    def _token(self, *, force=False, before_send=None):
        if self.access_token and not force and time.monotonic() < self.access_expires_at - 30:
            return self.access_token
        if not self.oauth or not self.refresh_token:
            raise MailError("configuration")
        data = {
            "client_id": self.oauth.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": SCOPES,
        }
        if self.oauth.client_secret:
            data["client_secret"] = self.oauth.client_secret
        response = self._request(
            "POST",
            f"{TOKEN_BASE}/{validate_tenant(self.oauth.tenant)}/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            before_send=before_send,
        )
        if response.status_code != 200:
            raise MailError("authentication")
        try:
            payload = response.json()
            token = payload["access_token"]
            expires = int(payload.get("expires_in", 300))
        except (ValueError, TypeError, KeyError):
            raise MailError("authentication") from None
        if not isinstance(token, str) or not token:
            raise MailError("authentication")
        self.access_token = token
        self.access_expires_at = time.monotonic() + max(60, min(expires, 3600))
        replacement = payload.get("refresh_token")
        if isinstance(replacement, str) and replacement and replacement != self.refresh_token:
            self.refresh_token = replacement
            self.rotated_refresh_token = replacement
            if self._refresh_token_callback is not None:
                self._refresh_token_callback(replacement)
                self.rotated_refresh_token = None
        return token

    def _graph(self, method, path, *, before_send=None, retry_auth=True, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        url = path if path.startswith("https://") else f"{GRAPH_BASE}{path}"
        token = self._token(before_send=before_send)
        response = self._request(
            method,
            url,
            before_send=before_send,
            headers={"Authorization": "Bearer " + token, **headers},
            **kwargs,
        )
        if response.status_code == 401 and retry_auth and self.refresh_token:
            self.access_token = None
            token = self._token(force=True, before_send=before_send)
            response = self._request(
                method,
                url,
                before_send=before_send,
                headers={"Authorization": "Bearer " + token, **headers},
                **kwargs,
            )
        if response.status_code in {401, 403}:
            raise MailError("authentication")
        return response

    def _ensure_budget(self, seconds=60):
        if self.budget <= time.monotonic():
            self.budget = time.monotonic() + seconds

    def iter_unread(self, since, deadline):
        remaining = min(60, (deadline - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            raise MailError("network", True)
        self.budget = time.monotonic() + remaining
        since_value = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        url = "/me/mailFolders/inbox/messages"
        params = {
            "$select": "id,from,subject,receivedDateTime,isRead",
            "$filter": f"receivedDateTime ge {since_value} and isRead eq false",
            "$orderby": "receivedDateTime desc",
            "$top": "100",
        }
        seen = 0
        while url and seen < 2000:
            response = self._graph("GET", url, params=params)
            params = None
            if response.status_code != 200:
                raise MailError("protocol")
            try:
                payload = response.json()
                rows = payload["value"]
            except (ValueError, TypeError, KeyError):
                raise MailError("protocol") from None
            if not isinstance(rows, list):
                raise MailError("protocol")
            for row in rows:
                seen += 1
                if not isinstance(row, dict) or row.get("isRead") is not False:
                    continue
                message_id = row.get("id")
                sender = row.get("from", {}).get("emailAddress", {})
                if not isinstance(message_id, str) or not message_id:
                    raise MailError("protocol")
                if not isinstance(sender, dict):
                    raise MailError("protocol")
                received = received_time(row.get("receivedDateTime"))
                yield Message(
                    message_id,
                    formataddr((str(sender.get("name") or ""), str(sender.get("address") or ""))),
                    str(row.get("subject") or ""),
                    received,
                )
                if seen >= 2000:
                    return
            url = payload.get("@odata.nextLink")
            if url is not None:
                self.safe_url(url)

    def read_raw(self, message_id):
        if not isinstance(message_id, str) or not message_id:
            raise MailError("protocol")
        self._ensure_budget()
        response = self._graph(
            "GET", f"/me/messages/{quote(message_id, safe='')}/$value", headers={"Accept": "message/rfc822"}
        )
        if response.status_code != 200 or len(response.content) > MAX_BODY:
            raise MailError("protocol")
        return response.content

    def mark_read(self, message_id, *, before_write=None, deadline=None):
        if not isinstance(message_id, str) or not message_id:
            raise MailError("protocol")
        if deadline is None:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        remaining = min(30, (deadline - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            raise MailError("network", True)
        self.budget = time.monotonic() + remaining
        path = f"/me/messages/{quote(message_id, safe='')}"
        response = self._graph(
            "PATCH", path, before_send=before_write,
            headers={"Content-Type": "application/json"}, json={"isRead": True}
        )
        if response.status_code not in {200, 202, 204}:
            raise MailError("read_failed", True)
        verify = self._graph("GET", path, params={"$select": "isRead"})
        if verify.status_code != 200:
            raise MailError("read_failed", True)
        try:
            confirmed = verify.json().get("isRead") is True
        except (ValueError, AttributeError):
            confirmed = False
        if not confirmed:
            raise MailError("read_failed", True)
