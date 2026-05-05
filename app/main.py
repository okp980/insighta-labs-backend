import logging
import time
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .database import create_db_and_tables
from .rate_limit import limiter
from .routers import auth, profiles
from .util import CustomHTTPException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
access_logger = logging.getLogger("app.access")

app = FastAPI()
app.state.limiter = limiter


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

app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    access_logger.info(
        "%s %s -> %d (%.2fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


app.include_router(profiles.router)
app.include_router(auth.router)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "status": "error",
            "message": "Too Many Requests",
        },
    )


@app.exception_handler(CustomHTTPException)
async def custom_http_exception_handler(request: Request, exc: CustomHTTPException):
    return JSONResponse(
        status_code=exc.status_code, content={"status": "error", "message": exc.message}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    message = exc.errors()[0]["msg"] if exc.errors() else "Invalid parameter type"
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": message},
    )


@app.get("/")
async def root():
    return {"message": "Hello, World!"}
