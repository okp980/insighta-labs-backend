from fastapi import APIRouter, Query, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from ..dependency import SessionDep, verify_api_version
import uuid
from datetime import datetime, timezone
from ..util import (
    CustomHTTPException,
    filter_profiles,
    filter_search_profiles,
    generate_profile,
    stream_profiles_csv,
    SUPPORTED_EXPORT_FORMATS,
    build_pagination_links,
    compute_total_pages,
)
from ..model.profiles import (
    ProfilePublic,
    ProfilesPublic,
    Profile,
    ProfileCreate,
    ProfilePublicMessage,
)
from sqlmodel import select
from typing import Annotated
from ..util import FilterParams, SearchParams, ExportParams
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
    total = profiles_result["count"]
    total_pages = compute_total_pages(total, filter_params.limit)
    return ProfilesPublic(
        page=filter_params.page,
        limit=filter_params.limit,
        total=total,
        total_pages=total_pages,
        links=build_pagination_links(
            path="/api/profiles",
            params=filter_params,
            total_pages=total_pages,
        ),
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
        total = profiles_result["count"]
        total_pages = compute_total_pages(total, search_params.limit)
        return ProfilesPublic(
            page=search_params.page,
            limit=search_params.limit,
            total=total,
            total_pages=total_pages,
            links=build_pagination_links(
                path="/api/profiles/search",
                params=search_params,
                total_pages=total_pages,
            ),
            data=profiles_result["data"],
        )
    except Exception:
        raise CustomHTTPException(status_code=502, message="Sever error")


@router.get("/export")
async def export_profiles(
    export_params: Annotated[ExportParams, Query()],
    session: SessionDep,
    current_user: Annotated[User, Depends(require_roles("admin", "analyst"))],
):
    if export_params.format not in SUPPORTED_EXPORT_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_EXPORT_FORMATS))
        raise CustomHTTPException(
            status_code=400,
            message=f"Unsupported export format '{export_params.format}'. Supported formats: {supported}.",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"profiles_{timestamp}.{export_params.format}"
    return StreamingResponse(
        stream_profiles_csv(session=session, filter_params=export_params),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.post("/", response_model=ProfilePublic)
async def create_profile(
    profile: ProfileCreate,
    session: SessionDep,
    # current_user: Annotated[
    #     User,
    #     Depends(
    #         require_roles(
    #             "admin",
    #         )
    #     ),
    # ],
):
    # db_profile = ProfileCreate.model_validate(profile)
    existing_profile = session.exec(
        select(Profile).where(Profile.name == profile.name)
    ).first()
    if existing_profile:
        print("Profile already exists")
        return ProfilePublicMessage(data=existing_profile)
    profile_data = generate_profile(profile.name)
    db_profile = Profile.model_validate(profile_data)
    session.add(db_profile)
    session.commit()
    session.refresh(db_profile)
    return ProfilePublic(data=db_profile)


@router.delete(
    "/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: Annotated[User, Depends(require_roles("admin"))],
):
    profile = session.get(Profile, profile_id)
    if not profile:
        raise CustomHTTPException(status_code=404, message="Profile not found")
    session.delete(profile)
    session.commit()
    return JSONResponse(status_code=204, content={})
