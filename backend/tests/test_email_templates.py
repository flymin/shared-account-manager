from email.message import EmailMessage

import pytest

from app.plugins import get_mail_tool
from app.plugins.templates.six_digit_code import SixDigitCodeTemplate


def extract_code(raw):
    template = get_mail_tool("mailcom").template
    assert template is not None
    return template.extract_code(raw)


def mail(
    code="123456",
    sender="ExampleService <noreply@login.example.test>",
    html=False,
    subject="你的临时 ExampleService 登录代码",
):
    msg = EmailMessage()
    msg["From"], msg["Subject"] = sender, subject
    msg["Date"] = "Fri, 18 Sep 2026 12:00:00 +0000"
    msg.set_content(
        f"输入此临时验证码以继续：\n{code}\n未请求验证码？你可以忽略此邮件。"
    )
    if html:
        msg.add_alternative(
            f"<html><head><style>/* 654321 */</style></head><body><p>ExampleService</p><strong>{code}</strong><a href='https://example.test/987654'>Help</a></body></html>",
            subtype="html",
        )
    return msg.as_bytes()


@pytest.mark.parametrize(
    "html,subject",
    [
        (False, "你的临时 ExampleService 登录代码"),
        (True, "Your temporary ExampleService login code"),
        (True, "Votre code ExampleService"),
    ],
)
def test_multilingual_mime_and_unique_numeric_code(html, subject):
    assert extract_code(mail(html=html, subject=subject)) == "123456"
    assert extract_code(mail("123456\n654321")) is None
    assert extract_code(mail(sender="ExampleService <spoof@example.test>")) is None
    assert extract_code(mail("91234567")) is None
    assert extract_code(mail(subject="Unrelated service")) is None


def test_verification_rules_are_configurable_and_match_exact_sender(monkeypatch):
    monkeypatch.setenv("MAIL_CODE_SENDER", "noreply@another.example.test")
    monkeypatch.setenv("MAIL_CODE_SUBJECT_KEYWORD", "DifferentService")
    assert extract_code(mail()) is None
    assert (
        extract_code(
            mail(
                sender="Notifier <NOREPLY@ANOTHER.EXAMPLE.TEST>",
                subject="Your DIFFERENTSERVICE login code",
            )
        )
        == "123456"
    )
    assert (
        extract_code(
            mail(
                sender="noreply@another.example.test.attacker.test",
                subject="DifferentService",
            )
        )
        is None
    )
    assert (
        extract_code(
            mail(sender="noreply@another.example.test", subject="Unrelated code")
        )
        is None
    )


@pytest.mark.parametrize(
    "sender,subject",
    [
        ("", "ExampleService"),
        ("noreply@login.example.test", ""),
        ("Display <noreply@login.example.test>", "ExampleService"),
        ("not-an-email", "ExampleService"),
        ("noreply@login.example.test", "ExampleService\nInjected"),
    ],
)
def test_invalid_template_configuration_disables_template(monkeypatch, sender, subject):
    monkeypatch.setenv("MAIL_CODE_SENDER", sender)
    monkeypatch.setenv("MAIL_CODE_SUBJECT_KEYWORD", subject)
    assert get_mail_tool("mailcom").template is None


@pytest.mark.parametrize("outer_code", [None, "654321"])
def test_attached_mail_and_multipart_subtrees_are_not_body_codes(outer_code):
    outer = EmailMessage()
    outer["From"] = "ExampleService <noreply@login.example.test>"
    outer["Subject"] = "ExampleService notice"
    outer.set_content(outer_code or "This notice contains no login code.")
    attached = EmailMessage()
    attached.set_content("Old unrelated code: 123456")
    attached.add_alternative("<p>123456</p>", subtype="html")
    outer.add_attachment(attached, filename="old.eml")
    assert extract_code(outer.as_bytes()) == outer_code
    outer.get_payload()[-1].replace_header("Content-Disposition", "inline")
    assert extract_code(outer.as_bytes()) == outer_code


@pytest.mark.parametrize(
    "sender",
    [
        "noreply@notify.example.test",
        "noreply@notify1.example.test",
        "Notifier <NOREPLY@NOTIFY42.EXAMPLE.TEST>",
        "noreply@notify-east.example.test",
    ],
)
def test_sender_label_wildcard_matches_metadata_and_raw_headers(sender):
    template = SixDigitCodeTemplate.from_config(
        {"sender": "noreply@notify*.example.test", "subject_keyword": "ExampleService"}
    )
    assert template is not None
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = "Your ExampleService login code"
    message.set_content("Temporary code: 123456")
    assert template.matches(sender, str(message["Subject"]))
    assert template.extract_code(message.as_bytes()) == "123456"


@pytest.mark.parametrize("rule", ["*@notify*.example.test", "*@notify1.example.test"])
@pytest.mark.parametrize(
    "sender",
    [
        "otp@notify1.example.test",
        "noreply@notify1.example.test",
        "Notifier <LOGIN@NOTIFY1.EXAMPLE.TEST>",
        "login+code@notify1.example.test",
        "first.last@notify1.example.test",
    ],
)
def test_whole_mailbox_wildcard_matches_metadata_and_raw_headers(rule, sender):
    template = SixDigitCodeTemplate.from_config(
        {"sender": rule, "subject_keyword": "ExampleService"}
    )
    assert template is not None
    assert template.matches(sender, "ExampleService login code")
    assert template.extract_code(mail(sender=sender)) == "123456"
    assert (
        template.extract_code(mail(sender=sender, subject="Unrelated service")) is None
    )
    assert template.extract_code(mail(code="123456\n654321", sender=sender)) is None
    assert (
        template.extract_code(mail(code="No code in this message", sender=sender))
        is None
    )


def test_mailbox_and_domain_wildcards_allow_an_empty_domain_suffix():
    template = SixDigitCodeTemplate.from_config(
        {"sender": "*@notify*.example.test", "subject_keyword": "ExampleService"}
    )
    assert template.matches("otp@notify.example.test", "ExampleService")
    assert template.extract_code(mail(sender="otp@notify.example.test")) == "123456"


@pytest.mark.parametrize(
    "sender",
    [
        "@notify1.example.test",
        ".login@notify1.example.test",
        "login..code@notify1.example.test",
        "a" * 65 + "@notify1.example.test",
        "otp@notify-.example.test",
        "otp@other.example.test",
        "otp@child.notify1.example.test",
        "otp@notify1.example.test.attacker.test",
        "otp@notify1.examplextest",
        "otp@notify1.attacker.example.test",
        "otp@notify1.example.test, other@notify1.example.test",
    ],
)
def test_mailbox_wildcard_keeps_sender_and_domain_boundaries(sender):
    template = SixDigitCodeTemplate.from_config(
        {"sender": "*@notify*.example.test", "subject_keyword": "ExampleService"}
    )
    assert not template.matches(sender, "ExampleService")
    assert template.extract_code(mail(sender=sender)) is None


@pytest.mark.parametrize(
    "sender",
    [
        "noreply@example.test",
        "noreply@other.example.test",
        "noreply@evilnotify1.example.test",
        "noreply@notify1.example.test.attacker.test",
        "noreply@notify1.examplextest",
        "noreply@child.notify1.example.test",
        "noreply@notify1.attacker.example.test",
        "other@notify1.example.test",
        "noreply+tag@notify1.example.test",
        "noreply@notify-.example.test",
        "noreply@notify1.example.test.",
        "noreply@notify１.example.test",
        "noreply@notify" + "1" * 64 + ".example.test",
        "noreply@notify1.example.test, attacker@elsewhere.example.test",
    ],
)
def test_sender_wildcard_rejects_sibling_domains_and_ambiguous_addresses(sender):
    template = SixDigitCodeTemplate.from_config(
        {"sender": "noreply@notify*.example.test", "subject_keyword": "ExampleService"}
    )
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = "ExampleService login code"
    message.set_content("123456")
    assert not template.matches(sender, str(message["Subject"]))
    assert template.extract_code(message.as_bytes()) is None


@pytest.mark.parametrize(
    "rule",
    [
        "prefix*@notify.example.test",
        "*suffix@notify*.example.test",
        "**@notify*.example.test",
        "*@*.example.test",
        "*@notify*.*.test",
        "*@localhost",
        "*@-invalid.example.test",
        "noreply@*.example.test",
        "noreply@notify*example.test",
        "noreply@notify*.test",
        "noreply@notify*.*.test",
        "noreply@no*tify.example.test",
        "noreply@notify**.example.test",
        "noreply@-notify*.example.test",
        "noreply@notify*.example.test,other@example.test",
    ],
)
def test_wildcard_configuration_cannot_expand_beyond_one_prefixed_label(rule):
    assert (
        SixDigitCodeTemplate.from_config(
            {"sender": rule, "subject_keyword": "ExampleService"}
        )
        is None
    )


@pytest.mark.parametrize("tool", ["mailcom", "gmxcom", "outlook", "outlook_manual"])
@pytest.mark.parametrize("local", ["noreply", "*"])
def test_every_registered_mailbox_tool_uses_the_same_sender_rule(
    tool, local, monkeypatch
):
    for prefix in ("MAIL", "OUTLOOK"):
        monkeypatch.setenv(prefix + "_CODE_SENDER", local + "@notify*.example.test")
        monkeypatch.setenv(prefix + "_CODE_SUBJECT_KEYWORD", "ExampleService")
    monkeypatch.setenv("OUTLOOK_MANUAL_CLIENT_ID", "fictional-public-client")
    configured = get_mail_tool(tool)
    assert configured is not None and configured.template is not None
    assert configured.template.matches("noreply@notify7.example.test", "ExampleService")
    assert configured.template.matches(
        "otp@notify7.example.test", "ExampleService"
    ) == (local == "*")
    assert not configured.template.matches(
        "otp@notify7.attacker.test", "ExampleService"
    )


@pytest.mark.parametrize("header", ["From", "Subject"])
def test_duplicate_matching_headers_are_not_accepted(header):
    message = EmailMessage()
    message["From"] = "noreply@login.example.test"
    message["Subject"] = "ExampleService"
    message.set_content("123456")
    raw = f"{header}: unrelated@example.test\r\n".encode() + message.as_bytes()
    assert extract_code(raw) is None
