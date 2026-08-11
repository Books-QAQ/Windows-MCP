from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandRequest(BaseModel):
    instruction: str = Field(min_length=1, description="Natural-language instruction from the phone.")
    model: str | None = Field(
        default=None,
        description="Optional Claude model override for this task.",
    )


class ScreenshotPayload(BaseModel):
    mime_type: str = "image/png"
    base64_data: str
    summary: str = ""


class TaskResult(BaseModel):
    message: str
    screenshot: ScreenshotPayload | None = None
    raw_agent_response: str | None = None
    completed_at: datetime


class TaskView(BaseModel):
    id: str
    status: TaskStatus
    instruction: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: TaskResult | None = None
    cancel_requested: bool = False


class SkillView(BaseModel):
    name: str
    description: str
    examples: list[str] = []
    triggers: list[str] = []
    input_schema: dict = {}
    output_schema: dict = {}


class SkillResult(BaseModel):
    """Structured result returned by a skill after execution.

    Carries typed data that downstream nodes can consume, plus a human-readable
    message and optional screenshot for the mobile-facing UI.
    """

    success: bool
    data: dict = {}
    message: str = ""
    screenshot: ScreenshotPayload | None = None
    error: str | None = None
