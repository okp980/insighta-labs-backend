import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session, select

from ..config import Settings
from ..database import engine
from ..dependency import SessionDep, verify_api_version
from ..ingest_csv import import_profiles_csv as run_csv_import
from ..model.profiles import (
    Profile,
    ProfileCreate,
    ProfilePublic,
    ProfilePublicMessage,
    ProfilesImportResponse,
    ProfilesPublic,
)
from ..model.users import User
from ..util import (
    SUPPORTED_EXPORT_FORMATS,
    CustomHTTPException,
    ExportParams,
    FilterParams,
    SearchParams,
    build_pagination_links,
    compute_total_pages,
    filter_profiles,
    filter_search_profiles,
    generate_profile,
    invalidate_profiles_cache,
    require_roles,
    stream_profiles_csv,
)

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
    profiles_result = filter_search_profiles(session=session, search_params=search_params)
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
            message=(
                f"Unsupported export format '{export_params.format}'. "
                f"Supported formats: {supported}."
            ),
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"profiles_{timestamp}.{export_params.format}"
    return StreamingResponse(
        stream_profiles_csv(session=session, filter_params=export_params),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=ProfilesImportResponse)
async def import_profiles_csv_endpoint(
    current_user: Annotated[
        User,
        Depends(require_roles("admin")),
    ],
    file: UploadFile = File(...),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise CustomHTTPException(status_code=400, message="A .csv file is required")

    settings = Settings()

    def run_import() -> dict:
        upload = file.file
        upload.seek(0)
        with Session(engine) as session:
            return run_csv_import(
                session=session,
                file_binary=upload,
                batch_size=settings.csv_import_batch_size,
            )

    try:
        result = await asyncio.to_thread(run_import)
    except ValueError as e:
        raise CustomHTTPException(status_code=400, message=str(e)) from e

    invalidate_profiles_cache()
    return result


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
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                "admin",
            )
        ),
    ],
):
    # db_profile = ProfileCreate.model_validate(profile)
    existing_profile = session.exec(select(Profile).where(Profile.name == profile.name)).first()
    if existing_profile:
        print("Profile already exists")
        return ProfilePublicMessage(data=existing_profile)
    profile_data = generate_profile(profile.name)
    db_profile = Profile.model_validate(profile_data)
    session.add(db_profile)
    session.commit()
    session.refresh(db_profile)
    invalidate_profiles_cache()
    return ProfilePublic(data=db_profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
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
    invalidate_profiles_cache()
    return JSONResponse(status_code=204, content={})
