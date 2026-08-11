from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
import json
import os
import re
import time

import requests

from windows_mcp.mobile.schemas import ScreenshotPayload, TaskResult
from windows_mcp.mobile.tools import DesktopAutomationTools


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentRunOutput:
    message: str
    screenshot: ScreenshotPayload
    raw_agent_response: str | None = None

    def to_task_result(self) -> TaskResult:
        return TaskResult(
            message=self.message,
            screenshot=self.screenshot,
            raw_agent_response=self.raw_agent_response,
            completed_at=utc_now(),
        )


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolExecutionResult:
    tool_use_id: str
    content: str


@dataclass
class ProviderTurn:
    assistant_message: dict[str, Any]
    text: str
    tool_calls: list[ToolCall]


class InstructionAgent(Protocol):
    def run_instruction(
        self,
        instruction: str,
        model: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentRunOutput:
        """Run a natural-language instruction and return the final mobile-facing result."""


class TaskCancelledError(RuntimeError):
    """Raised when a running mobile task is cancelled by the user."""


class ModelProvider(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
    ) -> ProviderTurn:
        """Complete one model turn and return normalized text/tool calls."""

    def build_tool_result_messages(
        self,
        results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        """Convert tool outputs into provider-specific follow-up messages."""


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.client = client or self._build_client(api_key=api_key, base_url=base_url)

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
    ) -> ProviderTurn:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        assistant_blocks = [self._serialize_block(block) for block in response.content]
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
            elif block_type == "text":
                text_parts.append(block.text)
        return ProviderTurn(
            assistant_message={"role": "assistant", "content": assistant_blocks},
            text="\n".join(part.strip() for part in text_parts if part.strip()).strip(),
            tool_calls=tool_calls,
        )

    def build_tool_result_messages(
        self,
        results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_use_id,
                        "content": result.content,
                    }
                    for result in results
                ],
            }
        ]

    @staticmethod
    def _build_client(*, api_key: str, base_url: str | None) -> Any:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package is required for Anthropic provider support.") from exc
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return Anthropic(**kwargs)

    @staticmethod
    def _serialize_block(block: Any) -> dict[str, Any]:
        if hasattr(block, "model_dump"):
            return block.model_dump()
        if isinstance(block, dict):
            return block
        return {
            key: value
            for key, value in vars(block).items()
            if not key.startswith("_") and value is not None
        }


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        session: requests.Session | None = None,
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
    ) -> ProviderTurn:
        payload = {
            "model": model,
            "messages": self._with_system_prompt(messages, system_prompt),
            "tools": self._transform_tools(tools),
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]

        text = self._extract_text(message.get("content"))
        raw_tool_calls = message.get("tool_calls") or []
        tool_calls = [
            ToolCall(
                id=tool_call["id"],
                name=tool_call["function"]["name"],
                input=self._parse_arguments(tool_call["function"].get("arguments")),
            )
            for tool_call in raw_tool_calls
        ]
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content") or "",
        }
        if raw_tool_calls:
            assistant_message["tool_calls"] = raw_tool_calls
        return ProviderTurn(
            assistant_message=assistant_message,
            text=text,
            tool_calls=tool_calls,
        )

    def build_tool_result_messages(
        self,
        results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "tool",
                "tool_call_id": result.tool_use_id,
                "content": result.content,
            }
            for result in results
        ]

    @staticmethod
    def _with_system_prompt(
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> list[dict[str, Any]]:
        return [{"role": "system", "content": system_prompt}, *messages]

    @staticmethod
    def _transform_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        parts.append(str(item["text"]).strip())
                    elif item.get("content"):
                        parts.append(str(item["content"]).strip())
            return "\n".join(part for part in parts if part).strip()
        if content is None:
            return ""
        return str(content).strip()

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            stripped = arguments.strip()
            if not stripped:
                return {}
            return json.loads(stripped)
        raise ValueError(f"Unsupported tool arguments payload: {arguments!r}")


class DesktopAgent:
    """Configurable model loop that drives local Windows automation tools."""

    def __init__(
        self,
        tools: DesktopAutomationTools,
        *,
        provider_name: str | None = None,
        model: str | None = None,
        max_steps: int = 12,
        max_tokens: int = 1024,
        provider: ModelProvider | None = None,
        client: Any | None = None,
    ) -> None:
        self.tools = tools
        self.provider_name = self._resolve_provider_name(provider_name)
        self.default_model = model or self._resolve_model_name(self.provider_name)
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.provider = provider or self._build_provider(self.provider_name, client=client)
        self.system_prompt = (
            "You are a Windows desktop control agent. "
            "Always inspect the current desktop before acting unless the task is trivial. "
            "Prefer labels from snapshot output over raw coordinates whenever possible. "
            "When the task is complete, answer briefly in Chinese."
        )

    def run_instruction(
        self,
        instruction: str,
        model: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentRunOutput:
        self._raise_if_cancelled(should_stop)
        shortcut_result = self._try_simple_instruction(instruction, should_stop)
        if shortcut_result is not None:
            return shortcut_result

        messages: list[dict[str, Any]] = [{"role": "user", "content": instruction}]
        final_text = ""
        tools = self.tools.tool_definitions()

        for _ in range(self.max_steps):
            self._raise_if_cancelled(should_stop)
            turn = self.provider.complete(
                messages=messages,
                system_prompt=self.system_prompt,
                tools=tools,
                model=model or self.default_model,
                max_tokens=self.max_tokens,
            )
            messages.append(turn.assistant_message)

            if turn.tool_calls:
                tool_results: list[ToolExecutionResult] = []
                for tool_call in turn.tool_calls:
                    self._raise_if_cancelled(should_stop)
                    tool_results.append(
                        ToolExecutionResult(
                            tool_use_id=tool_call.id,
                            content=self.tools.execute(tool_call.name, tool_call.input),
                        )
                    )
                    self._raise_if_cancelled(should_stop)
                messages.extend(self.provider.build_tool_result_messages(tool_results))
                continue

            final_text = turn.text
            break
        else:
            raise RuntimeError("Agent exceeded the maximum number of tool iterations.")

        completion_message = "该操作已完成。"
        if final_text:
            if not final_text.startswith(completion_message):
                separator = "" if completion_message.endswith(("。", "！", "？", ".", "!", "?")) else " "
                completion_message = f"{completion_message}{separator}{final_text}"
            else:
                completion_message = final_text

        self._raise_if_cancelled(should_stop)
        screenshot = self.tools.capture_completion_screenshot()
        return AgentRunOutput(
            message=completion_message,
            screenshot=screenshot,
            raw_agent_response=final_text or None,
        )

    def _try_simple_instruction(
        self,
        instruction: str,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentRunOutput | None:
        normalized = self._normalize_instruction(instruction)
        if not normalized:
            return None

        launch_match = re.fullmatch(
            r"(?:请|请你|帮我|麻烦|帮忙)?(?:打开|启动|运行)(.+)",
            normalized,
        )
        if launch_match:
            app_name = self._clean_app_name(launch_match.group(1))
            if app_name:
                self._raise_if_cancelled(should_stop)
                execution_text = self.tools.open_app(name=app_name)
                self._raise_if_cancelled(should_stop)
                time.sleep(2)
                self._raise_if_cancelled(should_stop)
                screenshot = self.tools.capture_completion_screenshot()
                return AgentRunOutput(
                    message=f"该操作已完成。已尝试打开{app_name}。",
                    screenshot=screenshot,
                    raw_agent_response=execution_text,
                )

        switch_match = re.fullmatch(
            r"(?:请|请你|帮我|麻烦|帮忙)?(?:切换到|切到|切换至)(.+)",
            normalized,
        )
        if switch_match:
            app_name = self._clean_app_name(switch_match.group(1))
            if app_name:
                self._raise_if_cancelled(should_stop)
                execution_text = self.tools.switch_app(name=app_name)
                self._raise_if_cancelled(should_stop)
                time.sleep(1)
                self._raise_if_cancelled(should_stop)
                screenshot = self.tools.capture_completion_screenshot()
                return AgentRunOutput(
                    message=f"该操作已完成。已尝试切换到{app_name}。",
                    screenshot=screenshot,
                    raw_agent_response=execution_text,
                )

        return None

    @staticmethod
    def _normalize_instruction(instruction: str) -> str:
        return instruction.strip().strip("。！？!?，,、 ")

    @staticmethod
    def _clean_app_name(name: str) -> str:
        cleaned = name.strip().strip("。！？!?，,、 ")
        cleaned = re.sub(r"(这个|一个)?(应用|软件|程序)$", "", cleaned).strip()
        return cleaned

    @staticmethod
    def _raise_if_cancelled(should_stop: Callable[[], bool] | None) -> None:
        if should_stop and should_stop():
            raise TaskCancelledError("任务已终止。")

    @staticmethod
    def _resolve_provider_name(provider_name: str | None) -> str:
        value = provider_name or os.getenv("MODEL_PROVIDER", "anthropic")
        normalized = value.strip().lower().replace("-", "_")
        aliases = {
            "claude": "anthropic",
            "anthropic": "anthropic",
            "openai": "openai_compatible",
            "openai_compatible": "openai_compatible",
            "compatible": "openai_compatible",
        }
        if normalized not in aliases:
            raise RuntimeError(
                "MODEL_PROVIDER must be one of: anthropic, claude, openai, openai_compatible."
            )
        return aliases[normalized]

    @staticmethod
    def _resolve_model_name(provider_name: str) -> str:
        generic = os.getenv("MODEL_NAME")
        if generic:
            return generic
        if provider_name == "anthropic":
            return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        raise RuntimeError("MODEL_NAME is required when using a non-Anthropic provider.")

    def _build_provider(self, provider_name: str, *, client: Any | None) -> ModelProvider:
        if provider_name == "anthropic":
            api_key = os.getenv("MODEL_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("MODEL_API_KEY or ANTHROPIC_API_KEY is required.")
            base_url = os.getenv("MODEL_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
            return AnthropicProvider(api_key=api_key, base_url=base_url, client=client)

        api_key = (
            os.getenv("MODEL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "MODEL_API_KEY is required for openai_compatible provider "
                "(OPENAI_API_KEY / DASHSCOPE_API_KEY are also accepted as fallbacks)."
            )
        base_url = os.getenv("MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "MODEL_BASE_URL is required for openai_compatible provider "
                "(for example https://api.deepseek.com/v1 or "
                "https://dashscope.aliyuncs.com/compatible-mode/v1)."
            )
        if client is not None and not isinstance(client, requests.Session):
            raise RuntimeError("client must be a requests.Session when provider is openai_compatible.")
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url,
            session=client,
        )


# Backward-compatible alias for existing imports.
ClaudeDesktopAgent = DesktopAgent
