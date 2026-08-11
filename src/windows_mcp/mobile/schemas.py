from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


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


class TaskNode(BaseModel):
    """A single node in a task DAG."""

    id: str = Field(description="Unique node identifier")
    skill: str = Field(description="Skill name or 'llm_fallback'")
    params: dict = Field(default_factory=dict, description="Resolved parameters for the skill")
    depends_on: list[str] = Field(default_factory=list, description="Node IDs this node depends on")
    on_failure: str = Field(default="abort", description="Failure strategy: skip, retry, abort, fallback")
    retry_count: int = Field(default=2, ge=0, le=5, description="Max retries when on_failure=retry")
    timeout_seconds: int = Field(default=30, ge=1, le=300, description="Max execution time per attempt")

    @field_validator("on_failure")
    @classmethod
    def validate_on_failure(cls, v: str) -> str:
        allowed = {"skip", "retry", "abort", "fallback"}
        if v not in allowed:
            raise ValueError(f"on_failure must be one of {allowed}, got '{v}'")
        return v


class TaskGraph(BaseModel):
    """A directed acyclic graph of task nodes."""

    nodes: list[TaskNode] = Field(description="All nodes in the graph")
    global_context: dict = Field(default_factory=dict, description="Initial shared context")


class NodeTrace(BaseModel):
    """Execution trace for a single node."""

    node_id: str
    skill: str
    success: bool
    result: SkillResult | None = None
    attempts: int = 0
    error: str | None = None
    duration_ms: float = 0


class ExecutionTrace(BaseModel):
    """Complete execution trace for a task graph."""

    nodes: list[NodeTrace] = Field(default_factory=list)
    final_context: dict = Field(default_factory=dict)
    overall_success: bool = False
