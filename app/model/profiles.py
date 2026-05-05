from datetime import datetime, timezone
from enum import Enum

import uuid7
from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class Gender(str, Enum):
    male = "male"
    female = "female"


class AgeGroup(str, Enum):
    child = "child"
    teenager = "teenager"
    adult = "adult"
    senior = "senior"


class ProfileBase(SQLModel):
    name: str = Field(index=True, unique=True)
    gender: Gender
    gender_probability: float = Field()
    age: int = Field()
    age_group: AgeGroup
    country_id: str = Field()
    country_name: str = Field()
    country_probability: float = Field()


class Profile(ProfileBase, table=True):
    __table_args__ = (
        Index("ix_profile_gender_age", "gender", "age"),
        Index("ix_profile_country_id_age", "country_id", "age"),
        Index("ix_profile_age_group", "age_group"),
        Index("ix_profile_created_at", "created_at"),
        Index("ix_profile_country_name", "country_name"),
    )
    id: uuid7.UUID = Field(default_factory=uuid7.create, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProfilePublic(SQLModel):
    status: str = "success"
    data: Profile


class PaginationLinks(SQLModel):
    self: str
    next: str | None = None
    prev: str | None = None


class ProfilesPublic(SQLModel):
    status: str = "success"
    page: int
    limit: int
    total: int
    total_pages: int
    links: PaginationLinks
    data: list[Profile]


class ProfilePublicMessage(ProfilePublic):
    message: str | None = "Profile already exists"


class ProfileCreate(SQLModel):
    name: str = Field()


class ProfilesImportResponse(SQLModel):
    status: str = "success"
    total_rows: int
    inserted: int
    skipped: int
    reasons: dict[str, int] = Field(default_factory=dict)
