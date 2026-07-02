"""Authentication API for the current family account boundary."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..account import AccountStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

_accounts: Optional[AccountStore] = None


def bind(accounts: AccountStore) -> None:
    global _accounts
    _accounts = accounts


class LoginIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=40)
    role: str = Field(..., description="parent/elder")
    elder_id: str = Field("elder-001", min_length=1, max_length=80)
    family_id: str = Field("family-default", min_length=1, max_length=80)


def _require_accounts() -> AccountStore:
    if _accounts is None:
        raise HTTPException(status_code=404, detail="账号服务未启用")
    return _accounts


@router.post("/login")
async def login(body: LoginIn) -> dict:
    try:
        return {"ok": True, **await _require_accounts().login_demo(**body.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/me")
async def me(authorization: str = Header("")) -> dict:
    token = authorization.replace("Bearer ", "").strip()
    session = await _require_accounts().session(token)
    if not session:
        raise HTTPException(status_code=401, detail="未登录")
    return {"ok": True, "session": session}
