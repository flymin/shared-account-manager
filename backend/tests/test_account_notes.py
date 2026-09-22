from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.db import Session
from app.main import app
from app.models import AccountNote, Event, User


def claim(client, account):
    r = client.post(
        "/api/v1/claims",
        json={"account_id": account["id"], "acknowledge_warning": True},
    )
    assert r.status_code == 201, r.text
    return r.json()


def path(account):
    return f"/api/v1/accounts/{account['id']}/notes"


def test_notes_access_and_lifecycle(admin, make_user, make_account):
    user, client = make_user()
    _, outsider = make_user("outsider")
    account = make_account(users=[user["id"]])
    endpoint = path(account)
    assert TestClient(app).get(endpoint).status_code == 401
    assert outsider.get(endpoint).status_code == 404
    assert client.get(endpoint).json() == []
    assert client.post(endpoint, json={"content": "未领用"}).status_code == 403
    c = claim(client, account)
    added = client.post(endpoint, json={"content": "  使用情况\n第二行  "})
    assert added.status_code == 201
    note = added.json()
    assert note["content"] == "使用情况\n第二行"
    assert note["author_username"] == user["username"]
    assert note["created_at"]
    assert client.get(endpoint).json() == [note]
    assert admin.get("/api/v1/accounts").json()[0]["note_count"] == 1
    assert client.get("/api/v1/claims").json()[0]["account"]["note_count"] == 1
    assert (
        client.put(
            f"/api/v1/claims/{c['id']}/return", json={"kind": "normal", "quota": 50, "reset_at": "2030-01-01T00:00:00+00:00"}
        ).status_code
        == 200
    )
    assert client.post(endpoint, json={"content": "已归还"}).status_code == 403
    assert client.get(endpoint).json() == [note]
    assert admin.post(endpoint, json={"content": "管理员补充"}).status_code == 201
    for method in ("put", "patch", "delete"):
        assert getattr(admin, method)(endpoint + f"/{note['id']}").status_code == 404
        assert getattr(admin, method)(endpoint).status_code == 405
    with Session() as db:
        event = db.scalar(
            select(Event).where(
                Event.kind == "account_note_added", Event.actor_id == user["id"]
            )
        )
        assert event.details == {"note_id": note["id"]}
    assert (
        admin.request(
            "DELETE",
            f"/api/v1/accounts/{account['id']}",
            json={"reason": "test removal"},
        ).status_code
        == 200
    )
    assert admin.get(endpoint).status_code == 404
    assert admin.post(endpoint, json={"content": "删除后"}).status_code == 404
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AccountNote)) == 2


def test_notes_visibility_and_revocation(admin, make_user, make_account):
    user, client = make_user()
    other, viewer = make_user("viewer")
    account = make_account(users=[user["id"], other["id"]])
    endpoint = path(account)
    c = claim(client, account)
    assert client.post(endpoint, json={"content": "共享情况"}).status_code == 201
    assert viewer.get(endpoint).json()[0]["content"] == "共享情况"
    assert viewer.post(endpoint, json={"content": "非领用"}).status_code == 403
    assert (
        admin.patch(
            f"/api/v1/accounts/{account['id']}", json={"user_ids": []}
        ).status_code
        == 200
    )
    assert viewer.get(endpoint).status_code == 404
    # Existing valid claims survive ACL changes.
    assert client.get(endpoint).status_code == 200
    assert client.post(endpoint, json={"content": "当前使用者"}).status_code == 201
    assert (
        admin.put(
            f"/api/v1/claims/{c['id']}/revocation", json={"reason": "test recall"}
        ).status_code
        == 200
    )
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint, json={"content": "回收后"}).status_code == 403


def test_note_validation_and_csrf(admin, make_account):
    account = make_account()
    endpoint = path(account)
    for content in ("", " \n\t ", "字" * 201, "😀" * 201):
        assert admin.post(endpoint, json={"content": content}).status_code == 422
    for content in ("字" * 200, "😀" * 200, "<script>literal text</script>"):
        assert admin.post(endpoint, json={"content": content}).status_code == 201
    assert (
        admin.post(
            endpoint, json={"content": "x", "created_at": "2020-01-01"}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            endpoint, json={"content": "x"}, headers={"X-CSRF-Token": ""}
        ).status_code
        == 403
    )
    assert admin.get(endpoint + "?limit=101").status_code == 422
    assert admin.get(endpoint + "?before=0").status_code == 422


def test_note_pagination_and_author_retention(admin, make_user, make_account):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    claim(client, account)
    endpoint = path(account)
    ids = [
        client.post(endpoint, json={"content": f"记录{i}"}).json()["id"]
        for i in range(7)
    ]
    page = admin.get(endpoint + "?limit=5").json()
    assert [n["id"] for n in page] == list(reversed(ids))[:5]
    # A new insertion cannot shift an existing cursor page.
    assert admin.post(endpoint, json={"content": "新记录"}).status_code == 201
    tail = admin.get(endpoint + f"?limit=5&before={page[-1]['id']}").json()
    assert [n["id"] for n in tail] == list(reversed(ids))[-2:]
    with Session.begin() as db:
        db.get(User, user["id"]).deleted = True
    assert admin.get(endpoint).json()[-1]["author_username"] == user["username"]
