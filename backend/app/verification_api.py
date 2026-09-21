from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from . import auth, domain as d, totp, verification as v
from .config import cipher
from .db import get_db, Session
from .models import TwoFactor, now
from .schemas import Input

router = APIRouter(prefix="/api/v1/accounts/{account_id}")
Kind = Literal["service"]


class StartInput(Input):
    id: UUID = Field(
        description="Client-generated ID permits cancellation of late start responses"
    )


@router.get("/verification")
def verification_status(
    account_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return v.status(db, user, account_id)


@router.post("/email-code-runs", status_code=201)
def start(
    account_id: str,
    data: StartInput,
    request: Request,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return v.start_run(db, user, request.state.login_session, account_id, str(data.id))


@router.get("/email-code-runs/{run_id}")
def read(
    account_id: str,
    run_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return v.read_run(db, user, account_id, run_id)


@router.delete("/email-code-runs/{run_id}")
def cancel(
    account_id: str,
    run_id: str,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    return v.cancel_run(db, user, account_id, run_id)


@router.put("/two-factor/{kind}")
async def upload(
    account_id: str, kind: Kind, request: Request, version: int = Query(ge=0)
):
    # Authenticate before reading the upload, but do not hold a connection or the
    # global business write lock while a slow client streams/decodes an image.
    def authorize():
        with Session.begin() as db:
            user = auth.authenticate_request(request, db, lock_writes=False)
            auth.admin(user)
            d.get_account(db, user, account_id, feedback=True)

    await run_in_threadpool(authorize)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > totp.MAX_IMAGE_BYTES:
            raise HTTPException(413, "二维码图片不得超过 1 MiB")
        data.extend(chunk)
    try:
        uri = await run_in_threadpool(totp.decode_image, bytes(data))
    except totp.InvalidQR as exc:
        raise HTTPException(422, str(exc)) from None

    def save():
        with Session.begin() as db:
            user = auth.authenticate_request(request, db)
            return v.save_totp(db, user, account_id, kind, uri, version)

    return await run_in_threadpool(save)


@router.delete("/two-factor/{kind}")
def remove(
    account_id: str,
    kind: Kind,
    version: int = Query(ge=0),
    user=Depends(auth.admin),
    db=Depends(get_db, scope="function"),
):
    return v.remove_totp(db, user, account_id, kind, version)


@router.get("/two-factor/{kind}/code")
def get_code(
    account_id: str,
    kind: Kind,
    user=Depends(auth.authenticate),
    db=Depends(get_db, scope="function"),
):
    d.get_account(db, user, account_id, feedback=True)
    config = db.get(TwoFactor, (account_id, kind))
    if config is None or not config.uri_encrypted:
        raise HTTPException(404, "尚未配置此类 2FA")
    result = totp.current_code(
        cipher().decrypt(config.uri_encrypted.encode()).decode(), now()
    )
    result["version"] = config.version
    return result
