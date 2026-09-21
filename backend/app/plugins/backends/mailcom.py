"""mail.com webmail adapter. Credentials and provider responses never enter logs.

The desktop client's separately scoped list/raw/update endpoints preserve unread
flags until a matching code has been durably claimed by our worker.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ...totp import current_code
from ...mail import MailError, Message
from ..common.html import HTML, visible_text

READ_SCOPE = "mail_mailbox_r"
WRITE_SCOPE = "mail_mailbox_w"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"


def receipt_time(value):
    # INTERNALDATE is epoch milliseconds. Reject sender Date, missing timestamps,
    # and guessed units instead of silently showing an incorrect receipt time.
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 10**12 <= value < 10**14
    ):
        raise MailError("received_time")
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise MailError("received_time") from None


def json_script(document, identifier):
    script = next(
        (
            n
            for n in HTML(document).root.nodes()
            if n.tag == "script" and n.attrs.get("id") == identifier
        ),
        None,
    )
    if script is None:
        raise MailError("protocol")
    return json.loads(script.text())


class MailComClient:
    def __init__(self, email, password, otp_uri=None, *, transport=None):
        # HTTPX INFO records include full bearer-bearing session URLs. These
        # library namespaces must never propagate into the worker's INFO log.
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            logger.handlers = [logging.NullHandler()]
        self.email, self.password, self.otp_uri = email, password, otp_uri
        self.client = httpx.Client(headers={"User-Agent": UA}, transport=transport)
        self.tokens = {}
        self.config = None
        self.budget = 0

    def close(self):
        self.client.close()
        self.tokens.clear()
        self.password = self.otp_uri = None

    @staticmethod
    def safe_url(url, method="GET"):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        mail_host = host == "mail.com" or host.endswith(".mail.com")
        static_host = host == "uicdn.com" or host.endswith(".uicdn.com")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not (mail_host or (method == "GET" and static_host))
        ):
            raise MailError("protocol")
        return url

    def request(self, method, url, *, before_send=None, **kwargs):
        for _ in range(8):
            self.safe_url(url, method)
            remaining = self.budget - time.monotonic()
            if remaining <= 0:
                raise MailError("network", True)
            if before_send is not None and not before_send():
                raise MailError("cancelled")
            # The DB permission fence may itself wait for a lock. Its elapsed
            # time belongs to the same cycle/job budget as the HTTP request.
            remaining = self.budget - time.monotonic()
            if remaining <= 0:
                raise MailError("network", True)
            try:
                with self.client.stream(
                    method, url, timeout=min(8, remaining), **kwargs
                ) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > 2 * 1024 * 1024:
                            raise MailError("protocol")
                        if time.monotonic() >= self.budget:
                            raise MailError("network", True)
                        body.extend(chunk)
                    if time.monotonic() >= self.budget:
                        raise MailError("network", True)
                    result = httpx.Response(
                        response.status_code,
                        # iter_bytes already decompresses the body. Do not ask
                        # the reconstructed response to decompress it twice.
                        headers={
                            k: v
                            for k, v in response.headers.items()
                            if k.lower() not in {"content-encoding", "content-length"}
                        },
                        content=bytes(body),
                        request=response.request,
                    )
            except httpx.HTTPError:
                raise MailError("network", True) from None
            if result.is_redirect:
                next_url = urljoin(str(result.url), result.headers.get("location", ""))
                if urlsplit(next_url).netloc != urlsplit(str(result.url)).netloc:
                    kwargs.pop("auth", None)
                    kwargs["headers"] = {
                        k: v
                        for k, v in kwargs.get("headers", {}).items()
                        if k.lower() != "authorization"
                    }
                kwargs.pop("params", None)
                if result.status_code in {301, 302, 303}:
                    method = "GET"
                    kwargs.pop("data", None)
                    kwargs.pop("json", None)
                    kwargs.pop("content", None)
                url = next_url
                continue
            if result.status_code == 429 or result.status_code >= 500:
                raise MailError("network", True)
            return result
        raise MailError("protocol")

    def login(self):
        response = self.request("GET", "https://www.mail.com/")
        forms = [n for n in HTML(response.text).root.nodes() if n.tag == "form"]
        form = next(
            (
                n
                for n in forms
                if any(x.attrs.get("name") == "username" for x in n.nodes())
                and any(x.attrs.get("name") == "password" for x in n.nodes())
            ),
            None,
        )
        if form is None:
            raise MailError("protocol")
        fields = {
            n.attrs["name"]: n.attrs.get("value", "")
            for n in form.nodes()
            if n.tag == "input" and n.attrs.get("name")
        }
        fields.update(username=self.email, password=self.password)
        response = self.request(
            "POST",
            urljoin(str(response.url), form.attrs.get("action", "")),
            data=fields,
            headers={
                "Origin": "https://www.mail.com",
                "Referer": "https://www.mail.com/",
            },
        )
        response = self.handle_totp(response)
        match = re.search(r"new\s+FeatureDetection\s*\(\s*", response.text)
        if not match:
            if "captcha" in response.text.lower():
                raise MailError("challenge")
            raise MailError("authentication")
        detection, _ = json.JSONDecoder().raw_decode(response.text[match.end() :])
        capabilities = dict.fromkeys(
            (
                "beacon",
                "classesstatic",
                "cookies",
                "customelements",
                "fetch",
                "objectfreeze",
                "optionalchaining",
                "resizeobserver",
                "sessionstorage",
                "urlparser",
                "urlsearchparams",
            ),
            True,
        )
        capabilities.update(detection.get("serverTests", {}))
        self.client.cookies.set(
            "detectionResult",
            json.dumps(capabilities, separators=(",", ":")),
            domain=urlsplit(str(response.url)).hostname,
            path="/",
        )
        target = urljoin(str(response.url), detection["redirectUrl"])
        parts = urlsplit(target)
        params = parse_qs(parts.query, keep_blank_values=True)
        params["tz"] = ["0"]
        target = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(params, doseq=True),
                parts.fragment,
            )
        )
        response = self.request("GET", target, headers={"Referer": str(response.url)})
        navigator = json_script(response.text, "application-config")
        self.sid = parse_qs(urlsplit(str(response.url)).query).get("sid", [None])[0]
        if not self.sid:
            raise MailError("protocol")
        response = self.request(
            "GET",
            navigator["thirdParty"]["navigator"]["apps"]["mail"]["url_map"]["default"],
            headers={"Referer": str(response.url)},
        )
        self.referer = str(response.url)
        self.origin = "https://" + urlsplit(self.referer).netloc
        global_config = json_script(response.text, "global-config")
        self.config = {}
        for name in ("list", "detail"):
            source = global_config["fd"][name]["src"]
            url = urljoin(source, "application-config.js")
            config_response = self.request("GET", url)
            start = re.search(r"export\s+const\s+config\s*=\s*", config_response.text)
            if not start:
                raise MailError("protocol")
            self.config[name] = json.JSONDecoder().raw_decode(
                config_response.text[start.end() :]
            )[0]

    def handle_totp(self, response):
        root = HTML(response.text).root
        context = visible_text(root)
        authenticator = re.search(
            r"authenticator|authentication\s+app|authentifizierungs.?app|验证器|認証アプリ",
            context,
            re.I,
        )
        other_challenge = re.search(
            r"captcha|\bSMS\b|recovery\s+code|backup\s+code|code.{0,80}(?:sent|email|e-mail)|sent.{0,80}(?:email|e-mail)|短信|恢复码|恢复代码",
            context,
            re.I,
        )
        # Only submit explicitly identified authenticator fields, never CAPTCHA,
        # recovery codes, email/SMS challenges, or arbitrary six-digit forms.
        for form in (n for n in root.nodes() if n.tag == "form"):
            inputs = [
                n for n in form.nodes() if n.tag == "input" and n.attrs.get("name")
            ]
            otp = next(
                (
                    n
                    for n in inputs
                    if n.attrs.get("autocomplete") == "one-time-code"
                    or n.attrs.get("name", "").lower()
                    in {"totp", "totpcode", "totp_code", "otp", "otpcode"}
                    or (
                        authenticator
                        and n.attrs.get("name", "").lower()
                        in {
                            "code",
                            "token",
                            "verificationcode",
                            "verification_code",
                            "tan",
                        }
                    )
                ),
                None,
            )
            if otp is None:
                continue
            explicit_totp = otp.attrs.get("name", "").lower() in {
                "totp",
                "totpcode",
                "totp_code",
            }
            if other_challenge or not (authenticator or explicit_totp):
                raise MailError("challenge")
            if not self.otp_uri:
                raise MailError("two_factor_required")
            values = {
                n.attrs["name"]: n.attrs.get("value", "")
                for n in inputs
                if n.attrs.get("type") in {"hidden", "submit"}
            }
            values[otp.attrs["name"]] = current_code(
                self.otp_uri, datetime.now(timezone.utc)
            )["code"]
            action = urljoin(str(response.url), form.attrs.get("action", ""))
            result = self.request(
                "POST",
                action,
                data=values,
                headers={
                    "Origin": "https://" + urlsplit(str(response.url)).netloc,
                    "Referer": str(response.url),
                },
            )
            if not re.search(r"new\s+FeatureDetection", result.text):
                raise MailError("two_factor_invalid")
            return result
        if authenticator:
            raise MailError("two_factor_required" if not self.otp_uri else "challenge")
        return response

    def token(self, scope):
        cached = self.tokens.get(scope)
        if cached and cached[1] > time.monotonic() + 15:
            return cached[0]
        auth = self.config["list" if scope == READ_SCOPE else "detail"][
            "authentication"
        ]
        result = self.request(
            "POST",
            auth["oauthEndpointToken"],
            params={"sid": self.sid},
            auth=(auth["clientId"], auth["clientSecret"]),
            data={"grant_type": "urn:mam:oauth:grant-type:spa", "scope": scope},
            headers={"Origin": self.origin, "Referer": self.referer},
        )
        if result.status_code != 200:
            raise MailError("authentication")
        data = result.json()
        self.tokens[scope] = (
            data["access_token"],
            time.monotonic() + int(data.get("expires_in", 60)),
        )
        return data["access_token"]

    def api(
        self,
        method,
        url,
        *,
        scope=READ_SCOPE,
        accept="application/json",
        content_type=None,
        **kwargs,
    ):
        headers = {
            "Authorization": "Bearer " + self.token(scope),
            "Accept": accept,
            "Origin": self.origin,
            "Referer": self.referer,
        }
        if content_type:
            headers["Content-Type"] = content_type
        result = self.request(method, url, headers=headers, **kwargs)
        if result.status_code in {401, 403}:
            raise MailError("authentication")
        if result.status_code not in {200, 204}:
            raise MailError("protocol")
        return result

    def list_page(self, offset=0):
        endpoint = self.config["list"]["features"]["mailList"]["endpoints"]["getMails"]
        return self.api(
            "POST",
            endpoint["url"],
            content=b"",
            params={
                "folderTypeOrId": "INBOX",
                "offset": offset,
                "amount": 100,
                "orderBy": "INTERNALDATE DESC",
            },
            accept="application/vnd.1and1.mms.unified-maillist-v1+json; charset=utf-8",
            content_type="application/vnd.1and1.mms.inboxadrequest-v1+json; charset=utf-8",
        ).json()

    def iter_unread(self, since, deadline):
        remaining = min(60, (deadline - datetime.now(timezone.utc)).total_seconds())
        self.budget = time.monotonic() + remaining
        try:
            if self.config is None:
                self.login()
            for offset in range(0, 2000, 100):
                page = self.list_page(offset)
                rows = [
                    e["rawData"]
                    for e in page["mailListElements"]
                    if e.get("type") == "mail"
                ]
                if not rows:
                    return
                reached_old = False
                for row in rows:
                    attr, header = row["attribute"], row["mailHeader"]
                    received = receipt_time(attr.get("internalDate"))
                    if received < since:
                        reached_old = True
                        continue
                    if (
                        received > min(datetime.now(timezone.utc), deadline)
                        or attr.get("read") is not False
                    ):
                        continue
                    yield Message(
                        attr["mailIdentifier"],
                        str(header.get("from") or ""),
                        str(header.get("subject") or ""),
                        received,
                    )
                if reached_old or offset + 100 >= page.get("totalCount", 0):
                    return
            raise MailError("protocol")
        except MailError:
            raise
        except (ValueError, KeyError, TypeError, StopIteration):
            raise MailError("protocol") from None

    def read_raw(self, message_id):
        try:
            endpoint = self.config["detail"]["features"]["mailBox"]["endpoints"][
                "getRawMail"
            ]
            return self.api(
                "GET",
                endpoint["url"].replace("{mailId}", quote(message_id, safe="")),
                params=endpoint.get("params", {"absoluteURI": "false"}),
                accept="message/rfc822",
            ).content
        except MailError:
            raise
        except (ValueError, KeyError, TypeError):
            raise MailError("protocol") from None

    def mark_read(self, message_id, *, before_write=None, deadline=None):
        remaining = (
            min(30, (deadline - datetime.now(timezone.utc)).total_seconds())
            if deadline
            else 30
        )
        self.budget = time.monotonic() + remaining
        try:
            if self.config is None:
                self.login()
            endpoint = self.config["detail"]["features"]["mailBox"]["endpoints"][
                "updateMails"
            ]
            self.api(
                "POST",
                endpoint["url"],
                scope=WRITE_SCOPE,
                params=endpoint.get("params", {"absoluteURI": "false"}),
                accept="application/vnd.ui.trinity.message.batchupdate.result-v2+json; charset=utf-8",
                content_type="application/vnd.ui.trinity.message.batchupdate-v2+json; charset=utf-8",
                json={"read": True, "mailURIs": ["/Mail/" + message_id]},
                before_send=before_write,
            )
            # Verify the external postcondition; do not publish a code on a
            # partial-success batch response or merely HTTP 200.
            for offset in range(0, 2000, 100):
                page = self.list_page(offset)
                for item in page["mailListElements"]:
                    attr = item.get("rawData", {}).get("attribute", {})
                    if attr.get("mailIdentifier") == message_id:
                        if attr.get("read") is True:
                            return
                        raise MailError("read_failed", True)
                if offset + 100 >= page.get("totalCount", 0):
                    break
            raise MailError("read_failed", True)
        except MailError:
            raise
        except (ValueError, KeyError, TypeError):
            raise MailError("protocol") from None
