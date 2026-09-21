"""Password policies are exercised only with fictional credentials."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.db import Session
from app.main import app
from app.models import User
from app.password_policy import MIN_LENGTH, MAX_LENGTH, password_error
from conftest import PASSWORD, login

STRONG = "R7!qL2#vN8@zW4$"


@pytest.mark.parametrize(
    "value",
    [
        STRONG[: MIN_LENGTH - 1],
        "a" * MIN_LENGTH,
        "Password123456!",
        "12345678901234567890",
        "qwertyuiopasdfghjkl",
        "Qwerty123!Qwerty123!",
        " " * MIN_LENGTH,
        STRONG * 5,
    ],
)
def test_weak_passwords_are_rejected(value):
    assert password_error(value)


@pytest.mark.parametrize(
    "value",
    [
        STRONG,
        "cobalt otters weave velvet kites",
        "风铃咖啡北极苔原星河松针蜻蜓雪山",
        STRONG + " " + "p7#Kx2!Nw9$Qr6%" * 3,
    ],
)
def test_long_passphrases_unicode_and_spaces_are_supported(value):
    assert MIN_LENGTH <= len(value) <= MAX_LENGTH
    assert password_error(value) is None


def test_username_and_display_name_are_used_as_guessable_inputs():
    assert password_error("synthetic.operator2026!", username="synthetic.operator")
    assert password_error("SyntheticTester2026!", display_name="SyntheticTester")


def test_create_reset_and_self_change_share_policy_without_partial_writes(
    admin, make_user
):
    weak = "qwertyuiopasdfghjkl"
    response = admin.post(
        "/api/v1/users",
        json={
            "username": "weak-example",
            "display_name": "Example",
            "password": weak,
        },
    )
    assert response.status_code == 422 and weak not in response.text
    assert not any(
        u["username"] == "weak-example" for u in admin.get("/api/v1/users").json()
    )
    user, client = make_user("policy-example")
    with Session() as db:
        original = db.get(User, user["id"]).password_hash
    response = admin.patch(
        f"/api/v1/users/{user['id']}",
        json={
            "password": weak,
            "display_name": "Should not persist",
        },
    )
    assert response.status_code == 422 and weak not in response.text
    response = client.put(
        "/api/v1/auth/password",
        json={
            "current_password": PASSWORD,
            "new_password": weak,
        },
    )
    assert response.status_code == 422 and weak not in response.text
    assert client.get("/api/v1/accounts").status_code == 200
    with Session() as db:
        saved = db.get(User, user["id"])
        assert saved.password_hash == original
        assert saved.display_name == user["display_name"]
        assert not saved.must_change_password
    # An unchanged password is not revalidated on profile-only edits.
    assert (
        admin.patch(
            f"/api/v1/users/{user['id']}", json={"display_name": "Example"}
        ).status_code
        == 200
    )
    assert (
        admin.patch(
            f"/api/v1/users/{user['id']}", json={"password": STRONG}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    reset = login(user["username"], STRONG)
    assert reset.get("/api/v1/accounts").status_code == 403
    assert (
        reset.put(
            "/api/v1/auth/password",
            json={
                "current_password": STRONG,
                "new_password": "cobalt otters weave velvet kites",
            },
        ).status_code
        == 200
    )
    assert reset.get("/api/v1/auth/me").status_code == 401
    assert (
        login(user["username"], "cobalt otters weave velvet kites")
        .get("/api/v1/accounts")
        .status_code
        == 200
    )


def test_length_rejection_is_redacted_and_legacy_login_still_works(admin, make_user):
    user, _ = make_user("legacy-example")
    legacy = "old-test-10"
    with Session.begin() as db:
        db.get(User, user["id"]).password_hash = auth.hasher.hash(legacy)
    client = login(user["username"], legacy)
    assert client.get("/api/v1/accounts").status_code == 200
    for value in (STRONG[: MIN_LENGTH - 1], "Z" * (MAX_LENGTH + 1)):
        response = client.put(
            "/api/v1/auth/password",
            json={
                "current_password": legacy,
                "new_password": value,
            },
        )
        assert response.status_code == 422
        assert value not in response.text and legacy not in response.text
    assert (
        client.put(
            "/api/v1/auth/password",
            json={
                "current_password": legacy,
                "new_password": STRONG,
            },
        ).status_code
        == 200
    )


def test_bootstrap_rejects_weak_password_and_preserves_existing_users(monkeypatch):
    from app import cli
    from app.models import Base
    from sqlalchemy import text

    with Session.begin() as db:
        tables = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(cli, "secret", lambda _: "a" * MIN_LENGTH)
    with pytest.raises(RuntimeError, match="password policy"):
        cli.bootstrap()
    with Session() as db:
        assert db.scalar(select(User)) is None
    monkeypatch.setattr(cli, "secret", lambda _: STRONG)
    cli.bootstrap()
    with Session() as db:
        original = db.scalar(select(User)).password_hash
    monkeypatch.setattr(cli, "secret", lambda _: "weak")
    cli.bootstrap()
    with Session() as db:
        assert db.scalar(select(User)).password_hash == original


def test_anonymous_requests_cannot_reach_password_strength_estimation(monkeypatch):
    import importlib

    main = importlib.import_module("app.main")

    def forbidden(*args, **kwargs):
        pytest.fail("Strength estimation must happen after authentication")

    monkeypatch.setattr(main, "password_error", forbidden)
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/users",
            json={
                "username": "outsider",
                "display_name": "Example",
                "password": STRONG,
            },
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/api/v1/auth/password",
            json={
                "current_password": STRONG,
                "new_password": STRONG,
            },
        ).status_code
        == 401
    )
