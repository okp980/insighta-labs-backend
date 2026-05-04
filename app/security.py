from datetime import datetime, timedelta, timezone
import jwt
from .config import Settings
from functools import lru_cache
from sqlmodel import Session, select
from .model.users import RefreshToken
import uuid


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


settings = get_settings()

JWT_SECRET = settings.secret
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 3
REFRESH_TOKEN_MINUTES = 5


def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: uuid.UUID, session: Session) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=REFRESH_TOKEN_MINUTES)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_token = RefreshToken(
        token=token, expires_at=now + timedelta(minutes=REFRESH_TOKEN_MINUTES)
    )
    session.add(refresh_token)
    session.commit()
    session.refresh(refresh_token)
    return token
