from functools import lru_cache
from typing import Annotated

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from ..config import Settings
from ..dependency import SessionDep
from ..model.users import TokenPair, User, UserPublic, UserPublicResponse
from ..rate_limit import limiter
from ..security import (
    ACCESS_TOKEN_MINUTES,
    REFRESH_TOKEN_MINUTES,
    create_access_token,
    create_refresh_token,
)
from ..util import (
    CustomHTTPException,
    create_user,
    get_current_user,
    get_user_by_github_id,
    refresh_all_tokens,
    revoke_refresh_token,
)


@lru_cache
def get_settings():
    return Settings()


oauth = OAuth()
oauth.register(
    name="github",
    client_id=get_settings().github_client_id,
    client_secret=get_settings().github_client_secret,
    authorize_url="https://github.com/login/oauth/authorize",
    access_token_url="https://github.com/login/oauth/access_token",
    api_base_url="https://api.github.com/",
    client_kwargs={
        "scope": "user:email",
        "code_challenge_method": "S256",
    },
)

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.get("/github/login")
@limiter.limit("10/minute")
async def github_login(request: Request):
    redirect_uri = request.url_for("github_callback")
    return await oauth.github.authorize_redirect(request, str(redirect_uri))


@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(request: Request, session: SessionDep):
    token = await oauth.github.authorize_access_token(request)
    resp = await oauth.github.get("user", token=token)
    github_user = resp.json()

    email_resp = await oauth.github.get("user/emails", token=token)
    emails = email_resp.json() if email_resp.status_code == 200 else []
    primary_email = next(
        (e["email"] for e in emails if e.get("primary") and e.get("verified")),
        None,
    )
    user = get_user_by_github_id(session=session, github_id=github_user.get("id"))
    if not user:
        user = create_user(
            session=session,
            user=User(
                github_id=github_user.get("id"),
                username=github_user.get("login"),
                email=primary_email or github_user.get("email"),
                avatar_url=github_user.get("avatar_url"),
            ),
        )
    access_token = create_access_token(user.id)
    refresh_token_value = create_refresh_token(user.id, session)

    settings = get_settings()
    redirect = RedirectResponse(
        url=f"{settings.frontend_url.rstrip('/')}/auth/callback",
        status_code=302,
    )
    redirect.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=ACCESS_TOKEN_MINUTES * 60,
    )
    redirect.set_cookie(
        key="refresh_token",
        value=refresh_token_value,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=REFRESH_TOKEN_MINUTES * 60,
    )
    return redirect


@router.post(
    "/refresh",
    response_model=TokenPair,
)
@limiter.limit("10/minute")
async def refresh(
    request: Request,
    session: SessionDep,
    response: Response,
    refresh_token: Annotated[str | None, Cookie()] = None,
):
    tokens = refresh_all_tokens(session=session, refresh_token=refresh_token)

    settings = get_settings()
    response.set_cookie(
        key="access_token",
        value=tokens["access_token"],
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=ACCESS_TOKEN_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=REFRESH_TOKEN_MINUTES * 60,
    )
    return TokenPair(access_token=tokens["access_token"], refresh_token=tokens["refresh_token"])


@router.post("/logout")
@limiter.limit("10/minute")
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    refresh_token: Annotated[str | None, Cookie()] = None,
):
    revoke_refresh_token(refresh_token, session)
    response.delete_cookie(key="access_token")
    response.delete_cookie(key="refresh_token")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserPublicResponse)
async def me(current_user: Annotated[User, Depends(get_current_user)]):
    return UserPublicResponse(
        data=UserPublic(
            id=current_user.id,
            github_id=current_user.github_id,
            username=current_user.username,
            email=current_user.email,
            avatar_url=current_user.avatar_url,
            role=current_user.role,
            is_active=current_user.is_active,
        )
    )


# ---------------------------------------------------------------------------
# CLI OAuth (PKCE) endpoints
#
# Designed for native/CLI clients per RFC 8252. The CLI generates its own
# state + PKCE pair, runs a temporary loopback HTTP server on the redirect
# port, drives the user through GitHub in their browser, then exchanges the
# returned `code` (plus the original `code_verifier`) here for our own
# JWT pair. Tokens are returned in the JSON body so the CLI can persist
# them locally; cookies are not used for the CLI.
# ---------------------------------------------------------------------------


class CliStartResponse(BaseModel):
    status: str = "success"
    client_id: str
    redirect_uri: str
    scope: str = "user:email"


class CliExchangeBody(BaseModel):
    code: str
    code_verifier: str
    state: str | None = None


class CliExchangeResponse(BaseModel):
    status: str = "success"
    access_token: str
    refresh_token: str
    user: UserPublic


@router.get("/cli/start", response_model=CliStartResponse)
@limiter.limit("10/minute")
async def cli_start(request: Request):
    settings = get_settings()
    if not settings.github_cli_client_id:
        raise CustomHTTPException(
            status_code=500,
            message=("CLI OAuth is not configured on the server (GITHUB_CLI_CLIENT_ID is unset)."),
        )
    return CliStartResponse(
        client_id=settings.github_cli_client_id,
        redirect_uri=settings.github_cli_redirect_uri,
    )


@router.post("/cli/exchange", response_model=CliExchangeResponse)
@limiter.limit("10/minute")
async def cli_exchange(request: Request, session: SessionDep, body: CliExchangeBody):
    settings = get_settings()
    if not settings.github_cli_client_id or not settings.github_cli_client_secret:
        raise CustomHTTPException(
            status_code=500,
            message=(
                "CLI OAuth is not configured on the server (GITHUB_CLI_CLIENT_ID/SECRET are unset)."
            ),
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_cli_client_id,
                "client_secret": settings.github_cli_client_secret,
                "code": body.code,
                "code_verifier": body.code_verifier,
                "redirect_uri": settings.github_cli_redirect_uri,
            },
        )
        try:
            token_payload = token_resp.json()
        except ValueError:
            raise CustomHTTPException(
                status_code=502, message="Invalid response from GitHub token endpoint"
            )

        gh_access_token = token_payload.get("access_token")
        if not gh_access_token:
            raise CustomHTTPException(
                status_code=401,
                message=token_payload.get(
                    "error_description",
                    token_payload.get("error", "GitHub token exchange failed"),
                ),
            )

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {gh_access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if user_resp.status_code != 200:
            raise CustomHTTPException(status_code=502, message="Failed to fetch user from GitHub")
        github_user = user_resp.json()

        emails_resp = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {gh_access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        emails = emails_resp.json() if emails_resp.status_code == 200 else []

    primary_email = next(
        (e["email"] for e in emails if e.get("primary") and e.get("verified")),
        None,
    )

    user = get_user_by_github_id(session=session, github_id=github_user.get("id"))
    if not user:
        user = create_user(
            session=session,
            user=User(
                github_id=github_user.get("id"),
                username=github_user.get("login"),
                email=primary_email or github_user.get("email"),
                avatar_url=github_user.get("avatar_url"),
            ),
        )

    access_token = create_access_token(user.id)
    refresh_token_value = create_refresh_token(user.id, session)

    return CliExchangeResponse(
        access_token=access_token,
        refresh_token=refresh_token_value,
        user=UserPublic(
            id=user.id,
            github_id=user.github_id,
            username=user.username,
            email=user.email,
            avatar_url=user.avatar_url,
            role=user.role,
            is_active=user.is_active,
        ),
    )
