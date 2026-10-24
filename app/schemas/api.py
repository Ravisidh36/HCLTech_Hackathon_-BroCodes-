from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.citations import Citation
from app.schemas.common import AnswerType, Role
from app.schemas.tools import ToolStatus


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    patient_id: str | None
    full_name: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


class ToolInvocation(BaseModel):
    tool: str
    status: ToolStatus


class PendingActionOut(BaseModel):
    action_id: str
    summary: str
    expires_at: datetime


class ChatResponse(BaseModel):
    """Response contract, extending the §8 example."""

    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation] = []
    tools_invoked: list[ToolInvocation] = []
    explanation: str = ""
    assumptions: list[str] = []
    pending_action: PendingActionOut | None = None
    latency_ms: int


class ConfirmRequest(BaseModel):
    action_id: str = Field(min_length=1, max_length=64)
    decision: Literal["confirm", "cancel"]
