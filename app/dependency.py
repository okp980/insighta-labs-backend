from typing import Annotated

from fastapi import Depends, Header

# from fastapi.responses import HTTPException
from sqlmodel import Session

from .database import engine


def get_session():
    with Session(engine) as session:
        yield session


def verify_api_version(
    x_api_version: Annotated[str | None, Header(alias="X-API-Version")] = None,
):
    from app.util import CustomHTTPException

    if not x_api_version:
        raise CustomHTTPException(status_code=400, message="API version header required")


SessionDep = Annotated[Session, Depends(get_session)]
