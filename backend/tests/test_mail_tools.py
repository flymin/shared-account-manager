"""Tool configuration, template isolation and durable configuration fencing."""

import os
import subprocess
import sys

import pytest

from app import plugins
from app.db import Session
from app.email_worker import EmailWorker, lease_next, save_candidate, snapshot
from app.mail import Candidate
from app.models import EmailCodeRun, now
from app.plugins.tools import get_mail_tool, tool_catalog
from test_email_worker import Mailbox, start


def test_plugins_can_be_imported_without_a_tool_catalog(tmp_path):
    env = {**os.environ, "MAIL_TOOLS_CONFIG_FILE": str(tmp_path / "missing.toml")}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.plugins.backends.mailcom import MailComClient; "
            "from app.plugins.templates.six_digit_code import SixDigitCodeTemplate",
        ],
        env=env,
        check=True,
        capture_output=True,
    )


def test_file_catalog_registers_named_combinations_without_exposing_options(
    admin, make_account, monkeypatch, tmp_path
):
    config = tmp_path / "tools.toml"
    config.write_text("""
default = "example_login"
[[tools]]
id = "example_login"
name = "Example login"
backend = "mailcom"
template = "six_digit_code"
[tools.options]
sender = { env = "MAIL_CODE_SENDER" }
subject_keyword = "ExampleService"
[[tools]]
id = "other_login"
name = "Other login"
backend = "mailcom"
template = "six_digit_code"
[tools.options]
sender = "noreply@other.example.test"
subject_keyword = "OtherService"
""")
    monkeypatch.setenv("MAIL_TOOLS_CONFIG_FILE", str(config))
    tool_catalog.cache_clear()
    assert admin.get("/api/v1/mail-tools").json() == {
        "default": "example_login",
        "items": [
            {"id": "example_login", "name": "Example login"},
            {"id": "other_login", "name": "Other login"},
        ],
    }
    response = admin.post(
        "/api/v1/account-imports",
        json={
            "tier": "5x",
            "text": "default@example.test----fictional----fictional",
        },
    )
    assert response.status_code == 201
    assert admin.get("/api/v1/accounts").json()[0]["mail_tool"] == "example_login"
    assert get_mail_tool("example_login").template.matches(
        "noreply@login.example.test", "ExampleService login"
    )
    assert not get_mail_tool("other_login").template.matches(
        "noreply@login.example.test", "ExampleService login"
    )
    # Installed implementations alone are not selectable tools.
    assert get_mail_tool("mailcom") is None
    assert (
        admin.post(
            "/api/v1/account-imports/preview",
            json={
                "tier": "5x",
                "text": "new@example.test----fictional----fictional",
                "mail_tool": "mailcom",
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "content",
    [
        'default="missing"',
        '[[tools]]\nid="bad.module"\nname="Example"\nbackend="mailcom"\ntemplate="six_digit_code"',
        '[[tools]]\nid="one"\nname="Example"\nbackend="mailcom"\ntemplate="six_digit_code"\nrevision=true',
        '[[tools]]\nid="one"\nname="Example"\nbackend="mailcom"\ntemplate="six_digit_code"\n[tools.options]\nsender={env="INVALID-NAME"}',
        '[[tools]]\nid="one"\nname="Example"\nbackend="mailcom"\ntemplate="six_digit_code"\n[[tools]]\nid="one"\nname="Duplicate"\nbackend="mailcom"\ntemplate="six_digit_code"',
        'private_value = "fictional-secret-value"',
        "[invalid toml",
        "x" * (128 * 1024 + 1),
    ],
)
def test_invalid_catalog_fails_without_printing_configuration(
    monkeypatch, tmp_path, content
):
    config = tmp_path / "tools.toml"
    config.write_text(content)
    monkeypatch.setenv("MAIL_TOOLS_CONFIG_FILE", str(config))
    tool_catalog.cache_clear()
    with pytest.raises(RuntimeError) as error:
        tool_catalog()
    assert str(error.value) == "Invalid mailbox tool configuration"


def test_no_default_and_empty_catalog_disable_automatic_selection(
    admin, monkeypatch, tmp_path
):
    config = tmp_path / "tools.toml"
    config.write_text("tools = []\n")
    monkeypatch.setenv("MAIL_TOOLS_CONFIG_FILE", str(config))
    tool_catalog.cache_clear()
    assert admin.get("/api/v1/mail-tools").json() == {"default": None, "items": []}
    response = admin.post(
        "/api/v1/account-imports",
        json={
            "tier": "5x",
            "text": "disabled@example.test----fictional----fictional",
        },
    )
    assert response.status_code == 201
    assert admin.get("/api/v1/accounts").json()[0]["mail_tool"] is None


def test_unselected_template_does_not_intercept_mail(
    admin, make_account, monkeypatch, register_tool
):
    class OtherTemplate:
        def matches(self, sender, subject):
            raise AssertionError("Unselected template must not run")

        def extract_code(self, raw):
            raise AssertionError("Unselected template must not run")

    monkeypatch.setitem(
        plugins.EMAIL_TEMPLATES,
        "other",
        plugins.EmailTemplatePlugin("other", "Other", lambda options: OtherTemplate()),
    )
    register_tool("other_tool", template="other", options={})
    identity = start(admin, make_account())
    mailbox = Mailbox()
    worker = EmailWorker(factory=lambda *args: mailbox)
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "found" and run.mail_tool == "mailcom"
    assert mailbox.marks == 1


@pytest.mark.parametrize("found", [False, True])
@pytest.mark.parametrize("change", ["options", "revision", "template", "backend"])
def test_reconfigured_tool_cancels_old_candidates_and_results(
    admin, make_account, register_tool, change, found
):
    account = make_account()
    identity = start(admin, account)
    lease = lease_next()
    assert save_candidate(*lease, Candidate("old-message", "123456", now()))
    if found:
        with Session.begin() as db:
            db.get(EmailCodeRun, identity).status = "found"
    base = f"/api/v1/accounts/{account['id']}"
    previous = admin.get(base + "/verification").json()["email_tool_revision"]
    definition = register_tool("mailcom")
    if change == "options":
        definition.options = {
            "sender": "changed@example.test",
            "subject_keyword": "Changed",
        }
    elif change == "revision":
        definition.revision += 1
    elif change == "template":
        definition.template = "missing_template"
    else:
        definition.backend = "missing_backend"
    assert admin.get(base + "/verification").json()["email_tool_revision"] != previous
    assert "code" not in admin.get(base + "/email-code-runs/" + identity).json()
    assert snapshot(*lease) is None
    assert lease_next() is None  # The reconciler also clears completed results.
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "cancelled" and run.code_encrypted is None
        assert run.message_id == "old-message"


def test_message_deduplication_shared_between_tools_on_same_backend(
    admin, make_account, register_tool
):
    register_tool("other_tool")
    account = make_account()
    start(admin, account)
    candidate = Candidate("same-mail", "123456", now())
    assert save_candidate(*lease_next(), candidate)
    assert (
        admin.patch(
            f"/api/v1/accounts/{account['id']}", json={"mail_tool": "other_tool"}
        ).status_code
        == 200
    )
    start(admin, account)
    lease = lease_next()
    assert "same-mail" in snapshot(*lease)["excluded"]
    assert not save_candidate(*lease, candidate)


def test_tool_change_during_mark_preflight_cannot_mark_or_publish(
    admin, make_account, register_tool
):
    identity = start(admin, make_account())
    mailbox = Mailbox()
    mailbox.before_mark = lambda: register_tool(
        "mailcom",
        options={
            "sender": "different@example.test",
            "subject_keyword": "Different",
        },
    )
    worker = EmailWorker(factory=lambda *args: mailbox)
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert mailbox.marks == 0
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "cancelled" and run.code_encrypted is None
