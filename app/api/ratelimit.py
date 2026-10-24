"""Per-user rate limiting (per-IP for anonymous requests) against abuse and runaway LLM cost."""

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth.tokens import decode_access_token


def _rate_limit_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            return f"user:{decode_access_token(auth[7:])['sub']}"
        except jwt.PyJWTError:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key)

LOGIN_LIMIT = "10/minute"
CHAT_LIMIT = "20/minute"
