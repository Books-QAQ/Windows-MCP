import threading
import time

from fastapi.testclient import TestClient

from windows_mcp.mobile.agent import AgentRunOutput, TaskCancelledError
from windows_mcp.mobile.api import create_app
from windows_mcp.mobile.schemas import ScreenshotPayload, TaskStatus
from windows_mcp.mobile.service import MobileTaskService


class FakeAgent:
    def run_instruction(self, instruction: str, model: str | None = None, should_stop=None) -> AgentRunOutput:
        return AgentRunOutput(
            message="该操作已完成。已打开记事本。",
            screenshot=ScreenshotPayload(
                base64_data="ZmFrZS1pbWFnZQ==",
                summary="Focused Window: Notepad",
            ),
            raw_agent_response=f"instruction={instruction}, model={model}",
        )


class SlowCancellableAgent:
    def __init__(self) -> None:
        self.started = threading.Event()

    def run_instruction(self, instruction: str, model: str | None = None, should_stop=None) -> AgentRunOutput:
        self.started.set()
        while True:
            if should_stop and should_stop():
                raise TaskCancelledError("任务已终止。")
            time.sleep(0.02)


def test_run_command_returns_completion_message_and_screenshot():
    app = create_app(task_service=MobileTaskService(agent=FakeAgent()))

    with TestClient(app) as client:
        index_response = client.get("/")
        assert index_response.status_code == 200
        assert "Windows MCP Mobile" in index_response.text
        assert "终止" in index_response.text
        skills_response = client.get("/mobile/skills")
        assert skills_response.status_code == 200
        assert any(skill["name"] == "open_or_focus_app" for skill in skills_response.json())

        response = client.post(
            "/mobile/commands/run",
            json={"instruction": "打开记事本"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == TaskStatus.COMPLETED
    assert payload["result"]["message"] == "该操作已完成。已打开记事本。"
    assert payload["result"]["screenshot"]["base64_data"] == "ZmFrZS1pbWFnZQ=="


def test_create_task_and_fetch_result():
    app = create_app(task_service=MobileTaskService(agent=FakeAgent()))

    with TestClient(app) as client:
        create_response = client.post(
            "/mobile/tasks",
            json={"instruction": "打开计算器", "model": "claude-test"},
        )
        assert create_response.status_code == 202
        task_id = create_response.json()["id"]

        result_response = client.get(f"/mobile/tasks/{task_id}")

    assert result_response.status_code == 200
    payload = result_response.json()
    assert payload["status"] in {TaskStatus.RUNNING, TaskStatus.COMPLETED}
    if payload["status"] == TaskStatus.COMPLETED:
        assert payload["result"]["raw_agent_response"] == "instruction=打开计算器, model=claude-test"


def test_cancel_running_task():
    agent = SlowCancellableAgent()
    app = create_app(task_service=MobileTaskService(agent=agent))

    with TestClient(app) as client:
        create_response = client.post("/mobile/tasks", json={"instruction": "打开QQ"})
        assert create_response.status_code == 202
        task_id = create_response.json()["id"]

        assert agent.started.wait(timeout=1.0)

        cancel_response = client.post(f"/mobile/tasks/{task_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] in {
            TaskStatus.CANCELLING,
            TaskStatus.CANCELLED,
        }

        deadline = time.time() + 2.0
        final_payload = None
        while time.time() < deadline:
            result_response = client.get(f"/mobile/tasks/{task_id}")
            assert result_response.status_code == 200
            final_payload = result_response.json()
            if final_payload["status"] == TaskStatus.CANCELLED:
                break
            time.sleep(0.05)

    assert final_payload is not None
    assert final_payload["status"] == TaskStatus.CANCELLED
    assert final_payload["cancel_requested"] is True
