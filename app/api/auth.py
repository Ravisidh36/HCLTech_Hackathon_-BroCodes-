from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.ratelimit import LOGIN_LIMIT, limiter
from app.audit.writer import AuditRecord, write_audit
from app.auth.deps import get_request_context
from app.auth.tokens import create_access_token, verify_password
from app.db import get_db, get_session_factory
from app.models.platform import User
from app.schemas.api import LoginRequest, TokenResponse
from app.schemas.common import RequestContext, Role

router = APIRouter(prefix="/auth", tags=["auth"])


class MeResponse(BaseModel):
    username: str
    full_name: str
    role: Role
    patient_id: str | None


@router.post("/login", response_model=TokenResponse)
@limiter.limit(LOGIN_LIMIT)
def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> TokenResponse:
    user = db.scalars(select(User).where(User.username == body.username)).first()
    ok = verify_password(body.password, user.password_hash if user else None) and user.is_active
    write_audit(
        session_factory,
        AuditRecord(
            trace_id=request.state.trace_id,
            endpoint="/auth/login",
            user_id=user.id if ok else None,
            role=user.role if ok else None,
            question=f"login attempt for '{body.username}'",
            error=None if ok else "invalid_credentials",
        ),
    )
    if not ok:
        # Same message for unknown user and wrong password: no account enumeration.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    return TokenResponse(
        access_token=create_access_token(user),
        role=Role(user.role),
        patient_id=user.patient_id,
        full_name=user.full_name,
    )


@router.get("/me", response_model=MeResponse)
def me(ctx: RequestContext = Depends(get_request_context), db: Session = Depends(get_db)) -> MeResponse:
    user = db.get(User, ctx.user_id)
    return MeResponse(username=user.username, full_name=user.full_name, role=ctx.role, patient_id=ctx.patient_id)
