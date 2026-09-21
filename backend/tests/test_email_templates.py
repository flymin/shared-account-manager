from email.message import EmailMessage

import pytest

from app.plugins import configured_templates
from app.plugins.template_a import TemplateA


def extract_code(raw):
    template = TemplateA.from_environment()
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
    assert TemplateA.from_environment() is None
    assert configured_templates() == ()


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
