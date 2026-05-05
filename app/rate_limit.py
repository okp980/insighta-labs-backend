import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from .security import JWT_SECRET, JWT_ALGORITHM

AUTH_PATH_PREFIX = "/auth"


def rate_limit_key(request: Request) -> str:
    if request.url.path.startswith(AUTH_PATH_PREFIX):
        return get_remote_address(request)

    token = request.cookies.get("access_token")
    if token:
        try:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except jwt.InvalidTokenError:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])
