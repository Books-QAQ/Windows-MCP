from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio

from uuid_extensions import uuid7str

from windows_mcp.mobile.schemas import TaskResult, TaskStatus, TaskView


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TaskRecord:
    id: str
    instruction: str
    model: str | None
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: TaskResult | None = None
    cancel_requested: bool = False

    def to_view(self) -> TaskView:
        return TaskView(
            id=self.id,
            status=self.status,
            instruction=self.instruction,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error=self.error,
            result=self.result,
            cancel_requested=self.cancel_requested,
        )


class TaskStore:
    """In-memory task store for the first mobile gateway iteration."""

    def __init__(self) -> None:
        self._items: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, instruction: str, model: str | None = None) -> TaskRecord:
        record = TaskRecord(
            id=uuid7str(),
            instruction=instruction,
            model=model,
            status=TaskStatus.PENDING,
            created_at=utc_now(),
        )
        async with self._lock:
            self._items[record.id] = record
        return record

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            return self._items.get(task_id)

    async def mark_running(self, task_id: str) -> TaskRecord:
        async with self._lock:
            record = self._items[task_id]
            if record.status == TaskStatus.PENDING:
                record.status = TaskStatus.RUNNING
                record.started_at = utc_now()
            return record

    async def request_cancel(self, task_id: str) -> TaskRecord:
        async with self._lock:
            record = self._items[task_id]
            record.cancel_requested = True
            if record.status == TaskStatus.PENDING:
                record.status = TaskStatus.CANCELLED
                record.error = "任务已终止。"
                record.completed_at = utc_now()
            elif record.status == TaskStatus.RUNNING:
                record.status = TaskStatus.CANCELLING
                record.error = "已收到终止请求，正在等待当前步骤安全结束。"
            return record

    async def mark_completed(self, task_id: str, result: TaskResult) -> TaskRecord:
        async with self._lock:
            record = self._items[task_id]
            if record.cancel_requested:
                record.status = TaskStatus.CANCELLED
                record.error = "任务已终止。当前步骤可能已经部分执行。"
                record.completed_at = utc_now()
                record.result = None
                return record
            record.status = TaskStatus.COMPLETED
            record.result = result
            record.completed_at = result.completed_at
            record.error = None
            return record

    async def mark_failed(self, task_id: str, error: str) -> TaskRecord:
        async with self._lock:
            record = self._items[task_id]
            if record.cancel_requested:
                record.status = TaskStatus.CANCELLED
                record.error = "任务已终止。当前步骤可能已经部分执行。"
                record.completed_at = utc_now()
                record.result = None
                return record
            record.status = TaskStatus.FAILED
            record.error = error
            record.completed_at = utc_now()
            return record

    async def mark_cancelled(self, task_id: str, reason: str = "任务已终止。") -> TaskRecord:
        async with self._lock:
            record = self._items[task_id]
            record.cancel_requested = True
            record.status = TaskStatus.CANCELLED
            record.error = reason
            record.completed_at = utc_now()
            record.result = None
            return record
