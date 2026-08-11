import asyncio
import json
import os
import subprocess
import sys
import threading

from windows_mcp.mobile.agent import AgentRunOutput, InstructionAgent, TaskCancelledError
from windows_mcp.mobile.schemas import ScreenshotPayload, TaskStatus, TaskView
from windows_mcp.mobile.store import TaskRecord, TaskStore


class MobileTaskService:
    """Coordinates request handling, task execution, and task status updates."""

    def __init__(
        self,
        agent: InstructionAgent | None = None,
        store: TaskStore | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.agent = agent
        self.store = store or TaskStore()
        self._cancel_events: dict[str, threading.Event] = {}
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._python_executable = python_executable or sys.executable
        self._closed = False

    async def run_now(self, instruction: str, model: str | None = None) -> TaskView:
        record = await self.store.create(instruction=instruction, model=model)
        self._cancel_events[record.id] = threading.Event()
        await self._execute_record(record)
        refreshed = await self.store.get(record.id)
        assert refreshed is not None
        return refreshed.to_view()

    async def create_task(self, instruction: str, model: str | None = None) -> TaskView:
        record = await self.store.create(instruction=instruction, model=model)
        self._cancel_events[record.id] = threading.Event()
        task = asyncio.create_task(self._execute_record(record))
        self._background_tasks[record.id] = task
        task.add_done_callback(lambda _: self._background_tasks.pop(record.id, None))
        return record.to_view()

    async def get_task(self, task_id: str) -> TaskView | None:
        record = await self.store.get(task_id)
        return None if record is None else record.to_view()

    async def cancel_task(self, task_id: str) -> TaskView | None:
        record = await self.store.get(task_id)
        if record is None:
            return None

        cancel_event = self._cancel_events.setdefault(task_id, threading.Event())
        cancel_event.set()
        updated = await self.store.request_cancel(task_id)
        await self._terminate_process(task_id)
        return updated.to_view()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        for cancel_event in self._cancel_events.values():
            cancel_event.set()

        for task_id in list(self._processes):
            await self._terminate_process(task_id)

        if self._background_tasks:
            await asyncio.wait(list(self._background_tasks.values()), timeout=2.0)

    async def _execute_record(self, record: TaskRecord) -> None:
        cancel_event = self._cancel_events.setdefault(record.id, threading.Event())

        if cancel_event.is_set():
            await self.store.mark_cancelled(record.id)
            return

        running_record = await self.store.mark_running(record.id)
        if running_record.status == TaskStatus.CANCELLED:
            return

        try:
            if self.agent is not None:
                result = await self._execute_inline(record, cancel_event)
            else:
                result = await self._execute_subprocess(record, cancel_event)
            if result is None:
                return
            await self.store.mark_completed(record.id, self._as_task_result(result))
        except TaskCancelledError:
            await self.store.mark_cancelled(record.id, "任务已终止。")
        except Exception as exc:
            await self.store.mark_failed(record.id, str(exc))

    async def _execute_inline(
        self,
        record: TaskRecord,
        cancel_event: threading.Event,
    ) -> AgentRunOutput | None:
        assert self.agent is not None
        result = await asyncio.to_thread(
            self.agent.run_instruction,
            record.instruction,
            record.model,
            cancel_event.is_set,
        )
        if cancel_event.is_set():
            await self.store.mark_cancelled(record.id, "任务已终止。当前步骤可能已经部分执行。")
            return None
        return result

    async def _execute_subprocess(
        self,
        record: TaskRecord,
        cancel_event: threading.Event,
    ) -> AgentRunOutput | None:
        if cancel_event.is_set():
            await self.store.mark_cancelled(record.id)
            return None

        payload = json.dumps(
            {
                "instruction": record.instruction,
                "model": record.model,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            self._python_executable,
            "-m",
            "windows_mcp.mobile.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        self._processes[record.id] = process

        try:
            stdout, stderr = await process.communicate(payload)
        finally:
            self._processes.pop(record.id, None)

        if cancel_event.is_set():
            await self.store.mark_cancelled(record.id, "任务已终止。")
            return None

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            if not error_text:
                error_text = stdout.decode("utf-8", errors="replace").strip()
            if not error_text:
                error_text = f"Worker exited with code {process.returncode}."
            raise RuntimeError(error_text)

        return self._decode_worker_output(stdout)

    async def _terminate_process(self, task_id: str) -> None:
        process = self._processes.get(task_id)
        if process is None or process.returncode is not None:
            return

        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _decode_worker_output(stdout: bytes) -> AgentRunOutput:
        raw_text = stdout.decode("utf-8", errors="replace").strip()
        if not raw_text:
            raise RuntimeError("Worker returned no output.")

        payload_line = raw_text.splitlines()[-1]
        payload = json.loads(payload_line)
        screenshot = payload.get("screenshot")
        if screenshot is None:
            raise RuntimeError("Worker result did not include a screenshot.")

        return AgentRunOutput(
            message=payload["message"],
            screenshot=ScreenshotPayload(**screenshot),
            raw_agent_response=payload.get("raw_agent_response"),
        )

    @staticmethod
    def _as_task_result(result: AgentRunOutput):
        return result.to_task_result()
