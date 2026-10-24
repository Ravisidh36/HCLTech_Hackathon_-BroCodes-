"""FastAPI dependencies that turn a bearer token into the trusted RequestContext (R7).

Identity, role and patient id are re-read from the database on every request, so a token can
never carry a patient id the user does not own, and deactivated users lose access immediately.
"""

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.tokens import decode_access_token
from app.db import get_db
from app.models.platform import User
from app.schemas.common import RequestContext, Role

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_request_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> RequestContext:
    if credentials is None:
        raise _unauthorized("Not authenticated.")
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        raise _unauthorized("Invalid or expired token.") from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token.")
    return RequestContext(
        user_id=user.id,
        username=user.username,
        role=Role(user.role),
        patient_id=user.patient_id,
        trace_id=request.state.trace_id,
    )


def require_role(role: Role):
    def dependency(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        if ctx.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this resource.")
        if role == Role.PATIENT and not ctx.patient_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No patient record is linked to this account.")
        return ctx

    return dependency


require_patient = require_role(Role.PATIENT)
require_admin = require_role(Role.ADMIN)
