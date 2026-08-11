import json

from windows_mcp.mobile.a2a import DesktopA2AOrchestrator
from windows_mcp.mobile.agent import AgentRunOutput
from windows_mcp.mobile.schemas import ScreenshotPayload
from windows_mcp.mobile.skills import build_default_skill_registry


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def open_app(self, *, name: str) -> str:
        self.calls.append(("open_app", name))
        return f"{name} launched."

    def switch_app(self, *, name: str) -> str:
        self.calls.append(("switch_app", name))
        return f"Switched to {name}."

    def capture_completion_screenshot(self) -> ScreenshotPayload:
        return ScreenshotPayload(base64_data="ZmFrZQ==", summary="done")


class FakeFallbackAgent:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    def run_instruction(
        self,
        instruction: str,
        model: str | None = None,
        should_stop=None,
    ) -> AgentRunOutput:
        self.instructions.append(instruction)
        return AgentRunOutput(
            message="fallback handled request",
            screenshot=ScreenshotPayload(base64_data="ZmFrZQ==", summary="browser"),
            raw_agent_response="fallback-result",
        )


def test_a2a_routes_open_instruction_to_skill():
    tools = FakeTools()
    fallback_agent = FakeFallbackAgent()
    orchestrator = DesktopA2AOrchestrator(
        tools=tools,
        fallback_agent=fallback_agent,
        skill_registry=build_default_skill_registry(),
    )

    result = orchestrator.run_instruction("打开QQ")

    assert tools.calls == [("open_app", "QQ")]
    assert result.message == "操作已完成，当前界面状态如下："
    trace = json.loads(result.raw_agent_response or "{}")
    assert trace["planner_route"] == "skill"
    assert trace["selected_skill"] == "open_or_focus_app"
    assert fallback_agent.instructions == []


def test_a2a_routes_search_instruction_to_browser_skill_and_fallback_agent():
    tools = FakeTools()
    fallback_agent = FakeFallbackAgent()
    orchestrator = DesktopA2AOrchestrator(
        tools=tools,
        fallback_agent=fallback_agent,
        skill_registry=build_default_skill_registry(),
    )

    result = orchestrator.run_instruction("搜索山东大学官网")

    assert result.message == "操作已完成，当前界面状态如下："
    assert fallback_agent.instructions
    assert "browser-first workflow" in fallback_agent.instructions[0]
    trace = json.loads(result.raw_agent_response or "{}")
    assert trace["selected_skill"] == "browser_search"
