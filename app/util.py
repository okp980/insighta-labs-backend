import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import timezone
from typing import Annotated, Callable, Iterator, Literal
from urllib.parse import urlencode

import httpx
import jwt
from cachetools import TTLCache
from fastapi import Cookie, Depends
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from pydantic import BaseModel, Field
from sqlmodel import Session, asc, col, desc, func, select

from .dependency import SessionDep
from .model.profiles import PaginationLinks, Profile
from .model.users import RefreshToken, User
from .security import (
    JWT_ALGORITHM,
    JWT_SECRET,
    create_access_token,
    create_refresh_token,
)


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


def _new_profiles_cache() -> TTLCache:
    from .config import Settings

    s = Settings()
    return TTLCache(maxsize=s.profiles_cache_max_entries, ttl=s.profiles_cache_ttl_seconds)


_PROFILES_CACHE = _new_profiles_cache()


def invalidate_profiles_cache() -> None:
    _PROFILES_CACHE.clear()


@dataclass(frozen=True)
class ParsedSearchFilters:
    genders: tuple[str, ...]
    min_age: int | None
    max_age: int | None
    exact_age: int | None
    age_group: str | None
    country_ilike: str | None


def _canonical_list_dict(fp: FilterParams) -> dict:
    min_age, max_age = fp.min_age, fp.max_age
    if min_age is not None and max_age is not None and min_age > max_age:
        min_age, max_age = max_age, min_age
    d: dict = {
        "endpoint": "list",
        "limit": fp.limit,
        "order": fp.order,
        "page": fp.page,
        "sort_by": fp.sort_by,
    }
    if fp.age_group is not None:
        d["age_group"] = fp.age_group.lower()
    if fp.country_id is not None:
        d["country_id"] = fp.country_id.upper()
    if fp.gender is not None:
        d["gender"] = fp.gender.lower()
    if min_age is not None:
        d["min_age"] = min_age
    if max_age is not None:
        d["max_age"] = max_age
    if fp.min_country_probability is not None:
        d["min_country_probability"] = fp.min_country_probability
    if fp.min_gender_probability is not None:
        d["min_gender_probability"] = fp.min_gender_probability
    return dict(sorted(d.items()))


def _canonical_search_dict(parsed: ParsedSearchFilters, page: int, limit: int) -> dict:
    d: dict = {"endpoint": "search", "limit": limit, "page": page}
    if parsed.age_group is not None:
        d["age_group"] = parsed.age_group
    if parsed.country_ilike is not None:
        d["country"] = parsed.country_ilike.casefold()
    if parsed.exact_age is not None:
        d["exact_age"] = parsed.exact_age
    if parsed.genders:
        d["genders"] = list(parsed.genders)
    if parsed.max_age is not None:
        d["max_age"] = parsed.max_age
    if parsed.min_age is not None:
        d["min_age"] = parsed.min_age
    return dict(sorted(d.items()))


def _profiles_result_to_cache(result: dict) -> dict:
    return {"count": result["count"], "data": [p.model_dump(mode="json") for p in result["data"]]}


def _profiles_result_from_cache(entry: dict) -> dict:
    return {
        "count": entry["count"],
        "data": [Profile.model_validate(x) for x in entry["data"]],
    }


def _list_cache_key(fp: FilterParams) -> str:
    return json.dumps(_canonical_list_dict(fp), sort_keys=True, separators=(",", ":"))


def _search_cache_key(parsed: ParsedSearchFilters, page: int, limit: int) -> str:
    return json.dumps(
        _canonical_search_dict(parsed, page, limit),
        sort_keys=True,
        separators=(",", ":"),
    )


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
        genderize_response = httpx.get(f"https://api.genderize.io?name={name}", timeout=10.0)
        genderize_data = genderize_response.json()
        if genderize_data["gender"] is None or genderize_data["count"] == 0:
            raise CustomHTTPException(
                status_code=502, message="Genderize returned an invalid response"
            )
        agify_response = httpx.get(f"https://api.agify.io?name={name}", timeout=10.0)
        agify_data = agify_response.json()
        if agify_data["age"] is None:
            raise CustomHTTPException(status_code=502, message="Agify returned an invalid response")
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
            "country_probability": round(country_with_highest_probability["probability"], 2),
        }
    except httpx.RequestError as e:
        print.pprint(e.request.url)
        raise CustomHTTPException(status_code=502, message="Upstream or server failure")


def _build_profile_statement(filter_params: FilterParams):
    statement = select(Profile)
    if filter_params.gender is not None:
        statement = statement.where(col(Profile.gender) == filter_params.gender.lower())
    if filter_params.country_id is not None:
        statement = statement.where(col(Profile.country_id) == filter_params.country_id.upper())
    if filter_params.age_group is not None:
        statement = statement.where(col(Profile.age_group) == filter_params.age_group.lower())
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
            asc(col(Profile.age)) if filter_params.order == "asc" else desc(col(Profile.age))
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


def parse_search_query(q: str) -> ParsedSearchFilters:
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
    word_list = q.strip().lower().split()
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

    genders = sorted(set(gender))
    if min_age is not None and max_age is not None and min_age > max_age:
        min_age, max_age = max_age, min_age

    return ParsedSearchFilters(
        genders=tuple(genders),
        min_age=min_age,
        max_age=max_age,
        exact_age=age,
        age_group=age_group,
        country_ilike=country_name,
    )


def _build_search_statement(parsed: ParsedSearchFilters):
    statement = select(Profile)
    if len(parsed.genders) == 1:
        statement = statement.where(col(Profile.gender) == parsed.genders[0])
    if len(parsed.genders) > 1:
        statement = statement.where(col(Profile.gender).in_(parsed.genders))
    if parsed.min_age is not None:
        statement = statement.where(col(Profile.age) >= parsed.min_age)
    if parsed.max_age is not None:
        statement = statement.where(col(Profile.age) <= parsed.max_age)
    if parsed.exact_age is not None and parsed.min_age is None and parsed.max_age is None:
        statement = statement.where(col(Profile.age) == parsed.exact_age)
    if parsed.age_group is not None:
        statement = statement.where(col(Profile.age_group) == parsed.age_group)
    if parsed.country_ilike is not None:
        statement = statement.where(col(Profile.country_name).ilike(parsed.country_ilike))
    return statement


def filter_profiles(*, session: Session, filter_params: FilterParams) -> dict:
    cache_key = "list:" + _list_cache_key(filter_params)
    cached = _PROFILES_CACHE.get(cache_key)
    if cached is not None:
        return _profiles_result_from_cache(cached)

    base = _build_profile_statement(filter_params)
    total_count = session.exec(select(func.count()).select_from(base.subquery())).one()
    page_stmt = base.offset((filter_params.page - 1) * filter_params.limit).limit(
        filter_params.limit
    )
    data = session.exec(page_stmt).all()
    result = {"count": total_count, "data": data}
    _PROFILES_CACHE[cache_key] = _profiles_result_to_cache(result)
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


def stream_profiles_csv(*, session: Session, filter_params: FilterParams) -> Iterator[str]:
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
    word_list = search_params.q.strip().lower().split()
    parsed = parse_search_query(search_params.q)

    is_not_interpreted = (
        len(parsed.genders) == 0
        and parsed.exact_age is None
        and parsed.age_group is None
        and parsed.country_ilike is None
    )

    if is_not_interpreted and len(word_list) > 0:
        raise CustomHTTPException(status_code=400, message="Unable to interpret query")

    cache_key = "search:" + _search_cache_key(parsed, search_params.page, search_params.limit)
    cached = _PROFILES_CACHE.get(cache_key)
    if cached is not None:
        return _profiles_result_from_cache(cached)

    base = _build_search_statement(parsed)
    total_count = session.exec(select(func.count()).select_from(base.subquery())).one()
    page_stmt = base.offset((search_params.page - 1) * search_params.limit).limit(
        search_params.limit
    )
    rows = session.exec(page_stmt).all()
    if len(rows) == 0:
        raise CustomHTTPException(status_code=404, message="No profiles found")
    result = {"count": total_count, "data": rows}
    _PROFILES_CACHE[cache_key] = _profiles_result_to_cache(result)
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
    refresh_token = session.exec(select(RefreshToken).where(RefreshToken.token == token)).first()
    if not refresh_token:
        raise CustomHTTPException(status_code=401, message="Refresh token not found")
    if refresh_token.is_revoked:
        raise CustomHTTPException(status_code=401, message="Refresh token has been revoked")
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
