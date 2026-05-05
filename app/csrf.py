"""CSRF double-submit cookie protection.

The middleware:
- Issues a non-HttpOnly `csrf_token` cookie on every response if one is not present
  on the request. The SPA reads this cookie and echoes it back via the `X-CSRF-Token`
  header on state-changing requests.
- Rejects state-changing requests (POST/PUT/PATCH/DELETE) on protected paths when
  the header is missing or does not match the cookie.

OAuth login/callback paths are exempt because the browser navigates to them as a
top-level redirect (no JS to attach a header) and the OAuth state itself defends
against CSRF on those routes.
"""

import secrets
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

EXEMPT_PATH_PREFIXES = (
    "/auth/github/login",
    "/auth/github/callback",
    "/auth/cli/",
)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _is_protected(path: str) -> bool:
    if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
        return False
    return path.startswith("/api/") or path.startswith("/auth/")


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        cookie_secure: bool = False,
        cookie_samesite: str = "lax",
        cookie_max_age: int = 60 * 60 * 8,
        exempt_prefixes: Iterable[str] = EXEMPT_PATH_PREFIXES,
    ):
        super().__init__(app)
        self.cookie_secure = cookie_secure
        self.cookie_samesite = cookie_samesite
        self.cookie_max_age = cookie_max_age
        self.exempt_prefixes = tuple(exempt_prefixes)

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.exempt_prefixes)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)

        if (
            method in UNSAFE_METHODS
            and not self._is_exempt(path)
            and (path.startswith("/api/") or path.startswith("/auth/"))
        ):
            header_token = request.headers.get(CSRF_HEADER_NAME)
            if (
                not cookie_token
                or not header_token
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "CSRF token missing or invalid"},
                )

        response = await call_next(request)

        if not cookie_token:
            new_token = _generate_token()
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=new_token,
                httponly=False,
                secure=self.cookie_secure,
                samesite=self.cookie_samesite,
                max_age=self.cookie_max_age,
                path="/",
            )

        return response
