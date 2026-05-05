from functools import lru_cache
from typing import Annotated

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, Response

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
