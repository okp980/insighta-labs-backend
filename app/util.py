from sqlmodel import Session
from .model.profiles import Profile, PaginationLinks
from sqlmodel import select, col, asc, desc, func
from pydantic import BaseModel, Field
from typing import Literal, Callable, Iterator
from urllib.parse import urlencode
from .model.users import User
from .security import (
    JWT_SECRET,
    JWT_ALGORITHM,
    create_access_token,
    create_refresh_token,
)
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from .model.users import RefreshToken
from fastapi import Depends, Cookie
from .dependency import SessionDep
from typing import Annotated
from datetime import timezone
import csv
import io
import uuid
import httpx
import json


class PaginationParams(BaseModel):
    page: int = 1
    limit: int = Field(default=10, le=50)


class FilterParams(PaginationParams):
    gender: str | None = None
    age_group: str | None = None
    country_id: str | None = None
    min_age: int | None = None
    max_age: int | None = None
    min_gender_probability: float | None = None
    min_country_probability: float | None = None
    order: Literal["asc", "desc"] = "asc"
    sort_by: Literal["age", "created_at", "gender_probability"] = "created_at"


class SearchParams(PaginationParams):
    q: str


class ExportParams(FilterParams):
    format: str = "csv"


SUPPORTED_EXPORT_FORMATS = {"csv"}


class CustomHTTPException(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def compute_total_pages(total: int, limit: int) -> int:
    if limit <= 0 or total <= 0:
        return 0
    return (total + limit - 1) // limit


def build_pagination_links(
    *,
    path: str,
    params: PaginationParams,
    total_pages: int,
) -> PaginationLinks:
    extras = params.model_dump(exclude_defaults=True, exclude={"page", "limit"})

    def url_for(page: int) -> str:
        query = {"page": page, "limit": params.limit, **extras}
        return f"{path}?{urlencode(query)}"

    has_next = params.page < total_pages
    has_prev = params.page > 1

    return PaginationLinks(
        self=url_for(params.page),
        next=url_for(params.page + 1) if has_next else None,
        prev=url_for(params.page - 1) if has_prev else None,
    )


def generate_profile(name: str):
    try:
        genderize_response = httpx.get(
            f"https://api.genderize.io?name={name}", timeout=10.0
        )
        genderize_data = genderize_response.json()
        if genderize_data["gender"] is None or genderize_data["count"] == 0:
            raise CustomHTTPException(
                status_code=502, message="Genderize returned an invalid response"
            )
        agify_response = httpx.get(f"https://api.agify.io?name={name}", timeout=10.0)
        agify_data = agify_response.json()
        if agify_data["age"] is None:
            raise CustomHTTPException(
                status_code=502, message="Agify returned an invalid response"
            )
        country_response = httpx.get(
            f"https://api.nationalize.io?name={name}",
            timeout=10.0,
        )
        country_data = country_response.json()
        if country_data["country"] is None:
            raise CustomHTTPException(
                status_code=502, message="Nationalize returned an invalid response"
            )

        with open("countries.json", "r") as f:
            countries = json.load(f)
        country_name = next(
            (
                c["country_name"]
                for c in countries
                if c["country_id"] == country_data["country"][0]["country_id"]
            ),
            None,
        )
        if country_name is None:
            country_name = "Nigeria"

        print(json.dumps(country_data, indent=4))
        print(json.dumps(genderize_data, indent=4))
        print(json.dumps(agify_data, indent=4))

        country_with_highest_probability = max(
            country_data["country"], key=lambda x: x["probability"]
        )

        return {
            "name": name,
            "gender": genderize_data["gender"],
            "gender_probability": genderize_data["probability"],
            "sample_size": genderize_data["count"],
            "age": agify_data["age"],
            "age_group": "child"
            if agify_data["age"] <= 12
            else "teenager"
            if agify_data["age"] <= 19
            else "adult"
            if agify_data["age"] <= 59
            else "senior",
            "country_name": country_name,
            "country_id": country_with_highest_probability["country_id"],
            "country_probability": round(
                country_with_highest_probability["probability"], 2
            ),
        }
    except httpx.RequestError as e:
        print.pprint(e.request.url)
        raise CustomHTTPException(status_code=502, message="Upstream or server failure")


def _build_profile_statement(filter_params: FilterParams):
    statement = select(Profile)
    if filter_params.gender is not None:
        statement = statement.where(col(Profile.gender) == filter_params.gender.lower())
    if filter_params.country_id is not None:
        statement = statement.where(
            col(Profile.country_id) == filter_params.country_id.upper()
        )
    if filter_params.age_group is not None:
        statement = statement.where(
            col(Profile.age_group) == filter_params.age_group.lower()
        )
    if filter_params.min_age is not None:
        statement = statement.where(col(Profile.age) >= filter_params.min_age)
    if filter_params.max_age is not None:
        statement = statement.where(col(Profile.age) <= filter_params.max_age)
    if filter_params.min_gender_probability is not None:
        statement = statement.where(
            col(Profile.gender_probability) >= filter_params.min_gender_probability
        )
    if filter_params.min_country_probability is not None:
        statement = statement.where(
            col(Profile.country_probability) >= filter_params.min_country_probability
        )
    if filter_params.sort_by == "age":
        statement = statement.order_by(
            asc(col(Profile.age))
            if filter_params.order == "asc"
            else desc(col(Profile.age))
        )
    if filter_params.sort_by == "created_at":
        statement = statement.order_by(
            asc(col(Profile.created_at))
            if filter_params.order == "asc"
            else desc(col(Profile.created_at))
        )
    if filter_params.sort_by == "gender_probability":
        statement = statement.order_by(
            asc(col(Profile.gender_probability))
            if filter_params.order == "asc"
            else desc(col(Profile.gender_probability))
        )
    return statement


def filter_profiles(*, session: Session, filter_params: FilterParams) -> dict:
    statement = _build_profile_statement(filter_params)
    statement = statement.offset((filter_params.page - 1) * filter_params.limit).limit(
        filter_params.limit
    )
    total_count = session.exec(select(func.count()).select_from(Profile)).one()
    result = {
        "count": total_count,
        "data": session.exec(statement).all(),
    }

    return result


CSV_COLUMNS = [
    "id",
    "name",
    "gender",
    "gender_probability",
    "age",
    "age_group",
    "country_id",
    "country_name",
    "country_probability",
    "created_at",
]


def stream_profiles_csv(
    *, session: Session, filter_params: FilterParams
) -> Iterator[str]:
    statement = _build_profile_statement(filter_params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def _flush() -> str:
        data = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return data

    writer.writerow(CSV_COLUMNS)
    yield _flush()

    for profile in session.exec(statement).yield_per(1000):
        created_at = profile.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        writer.writerow(
            [
                str(profile.id),
                profile.name,
                profile.gender.value,
                profile.gender_probability,
                profile.age,
                profile.age_group.value,
                profile.country_id,
                profile.country_name,
                profile.country_probability,
                created_at.astimezone(timezone.utc).isoformat(),
            ]
        )
        yield _flush()


def filter_search_profiles(*, session: Session, search_params: SearchParams) -> dict:
    male = [
        "male",
        "males",
        "man",
        "men",
        "guy",
        "guys",
        "boy",
        "boys",
        "gentleman",
        "gentlemen",
    ]
    female = [
        "female",
        "females",
        "women",
        "woman",
        "lady",
        "ladies",
        "girl",
        "girlsgentlewomen",
    ]
    teenager = ["teen", "teenager", "teenagers", "teenage"]
    adult = ["adult", "adults", "adulthood"]
    old = ["old", "elder", "elderly", "senior", "seniors"]

    gender: list[str] = []
    age: int | None = None
    min_age: int | None = None
    max_age: int | None = None
    age_group: str | None = None
    country_name: str | None = None
    word_list = search_params.q.strip().lower().split()
    for index, word in enumerate(word_list):
        if word in male:
            gender.append("male")
        if word in female:
            gender.append("female")
        if word == "young":
            min_age = 16
            max_age = 24
        if word.isdigit():
            age = int(word)
        if word == "above":
            min_age = int(word_list[index + 1])
        if word == "below":
            max_age = int(word_list[index + 1])
        if word in teenager:
            age_group = "teenager"
        if word in adult:
            age_group = "adult"
        if word in old:
            age_group = "senior"
        if word == "from" or word == "in":
            country_name = word_list[index + 1]

    statement = select(Profile)
    if len(gender) == 1:
        statement = statement.where(col(Profile.gender) == gender[0])
    if len(gender) > 1:
        statement = statement.where(col(Profile.gender).in_(gender))

    if min_age is not None:
        statement = statement.where(col(Profile.age) >= min_age)
    if max_age is not None:
        statement = statement.where(col(Profile.age) <= max_age)
    if age is not None and min_age is None and max_age is None:
        statement = statement.where(col(Profile.age) == age)
    if age_group is not None:
        statement = statement.where(col(Profile.age_group) == age_group)
    if country_name is not None:
        statement = statement.where(col(Profile.country_name).ilike(country_name))

    total_count = session.exec(
        select(func.count()).select_from(statement.subquery())
    ).one()

    statement = statement.offset((search_params.page - 1) * search_params.limit).limit(
        search_params.limit
    )
    is_not_interpreted = (
        len(gender) == 0 and age is None and age_group is None and country_name is None
    )

    if is_not_interpreted and len(word_list) > 0:
        raise CustomHTTPException(status_code=400, message="Unable to interpret query")
    if len(session.exec(statement).all()) == 0:
        raise CustomHTTPException(status_code=404, message="No profiles found")
    result = {
        "count": total_count,
        "data": session.exec(statement).all(),
    }
    return result


def get_user_by_github_id(*, session: Session, github_id: int) -> User | None:
    return session.exec(select(User).where(User.github_id == github_id)).first()


def create_user(*, session: Session, user: User) -> User:
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_user_by_id(*, session: Session, user_id: uuid.UUID) -> User | None:
    return session.exec(select(User).where(User.id == user_id)).first()


def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except ExpiredSignatureError:
        raise CustomHTTPException(status_code=401, message="Token has expired")
    except InvalidTokenError:
        raise CustomHTTPException(status_code=401, message="Invalid token")


def verify_refresh_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except ExpiredSignatureError:
        raise CustomHTTPException(status_code=401, message="Refresh token has expired")
    except InvalidTokenError:
        raise CustomHTTPException(status_code=401, message="Invalid refresh token")


def revoke_refresh_token(token: str, session: Session):
    refresh_token = session.exec(
        select(RefreshToken).where(RefreshToken.token == token)
    ).first()
    if not refresh_token:
        raise CustomHTTPException(status_code=401, message="Refresh token not found")
    if refresh_token.is_revoked:
        raise CustomHTTPException(
            status_code=401, message="Refresh token has been revoked"
        )
    refresh_token.is_revoked = True
    session.commit()
    session.refresh(refresh_token)
    return refresh_token


def refresh_all_tokens(*, session: Session, refresh_token: str) -> dict:
    payload = verify_refresh_token(refresh_token)
    user_id = payload.get("sub")
    user = get_user_by_id(session=session, user_id=uuid.UUID(user_id))
    if not user:
        raise CustomHTTPException(status_code=401, message="User not found")
    revoke_refresh_token(refresh_token, session)
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id, session)

    return {"access_token": access_token, "refresh_token": refresh_token}


def get_current_user(
    *, session: SessionDep, access_token: Annotated[str | None, Cookie()] = None
) -> User:
    payload = verify_access_token(str(access_token))
    user_id = payload.get("sub")

    user = get_user_by_id(session=session, user_id=uuid.UUID(user_id))

    if not user:
        raise CustomHTTPException(status_code=401, message="User not found")
    return user


def require_roles(*allowed_roles: Literal["admin", "analyst"]) -> Callable[..., User]:
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.value not in allowed_roles:
            raise CustomHTTPException(
                status_code=403,
                message="Not enough permissions",
            )
        return current_user

    return role_checker
