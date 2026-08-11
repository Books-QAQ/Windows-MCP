from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
import os
import re
import time
import tomllib

from windows_mcp.mobile.agent import AgentRunOutput, InstructionAgent, TaskCancelledError
from windows_mcp.mobile.schemas import SkillView
from windows_mcp.mobile.tools import DesktopAutomationTools

DEFAULT_SKILL_CONFIG_ENV = "WINDOWS_MCP_SKILLS_CONFIG"
DEFAULT_SKILL_CONFIG_PATH = Path(__file__).with_name("skills.toml")


def _raise_if_cancelled(should_stop: Callable[[], bool] | None) -> None:
    if should_stop and should_stop():
        raise TaskCancelledError("Task cancelled.")


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    examples: tuple[str, ...]
    triggers: tuple[str, ...]


DEFAULT_SKILL_SPECS: dict[str, SkillSpec] = {
    "open_or_focus_app": SkillSpec(
        name="open_or_focus_app",
        description="Open an application or focus an existing window with a direct desktop action.",
        examples=("打开QQ", "启动微信", "切换到浏览器", "focus VS Code"),
        triggers=("打开", "启动", "运行", "切换到", "focus", "switch to"),
    ),
    "capture_desktop_state": SkillSpec(
        name="capture_desktop_state",
        description="Capture the current desktop state and return a screenshot without extra actions.",
        examples=("截个图", "看下当前界面", "当前桌面状态", "capture current desktop"),
        triggers=("截图", "当前界面", "桌面状态", "screen", "capture"),
    ),
    "browser_search": SkillSpec(
        name="browser_search",
        description="Use a browser-focused workflow to search the web and stop when results are visible.",
        examples=("搜索今天的 AI 新闻", "帮我查一下 SDU 官网", "search for Python asyncio guide"),
        triggers=("搜索", "查一下", "search", "look up"),
    ),
}


@dataclass
class SkillContext:
    instruction: str
    tools: DesktopAutomationTools
    fallback_agent: InstructionAgent
    model: str | None = None
    should_stop: Callable[[], bool] | None = None


class DesktopSkill(Protocol):
    spec: SkillSpec

    def match_score(self, instruction: str) -> int:
        """Return a positive score when the skill can handle the instruction."""

    def execute(self, context: SkillContext) -> AgentRunOutput:
        """Execute the skill and return a mobile-facing result."""


class OpenOrFocusAppSkill:
    _open_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:打开|启动|运行|open|launch|start)\s*(?P<app>.+?)\s*$",
        re.IGNORECASE,
    )
    _focus_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:切换到|切到|切换至|聚焦|focus|switch to)\s*(?P<app>.+?)\s*$",
        re.IGNORECASE,
    )

    def __init__(self, *, spec: SkillSpec | None = None, settings: dict[str, Any] | None = None) -> None:
        self.spec = spec or DEFAULT_SKILL_SPECS["open_or_focus_app"]
        settings = settings or {}
        self.open_wait_seconds = float(settings.get("open_wait_seconds", 2))
        self.focus_wait_seconds = float(settings.get("focus_wait_seconds", 1))

    def match_score(self, instruction: str) -> int:
        if self._open_pattern.match(instruction):
            return 100
        if self._focus_pattern.match(instruction):
            return 95
        return 0

    def execute(self, context: SkillContext) -> AgentRunOutput:
        normalized = _trim_instruction(context.instruction)
        open_match = self._open_pattern.match(normalized)
        focus_match = self._focus_pattern.match(normalized)

        _raise_if_cancelled(context.should_stop)
        if open_match:
            app_name = _clean_app_name(open_match.group("app"))
            execution_text = context.tools.open_app(name=app_name)
            wait_seconds = self.open_wait_seconds
        elif focus_match:
            app_name = _clean_app_name(focus_match.group("app"))
            execution_text = context.tools.switch_app(name=app_name)
            wait_seconds = self.focus_wait_seconds
        else:
            raise RuntimeError("open_or_focus_app skill was selected without a matching instruction.")

        _raise_if_cancelled(context.should_stop)
        time.sleep(wait_seconds)
        _raise_if_cancelled(context.should_stop)
        screenshot = context.tools.capture_completion_screenshot()
        return AgentRunOutput(
            message=f"Skill open_or_focus_app handled: {app_name}",
            screenshot=screenshot,
            raw_agent_response=execution_text,
        )


class CaptureDesktopSkill:
    _keywords = (
        "截图",
        "截个图",
        "当前界面",
        "当前桌面",
        "桌面状态",
        "现在界面",
        "capture",
        "screenshot",
        "current screen",
        "current desktop",
    )

    def __init__(self, *, spec: SkillSpec | None = None, settings: dict[str, Any] | None = None) -> None:
        self.spec = spec or DEFAULT_SKILL_SPECS["capture_desktop_state"]
        self.settings = settings or {}

    def match_score(self, instruction: str) -> int:
        normalized = instruction.lower()
        if any(keyword in normalized for keyword in self._keywords):
            return 80
        return 0

    def execute(self, context: SkillContext) -> AgentRunOutput:
        _raise_if_cancelled(context.should_stop)
        screenshot = context.tools.capture_completion_screenshot()
        return AgentRunOutput(
            message="Skill capture_desktop_state handled the request.",
            screenshot=screenshot,
            raw_agent_response="Captured the current desktop state.",
        )


class BrowserSearchSkill:
    _patterns = (
        re.compile(r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:搜索|查一下|查一查)\s*.+$", re.IGNORECASE),
        re.compile(r"^\s*(?:search for|look up)\s+.+$", re.IGNORECASE),
    )

    def __init__(self, *, spec: SkillSpec | None = None, settings: dict[str, Any] | None = None) -> None:
        self.spec = spec or DEFAULT_SKILL_SPECS["browser_search"]
        settings = settings or {}
        self.prompt_prefix = settings.get(
            "prompt_prefix",
            (
                "Use a browser-first workflow. Focus an existing browser if available, otherwise open one. "
                "Search for the user's target and stop when the relevant results page is visible."
            ),
        )

    def match_score(self, instruction: str) -> int:
        if any(pattern.match(instruction) for pattern in self._patterns):
            return 60
        return 0

    def execute(self, context: SkillContext) -> AgentRunOutput:
        _raise_if_cancelled(context.should_stop)
        skill_prompt = f"{self.prompt_prefix} User request: {context.instruction}"
        return context.fallback_agent.run_instruction(
            skill_prompt,
            context.model,
            context.should_stop,
        )


SKILL_BUILDERS: dict[str, type[DesktopSkill]] = {
    "open_or_focus_app": OpenOrFocusAppSkill,
    "capture_desktop_state": CaptureDesktopSkill,
    "browser_search": BrowserSearchSkill,
}


class SkillRegistry:
    def __init__(self, skills: list[DesktopSkill]) -> None:
        self._skills = skills

    @property
    def skills(self) -> list[DesktopSkill]:
        return list(self._skills)

    def select(self, instruction: str) -> DesktopSkill | None:
        best_skill: DesktopSkill | None = None
        best_score = 0
        for skill in self._skills:
            score = skill.match_score(instruction)
            if score > best_score:
                best_skill = skill
                best_score = score
        return best_skill

    def to_views(self) -> list[SkillView]:
        return [
            SkillView(
                name=skill.spec.name,
                description=skill.spec.description,
                examples=list(skill.spec.examples),
                triggers=list(skill.spec.triggers),
            )
            for skill in self._skills
        ]


def build_default_skill_registry() -> SkillRegistry:
    return load_skill_registry()


def load_skill_registry(config_path: str | Path | None = None) -> SkillRegistry:
    path = _resolve_skill_config_path(config_path)
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        skills = _build_skills_from_config(data)
        if skills:
            return SkillRegistry(skills)

    return SkillRegistry(_build_builtin_skills())


def _resolve_skill_config_path(config_path: str | Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    env_path = os.getenv(DEFAULT_SKILL_CONFIG_ENV)
    if env_path:
        return Path(env_path)
    return DEFAULT_SKILL_CONFIG_PATH


def _build_builtin_skills() -> list[DesktopSkill]:
    return [builder() for builder in SKILL_BUILDERS.values()]


def _build_skills_from_config(data: dict[str, Any]) -> list[DesktopSkill]:
    entries = data.get("skills")
    if not isinstance(entries, list):
        return []

    built_skills: list[DesktopSkill] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        if not entry.get("enabled", True):
            continue

        builder = SKILL_BUILDERS.get(name)
        if builder is None:
            raise RuntimeError(f"Unknown skill '{name}' in skill config.")

        spec = _merge_spec(DEFAULT_SKILL_SPECS[name], entry)
        settings = entry.get("settings")
        if settings is not None and not isinstance(settings, dict):
            raise RuntimeError(f"Skill '{name}' settings must be a TOML table or inline table.")
        built_skills.append(builder(spec=spec, settings=settings))

    return built_skills


def _merge_spec(default_spec: SkillSpec, entry: dict[str, Any]) -> SkillSpec:
    examples = entry.get("examples", default_spec.examples)
    triggers = entry.get("triggers", default_spec.triggers)
    return SkillSpec(
        name=default_spec.name,
        description=str(entry.get("description", default_spec.description)),
        examples=tuple(str(example) for example in examples),
        triggers=tuple(str(trigger) for trigger in triggers),
    )


def _trim_instruction(instruction: str) -> str:
    return instruction.strip().strip("。！？!?，,、 ")


def _clean_app_name(name: str) -> str:
    cleaned = _trim_instruction(name)
    cleaned = re.sub(r"(这个|一个)?(应用|软件|程序)$", "", cleaned).strip()
    return cleaned
