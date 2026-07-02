"""Minimal family-boundary auth middleware.

This is intentionally small: demo mode stays frictionless, production can set
AUTH_REQUIRED=1 and FAMILY_API_TOKEN to stop naked elder_id routes from being
publicly readable. A real account system can replace this boundary later while
keeping route handlers unchanged.
"""
from __future__ import annotations

import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .account import AccountStore


class FamilyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        required: bool,
        token: str,
        account_store: AccountStore | None = None,
    ) -> None:
        super().__init__(app)
        self._required = required
        self._token = token
        self._accounts = account_store

    async def dispatch(self, request: Request, call_next):
        if not self._required:
            return await call_next(request)
        path = request.url.path
        if path in ("/healthz",) or path.startswith(("/shared/", "/elder/", "/parent/", "/admin/")):
            return await call_next(request)
        if path.startswith("/api/auth/"):
            return await call_next(request)
        if path.startswith(("/api/", "/ws/")) and not await self._authorized(request):
            return JSONResponse({"detail": "未授权的家庭访问"}, status_code=401)
        return await call_next(request)

    async def _authorized(self, request: Request) -> bool:
        supplied = request.headers.get("X-Family-Token") or request.query_params.get("family_token")
        if supplied and supplied == self._token:
            return True
        if self._accounts is None:
            return False
        bearer = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        token = bearer or request.query_params.get("session_token", "")
        elder_id, role = _route_requirement(request.url.path)
        if not elder_id or not role:
            return False
        return await self._accounts.allowed(token, elder_id=elder_id, required_role=role)


def _route_requirement(path: str) -> tuple[str, str] | tuple[None, None]:
    parent = re.match(r"^/api/parent/([^/]+)", path)
    if parent:
        return parent.group(1), "parent"
    elder = re.match(r"^/api/elder/([^/]+)", path)
    if elder:
        return elder.group(1), "elder"
    character = re.match(r"^/api/character/([^/]+)", path)
    if character:
        return character.group(1), "parent"
    ws = re.match(r"^/ws/elder/([^/]+)", path)
    if ws:
        return ws.group(1), "elder"
    return None, None
