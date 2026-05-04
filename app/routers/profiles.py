from fastapi import APIRouter, Query, Depends
from ..dependency import SessionDep, verify_api_version
import uuid
from ..util import CustomHTTPException, filter_profiles, filter_search_profiles
from ..model.profiles import (
    ProfilePublic,
    ProfilesPublic,
    Profile,
)

from typing import Annotated
from ..util import FilterParams, SearchParams
from ..util import require_roles
from ..model.users import User

router = APIRouter(
    prefix="/api/profiles",
    tags=["Profiles"],
    dependencies=[Depends(verify_api_version)],
)


@router.get("/", response_model=ProfilesPublic)
async def get_profiles(
    filter_params: Annotated[FilterParams, Query()],
    session: SessionDep,
    current_user: Annotated[User, Depends(require_roles("admin", "analyst"))],
):
    profiles_result = filter_profiles(session=session, filter_params=filter_params)
    return ProfilesPublic(
        page=filter_params.page,
        limit=filter_params.limit,
        total=profiles_result["count"],
        data=profiles_result["data"],
    )


@router.get("/search", response_model=ProfilesPublic)
async def search_profiles(
    search_params: Annotated[SearchParams, Query()],
    session: SessionDep,
    current_user: Annotated[User, Depends(require_roles("admin", "analyst"))],
):
    try:
        profiles_result = filter_search_profiles(
            session=session, search_params=search_params
        )
        return ProfilesPublic(
            page=search_params.page,
            limit=search_params.limit,
            total=profiles_result["count"],
            data=profiles_result["data"],
        )
    except Exception:
        raise CustomHTTPException(status_code=502, message="Sever error")


@router.get("/{profile_id}", response_model=ProfilePublic)
async def get_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: Annotated[User, Depends(require_roles("admin", "analyst"))],
):
    profile = session.get(Profile, profile_id)
    if not profile:
        raise CustomHTTPException(status_code=404, message="Profile not found")
    return ProfilePublic(data=profile)
