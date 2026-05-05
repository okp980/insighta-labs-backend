from sqlmodel import SQLModel, Field
import uuid7
from datetime import datetime, timezone
from enum import Enum


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
