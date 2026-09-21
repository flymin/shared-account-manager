import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.auth import hasher
from app.db import Session, engine
from app.main import app
from app.models import Base, Settings, User
from app.plugins.tools import ToolDefinition, tool_catalog

PASSWORD = "Fictional-Test-Password-2026!"


@pytest.fixture
def register_tool(monkeypatch):
    def register(
        identity, backend="mailcom", template="six_digit_code", name=None, options=None
    ):
        definition = ToolDefinition.model_validate(
            {
                "id": identity,
                "name": name or identity,
                "backend": backend,
                "template": template,
                "options": options
                if options is not None
                else {
                    "sender": {"env": "MAIL_CODE_SENDER"},
                    "subject_keyword": {"env": "MAIL_CODE_SUBJECT_KEYWORD"},
                },
            }
        )
        catalog = tool_catalog()
        others = [tool for tool in catalog.tools if tool.id != identity]
        monkeypatch.setattr(catalog, "tools", [*others, definition])
        return definition

    return register


@pytest.fixture(autouse=True)
def database(monkeypatch):
    from app import config

    assert engine.url.database.endswith("_test"), (
        "Tests require an isolated *_test database"
    )
    monkeypatch.setenv("MAIL_CODE_SENDER", "noreply@login.example.test")
    monkeypatch.setenv("MAIL_CODE_SUBJECT_KEYWORD", "ExampleService")
    monkeypatch.setattr(config, "LOGIN_RATE_PER_MINUTE", 30)
    monkeypatch.setattr(config, "LOGIN_BURST", 10)
    # Ignore deployment-specific tool catalogs in isolated tests.
    monkeypatch.delenv("MAIL_TOOLS_CONFIG_FILE", raising=False)
    tool_catalog.cache_clear()
    with Session.begin() as db:
        tables = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.add(Settings(id=1))
        db.add(
            User(
                username="admin",
                display_name="测试管理员",
                password_hash=hasher.hash(PASSWORD),
                role="admin",
                must_change_password=False,
            )
        )
    yield
    tool_catalog.cache_clear()


def login(username, password=PASSWORD):
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Login-Request": "1"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def admin():
    return login("admin")


@pytest.fixture
def make_user(admin):
    def create(username="alice", groups=None, limit=None, role="user"):
        response = admin.post(
            "/api/v1/users",
            json={
                "username": username,
                "display_name": username,
                "password": PASSWORD,
                "group_ids": groups or [],
                "claim_limit": limit,
                "role": role,
            },
        )
        assert response.status_code == 201, response.text
        user = response.json()
        with Session.begin() as db:
            db.get(User, user["id"]).must_change_password = False
        return user, login(username)

    return create


@pytest.fixture
def make_account(admin):
    def create(
        email="account@example.test",
        users=None,
        groups=None,
        capacity=None,
        expires_at=None,
        quota_reset_interval_days=None,
        mail_tool="mailcom",
    ):
        response = admin.post(
            "/api/v1/account-imports",
            json={
                "text": f"{email}----fictional-password----fictional-auth",
                "tier": "5x",
                "user_ids": users or [],
                "group_ids": groups or [],
                "capacity": capacity,
                "expires_at": expires_at,
                "quota_reset_interval_days": quota_reset_interval_days,
                "mail_tool": mail_tool,
            },
        )
        assert response.status_code == 201, response.text
        return next(
            a for a in admin.get("/api/v1/accounts").json() if a["email"] == email
        )

    return create
