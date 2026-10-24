from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR = "error"


class ToolResult(BaseModel):
    """Uniform envelope returned by every deterministic tool (R5)."""

    tool: str
    status: ToolStatus
    data: Any = None
    message: str = ""
    source_system: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def ok(cls, tool: str, source_system: str, data: Any, message: str = "") -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.OK, data=data, message=message, source_system=source_system)

    @classmethod
    def not_found(cls, tool: str, source_system: str, message: str) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.NOT_FOUND, message=message, source_system=source_system)

    @classmethod
    def error(cls, tool: str, source_system: str, message: str) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.ERROR, message=message, source_system=source_system)
