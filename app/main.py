from fastapi import FastAPI
from .routers import profiles, auth
from .database import create_db_and_tables
from .util import CustomHTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .config import Settings
from functools import lru_cache

app = FastAPI()


@lru_cache
def get_settings():
    return Settings()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# we need this to save temporary code & state in session
# for authlib to work
app.add_middleware(SessionMiddleware, secret_key=get_settings().secret)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


app.include_router(profiles.router)
app.include_router(auth.router)


@app.exception_handler(CustomHTTPException)
async def custom_http_exception_handler(request: Request, exc: CustomHTTPException):
    return JSONResponse(
        status_code=exc.status_code, content={"status": "error", "message": exc.message}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # return JSONResponse(
    #     status_code=422,
    #     content={"status": "error", "message": "Invalid parameter type"},
    # )
    message = "Validation errors:"
    for error in exc.errors():
        message += f"\nField: {error['loc']}, Error: {error['msg']}"
    return PlainTextResponse(message, status_code=400)


@app.get("/")
async def root():
    return {"message": "Hello, World!"}
