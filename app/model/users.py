from datetime import datetime, timezone
from enum import Enum

import uuid7
from sqlalchemy import String
from sqlmodel import Field, SQLModel


class TokenPair(SQLModel):
    status: str = "success"
    access_token: str
    refresh_token: str
    # token_type: str = "bearer"


class TokenData(SQLModel):
    username: str | None = None


class RefreshTokenBase(SQLModel):
    token: str


class RefreshToken(RefreshTokenBase, table=True):
    id: uuid7.UUID = Field(default_factory=uuid7.create, primary_key=True)
    expires_at: datetime
    is_revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Role(str, Enum):
    admin = "admin"
    analyst = "analyst"


class UserBase(SQLModel):
    github_id: int = Field(index=True)
    username: str = Field(index=True)
    email: str = Field(index=True)
    avatar_url: str = Field(sa_type=String(255))
    role: Role = Field(default=Role.analyst)


class User(UserBase, table=True):
    id: uuid7.UUID = Field(default_factory=uuid7.create, primary_key=True)
    is_active: bool = Field(default=True)
    last_login_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserPublic(SQLModel):
    id: uuid7.UUID
    github_id: int
    username: str
    email: str
    avatar_url: str
    role: Role
    is_active: bool


class UserPublicResponse(SQLModel):
    status: str = "success"
    data: UserPublic
