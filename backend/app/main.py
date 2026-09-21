from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, func, text, or_
from sqlalchemy.exc import IntegrityError
from . import auth, config, domain as d, schemas as s
from .db import get_db
from .models import (
    User,
    Group,
    UserGroup,
    Account,
    AccountGroup,
    AccountUser,
    Claim,
    Event,
    Settings,
    LoginSession,
    now,
)
from .config import cipher
from .verification_api import router as verification_router
from .verification import cancel_account_runs
from .plugins import mail_backend_options
from .password_policy import password_error

app = FastAPI(
    title="Account Manager",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(verification_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic input/ctx can contain submitted passwords: never echo them.
    return JSONResponse(
        status_code=422,
        content={
            "detail": "输入无效，请检查必填项、范围和时间格式",
            "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
        },
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(
        status_code=409, content={"detail": "数据已存在或状态发生变化，请刷新重试"}
    )


@app.middleware("http")
async def headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = config.PUBLIC_ORIGIN or str(request.base_url).rstrip("/")
        if origin and origin != expected_origin:
            return JSONResponse(status_code=403, content={"detail": "不允许跨站请求"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ready")
def ready(db=Depends(get_db, scope="function")):
    db.execute(text("SELECT 1"))
    if not db.get(Settings, 1):
        raise HTTPException(503, "数据库尚未初始化")
    return {"status": "ready"}


@app.post("/api/v1/auth/login")
def login(
    data: s.LoginInput,
    request: Request,
    response: Response,
    db=Depends(get_db, scope="function"),
):
    if request.headers.get("x-login-request") != "1":
        raise HTTPException(403, "请求校验失败")
    result, error, code = auth.login(db, data, response)
    if error:
        response.status_code = code
        return {"detail": error}
    user, session = result
    return {"user": d.user_dto(db, user), "csrf_token": session.csrf_token}


@app.get("/api/v1/auth/me")
def me(
    request: Request,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return {
        "user": d.user_dto(db, user),
        "csrf_token": request.state.login_session.csrf_token,
    }


@app.post("/api/v1/auth/logout")
def logout(
    request: Request,
    response: Response,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    db.delete(request.state.login_session)
    response.delete_cookie(
        auth.COOKIE, path="/", secure=config.PUBLIC_ORIGIN.startswith("https://")
    )
    return {"ok": True}


@app.put("/api/v1/auth/password")
def change_password(
    data: s.PasswordInput,
    response: Response,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    if not auth.check_password(user.password_hash, data.current_password):
        raise HTTPException(400, "当前密码不正确")
    if data.current_password == data.new_password:
        raise HTTPException(422, "新密码必须与当前密码不同")
    error = password_error(
        data.new_password, username=user.username, display_name=user.display_name
    )
    if error:
        raise HTTPException(422, error)
    user.password_hash = auth.hasher.hash(data.new_password)
    user.must_change_password = False
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    d.event(db, "password_changed", user)
    response.delete_cookie(
        auth.COOKIE, path="/", secure=config.PUBLIC_ORIGIN.startswith("https://")
    )
    return {"ok": True}


@app.get("/api/v1/accounts")
def accounts(
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
    scope: str = Query("all", pattern="^(all|hall)$"),
):
    query = select(Account).where(Account.deleted.is_(False))
    if scope == "hall":
        query = query.where(
            Account.disabled.is_(False),
            or_(Account.expires_at.is_(None), Account.expires_at > now()),
        )
    if user.role != "admin":
        query = query.where(d.visible_clause(user.id))
    return [
        d.account_dto(db, user, a)
        for a in db.scalars(query.order_by(Account.created_at.desc()))
    ]


@app.get("/api/v1/accounts/{account_id}")
def account(
    account_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return d.account_dto(db, user, d.get_account(db, user, account_id))


@app.get("/api/v1/accounts/{account_id}/credentials")
def credentials(
    account_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    a = d.get_account(db, user, account_id, feedback=True)
    return {
        "email": a.email,
        "password": cipher().decrypt(a.password_encrypted.encode()).decode(),
        "auth_password": cipher().decrypt(a.auth_password_encrypted.encode()).decode(),
    }


@app.get("/api/v1/accounts/{account_id}/events")
def events(
    account_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
    before: int | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    d.get_account(db, user, account_id)
    query = select(Event).where(Event.account_id == account_id)
    if before is not None:
        query = query.where(Event.id < before)
    return [
        d.serialize_event(db, e)
        for e in db.scalars(query.order_by(Event.id.desc()).limit(limit))
    ]


@app.get("/api/v1/mail-backends")
def mail_backends(user=Depends(auth.admin)):
    return mail_backend_options()


@app.post("/api/v1/account-imports/preview")
def preview_import(
    data: s.ImportInput, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    rows, errors = d.parse_import(db, data)
    return {
        "rows": [
            {
                "line": row[0],
                "email": row[1],
                "tier": data.tier,
                "mail_backend": data.mail_backend,
                "mail_backend_name": d.mail_backend_name(data.mail_backend),
            }
            for row in rows
        ],
        "errors": errors,
    }


@app.post("/api/v1/account-imports", status_code=201)
def import_accounts(
    data: s.ImportInput, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    result = d.import_accounts(db, user, data)
    return {"count": len(result)}


@app.patch("/api/v1/accounts/{account_id}")
def update_account(
    account_id: str,
    data: s.AccountPatch,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    a = d.get_account(db, user, account_id)
    values = data.model_dump(exclude_unset=True)
    if "mail_backend" in values:
        d.validate_mail_backend(data.mail_backend)
    if ("mail_backend" in values and a.mail_backend != data.mail_backend) or values.get(
        "auth_password"
    ):
        cancel_account_runs(db, a.id)
        a.mail_config_version += 1
    for field in (
        "tier",
        "capacity",
        "expires_at",
        "mail_backend",
        "quota_reset_interval_days",
    ):
        if field in values:
            if field == "tier" and values[field] is None:
                d.fail("档位不能为空", 422)
            setattr(a, field, values[field])
    for field, dest in (
        ("password", "password_encrypted"),
        ("auth_password", "auth_password_encrypted"),
    ):
        if values.get(field):
            setattr(a, dest, cipher().encrypt(values[field].encode()).decode())
    d.set_acl(db, a, data.group_ids, data.user_ids)
    d.event(db, "account_updated", user, a, {"fields": list(values.keys())})
    db.flush()
    return d.account_dto(db, user, a)


@app.put("/api/v1/accounts/{account_id}/activation")
def account_activation(
    account_id: str,
    data: s.ActivationInput,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    a = d.get_account(db, user, account_id)
    disabled = not data.enabled
    if a.disabled != disabled:
        a.disabled = disabled
        d.event(db, "account_enabled" if data.enabled else "account_disabled", user, a)
    db.flush()
    return d.account_dto(db, user, a)


@app.delete("/api/v1/accounts/{account_id}")
def delete_account(
    account_id: str,
    data: s.RevokeInput,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    a = db.get(Account, account_id)
    if not a:
        d.fail("账号不存在", 404)
    if not data.reason.strip():
        d.fail("请填写删除原因", 422)
    if not a.deleted:
        for c in db.scalars(
            select(Claim).where(Claim.account_id == a.id, Claim.returned_at.is_(None))
        ):
            d.invalidate(db, c, user, data.reason.strip(), "deleted")
        a.deleted = True
        d.event(db, "account_deleted", user, a, {"reason": data.reason.strip()})
    return {"ok": True}


@app.post("/api/v1/accounts/{account_id}/quota-reports", status_code=201)
def quota_report(
    account_id: str,
    data: s.QuotaInput,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    a = d.get_account(db, user, account_id, feedback=True)
    d.update_quota(
        db,
        a,
        user,
        data.quota,
        data.reset_at,
        replace_reset="reset_at" in data.model_fields_set,
    )
    return {"ok": True}


@app.post("/api/v1/accounts/{account_id}/health-reports", status_code=201)
def health_report(
    account_id: str,
    data: s.HealthInput,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    a = d.get_account(db, user, account_id, feedback=True)
    if data.version != a.health_version:
        d.fail("异常信息已更新，请刷新后重试")
    if data.action == "clear":
        d.clear_health(db, a, user)
    else:
        d.health_report(db, a, user, data.categories, data.note)
    return {"ok": True}


@app.get("/api/v1/claims")
def claims(
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
    scope: str = Query("mine", pattern="^(mine|all)$"),
    history: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    q: str = Query("", max_length=254),
    state: str = Query("all", pattern="^(all|active|pending)$"),
    tier: str | None = Query(None, pattern="^(5x|20x)$"),
    group_id: str | None = None,
):
    query = (
        select(Claim)
        .join(Account, Claim.account_id == Account.id)
        .join(User, Claim.user_id == User.id)
    )
    if q:
        query = query.where(
            or_(
                func.lower(Account.email).contains(q.lower(), autoescape=True),
                func.lower(User.display_name).contains(q.lower(), autoescape=True),
                func.lower(User.username).contains(q.lower(), autoescape=True),
            )
        )
    if state == "active":
        query = query.where(Claim.invalidated_at.is_(None))
    elif state == "pending":
        query = query.where(Claim.invalidated_at.is_not(None))
    if tier:
        query = query.where(Account.tier == tier)
    if group_id:
        query = query.where(
            Account.id.in_(
                select(AccountGroup.account_id).where(AccountGroup.group_id == group_id)
            )
        )
    if scope == "all":
        if user.role != "admin":
            d.fail("需要管理员权限", 403)
    else:
        query = query.where(Claim.user_id == user.id)
    if history:
        query = query.where(Claim.returned_at.is_not(None))
    else:
        query = query.where(Claim.returned_at.is_(None))
    return [
        d.claim_dto(db, user, c)
        for c in db.scalars(
            query.order_by(Claim.claimed_at.desc()).offset(offset).limit(limit)
        )
    ]


@app.post("/api/v1/claims", status_code=201)
def claim(
    data: s.ClaimInput,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return d.claim_dto(db, user, d.create_claim(db, user, data))


@app.put("/api/v1/claims/{claim_id}/return")
def return_claim(
    claim_id: str,
    data: s.ReturnInput,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return d.claim_dto(db, user, d.return_claim(db, user, claim_id, data))


@app.put("/api/v1/claims/{claim_id}/revocation")
def revoke(
    claim_id: str,
    data: s.RevokeInput,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    c = db.get(Claim, claim_id)
    if not c:
        d.fail("领用记录不存在", 404)
    if not data.reason.strip():
        d.fail("请填写回收原因", 422)
    d.invalidate(db, c, user, data.reason.strip())
    return {"ok": True}


@app.get("/api/v1/users")
def users(user=Depends(auth.admin), db=Depends(get_db, scope="function")):
    return [
        d.user_dto(db, u)
        for u in db.scalars(
            select(User).where(User.deleted.is_(False)).order_by(User.created_at)
        )
    ]


@app.post("/api/v1/users", status_code=201)
def create_user(
    data: s.UserInput, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    if not data.display_name.strip():
        d.fail("姓名不能为空", 422)
    error = password_error(
        data.password, username=data.username, display_name=data.display_name
    )
    if error:
        raise HTTPException(422, error)
    u = User(
        username=data.username.lower(),
        display_name=data.display_name.strip(),
        password_hash=auth.hasher.hash(data.password),
        role=data.role,
        claim_limit=data.claim_limit,
    )
    db.add(u)
    db.flush()
    d.set_links(db, UserGroup, "user_id", u.id, data.group_ids, "group_id", Group)
    d.event(
        db, "user_created", user, details={"username": u.username}, target_user=u.id
    )
    return d.user_dto(db, u)


def protect_admin(db, u):
    if (
        u.role == "admin"
        and db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.deleted.is_(False))
        )
        <= 1
    ):
        d.fail("不能删除或降级最后一个管理员")


@app.patch("/api/v1/users/{user_id}")
def update_user(
    user_id: str,
    data: s.UserPatch,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    u = db.get(User, user_id)
    if not u or u.deleted:
        d.fail("用户不存在", 404)
    if data.password is not None:
        error = password_error(
            data.password,
            username=u.username,
            display_name=data.display_name
            if data.display_name is not None
            else u.display_name,
        )
        if error:
            raise HTTPException(422, error)
    values = data.model_dump(exclude_unset=True)
    if data.role == "user":
        protect_admin(db, u)
    if data.display_name is not None:
        if not data.display_name.strip():
            d.fail("姓名不能为空", 422)
        u.display_name = data.display_name.strip()
    if data.role:
        u.role = data.role
    if "claim_limit" in values:
        u.claim_limit = data.claim_limit
    if data.password:
        u.password_hash = auth.hasher.hash(data.password)
        u.must_change_password = True
        db.execute(delete(LoginSession).where(LoginSession.user_id == u.id))
    d.set_links(db, UserGroup, "user_id", u.id, data.group_ids, "group_id", Group)
    d.event(
        db, "user_updated", user, details={"fields": list(values)}, target_user=u.id
    )
    return d.user_dto(db, u)


@app.delete("/api/v1/users/{user_id}")
def delete_user(
    user_id: str, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    u = db.get(User, user_id)
    if not u:
        d.fail("用户不存在", 404)
    if not u.deleted:
        protect_admin(db, u)
        u.deleted = True
        db.execute(delete(AccountUser).where(AccountUser.user_id == u.id))
        db.execute(delete(UserGroup).where(UserGroup.user_id == u.id))
        for c in db.scalars(
            select(Claim).where(Claim.user_id == u.id, Claim.returned_at.is_(None))
        ):
            c.returned_at, c.return_kind = now(), "user_deleted"
            d.event(
                db,
                "returned",
                user,
                db.get(Account, c.account_id),
                {"claim_id": c.id, "kind": "user_deleted"},
                target_user=u.id,
            )
        db.execute(delete(LoginSession).where(LoginSession.user_id == u.id))
        d.event(db, "user_deleted", user, target_user=u.id)
    return {"ok": True}


@app.get("/api/v1/groups")
def groups(user=Depends(auth.admin), db=Depends(get_db, scope="function")):
    return [d.group_dto(db, g) for g in db.scalars(select(Group).order_by(Group.name))]


@app.post("/api/v1/groups", status_code=201)
def create_group(
    data: s.GroupInput, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    if not data.name.strip():
        d.fail("组名不能为空", 422)
    g = Group(name=data.name.strip())
    db.add(g)
    db.flush()
    d.set_group_members(db, g, data.user_ids, user)
    d.event(db, "group_created", user, details={"name": g.name})
    return d.group_dto(db, g)


@app.patch("/api/v1/groups/{group_id}")
def update_group(
    group_id: str,
    data: s.GroupInput,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    g = db.get(Group, group_id)
    if not g:
        d.fail("用户组不存在", 404)
    if not data.name.strip():
        d.fail("组名不能为空", 422)
    g.name = data.name.strip()
    d.set_group_members(db, g, data.user_ids, user)
    d.event(db, "group_updated", user, details={"name": g.name})
    return d.group_dto(db, g)


@app.delete("/api/v1/groups/{group_id}")
def delete_group(
    group_id: str, user=Depends(auth.admin), db=Depends(get_db, scope="function")
):
    g = db.get(Group, group_id)
    if not g:
        d.fail("用户组不存在", 404)
    d.event(db, "group_deleted", user, details={"name": g.name})
    db.delete(g)
    return {"ok": True}


@app.get("/api/v1/settings")
def get_settings(user=Depends(auth.admin), db=Depends(get_db, scope="function")):
    return {
        field: getattr(d.settings(db), field) for field in s.SettingsInput.model_fields
    }


@app.put("/api/v1/settings")
def put_settings(
    data: s.SettingsInput,
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    cfg = d.settings(db)
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(cfg, key, value)
    d.event(db, "settings_updated", user, details=changes)
    return {field: getattr(cfg, field) for field in s.SettingsInput.model_fields}


@app.get("/api/v1/audit-subjects")
def audit_subjects(user=Depends(auth.admin), db=Depends(get_db, scope="function")):
    return {
        "accounts": [
            {"id": a.id, "email": a.email, "deleted": a.deleted}
            for a in db.scalars(select(Account).order_by(Account.email))
        ],
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "deleted": u.deleted,
            }
            for u in db.scalars(select(User).order_by(User.username))
        ],
    }


@app.get("/api/v1/audit-events")
def audit(
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
    before: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    account_id: str | None = None,
    user_id: str | None = None,
):
    query = select(Event)
    if before is not None:
        query = query.where(Event.id < before)
    if account_id:
        query = query.where(Event.account_id == account_id)
    if user_id:
        query = query.where(
            (Event.actor_id == user_id) | (Event.target_user_id == user_id)
        )
    return [
        d.serialize_event(db, e, identities=True)
        for e in db.scalars(query.order_by(Event.id.desc()).limit(limit))
    ]


@app.get("/api/v1/overview")
def overview(user=Depends(auth.admin), db=Depends(get_db, scope="function")):
    stamp = now()
    accounts = list(db.scalars(select(Account).where(Account.deleted.is_(False))))
    occupancy = dict(
        db.execute(
            select(Claim.account_id, func.count())
            .where(Claim.returned_at.is_(None), Claim.invalidated_at.is_(None))
            .group_by(Claim.account_id)
        ).all()
    )
    cfg = d.settings(db)
    default_capacity = cfg.account_capacity
    return {
        "accounts": len(accounts),
        "available": sum(
            not a.disabled
            and occupancy.get(a.id, 0) < (a.capacity or default_capacity)
            and (a.expires_at is None or a.expires_at > stamp)
            for a in accounts
        ),
        "abnormal": sum(a.health == "abnormal" for a in accounts),
        "disabled": sum(a.disabled for a in accounts),
        "depleted": sum(d.quota_depleted(a.quota, cfg) for a in accounts),
        "expired": sum(
            a.expires_at is not None and a.expires_at <= stamp for a in accounts
        ),
        "active_claims": sum(occupancy.values()),
        "pending_claims": db.scalar(
            select(func.count())
            .select_from(Claim)
            .where(Claim.returned_at.is_(None), Claim.invalidated_at.is_not(None))
        ),
    }
