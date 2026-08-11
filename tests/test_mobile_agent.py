import json

from windows_mcp.mobile.agent import (
    AnthropicProvider,
    DesktopAgent,
    OpenAICompatibleProvider,
    ProviderTurn,
    ToolExecutionResult,
)
from windows_mcp.mobile.schemas import ScreenshotPayload


class FakeTools:
    def __init__(self):
        self.opened_apps: list[str] = []
        self.switched_apps: list[str] = []

    def tool_definitions(self):
        return [
            {
                "name": "snapshot",
                "description": "Inspect desktop",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    def execute(self, tool_name, tool_input):
        assert tool_name == "snapshot"
        return "Focused Window: Notepad"

    def open_app(self, *, name: str):
        self.opened_apps.append(name)
        return f"{name} launched."

    def switch_app(self, *, name: str):
        self.switched_apps.append(name)
        return f"Switched to {name}."

    def capture_completion_screenshot(self):
        return ScreenshotPayload(base64_data="ZmFrZQ==", summary="done")


class FakeAnthropicBlock:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAnthropicMessages:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        class Response:
            def __init__(self, content):
                self.content = content

        return Response(self._content)


class FakeAnthropicClient:
    def __init__(self, content):
        self.messages = FakeAnthropicMessages(content)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return FakeResponse(self.payload)


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, *, messages, system_prompt, tools, model, max_tokens):
        self.calls += 1
        if self.calls == 1:
            return ProviderTurn(
                assistant_message={"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]},
                text="",
                tool_calls=[type("Call", (), {"id": "tc1", "name": "snapshot", "input": {}})()],
            )
        return ProviderTurn(
            assistant_message={"role": "assistant", "content": "记事本已打开"},
            text="记事本已打开",
            tool_calls=[],
        )

    def build_tool_result_messages(self, results):
        return [{"role": "tool", "tool_call_id": results[0].tool_use_id, "content": results[0].content}]


def test_anthropic_provider_normalizes_tool_calls():
    provider = AnthropicProvider(
        api_key="test",
        client=FakeAnthropicClient(
            [
                FakeAnthropicBlock(type="text", text="先看一下"),
                FakeAnthropicBlock(type="tool_use", id="tool-1", name="snapshot", input={}),
            ]
        ),
    )

    turn = provider.complete(
        messages=[{"role": "user", "content": "打开记事本"}],
        system_prompt="system",
        tools=[{"name": "snapshot", "input_schema": {"type": "object"}}],
        model="claude-test",
        max_tokens=128,
    )

    assert turn.text == "先看一下"
    assert turn.tool_calls[0].id == "tool-1"
    assert turn.tool_calls[0].name == "snapshot"


def test_openai_compatible_provider_normalizes_tool_calls():
    session = FakeSession(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "snapshot",
                                    "arguments": json.dumps({}),
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        session=session,
    )

    turn = provider.complete(
        messages=[{"role": "user", "content": "打开记事本"}],
        system_prompt="system prompt",
        tools=[{"name": "snapshot", "description": "Inspect", "input_schema": {"type": "object"}}],
        model="deepseek-chat",
        max_tokens=128,
    )

    assert session.requests[0]["url"] == "https://example.com/v1/chat/completions"
    assert session.requests[0]["json"]["tools"][0]["function"]["name"] == "snapshot"
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].name == "snapshot"
    assert turn.tool_calls[0].input == {}


def test_desktop_agent_runs_tool_loop_with_generic_provider():
    agent = DesktopAgent(
        tools=FakeTools(),
        provider_name="anthropic",
        provider=FakeProvider(),
        model="fake-model",
    )

    result = agent.run_instruction("先检查桌面，再打开记事本")

    assert result.message == "该操作已完成。记事本已打开"
    assert result.screenshot.base64_data == "ZmFrZQ=="


def test_desktop_agent_shortcuts_simple_open_instruction_without_provider_call():
    tools = FakeTools()

    class UnusedProvider:
        def complete(self, **kwargs):
            raise AssertionError("simple instruction should not call provider")

        def build_tool_result_messages(self, results):
            raise AssertionError("simple instruction should not call provider")

    agent = DesktopAgent(
        tools=tools,
        provider_name="anthropic",
        provider=UnusedProvider(),
        model="fake-model",
    )

    result = agent.run_instruction("帮我打开QQ。")

    assert tools.opened_apps == ["QQ"]
    assert result.message == "该操作已完成。已尝试打开QQ。"
    assert result.raw_agent_response == "QQ launched."


def test_openai_compatible_provider_builds_tool_messages():
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        session=FakeSession({"choices": [{"message": {"content": "done"}}]}),
    )

    messages = provider.build_tool_result_messages(
        [ToolExecutionResult(tool_use_id="call_1", content="Focused Window: Notepad")]
    )

    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "Focused Window: Notepad",
        }
    ]
