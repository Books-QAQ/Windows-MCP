from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
import os
import re
import time
import tomllib

from windows_mcp.mobile.agent import AgentRunOutput, InstructionAgent, TaskCancelledError
from windows_mcp.mobile.schemas import SkillResult, SkillView
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
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


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
    "file_operation": SkillSpec(
        name="file_operation",
        description="Read, write, list, or search files on the local file system.",
        examples=("读取 C:/test.txt", "列出桌面文件", "写入hello到D:/output.txt"),
        triggers=("读取", "列出", "写入", "保存", "read", "list", "write"),
    ),
    "clipboard_operation": SkillSpec(
        name="clipboard_operation",
        description="Read the current clipboard content or write text to the clipboard.",
        examples=("获取剪贴板内容", "复制hello到剪贴板", "查看剪贴板"),
        triggers=("剪贴板", "clipboard", "复制", "粘贴"),
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

    def execute(self, context: SkillContext) -> SkillResult:
        """Execute the skill and return a structured result."""


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

    def execute(self, context: SkillContext) -> SkillResult:
        normalized = _trim_instruction(context.instruction)
        open_match = self._open_pattern.match(normalized)
        focus_match = self._focus_pattern.match(normalized)

        _raise_if_cancelled(context.should_stop)
        if open_match:
            app_name = _clean_app_name(open_match.group("app"))
            execution_text = context.tools.open_app(name=app_name)
            wait_seconds = self.open_wait_seconds
            action = "opened"
        elif focus_match:
            app_name = _clean_app_name(focus_match.group("app"))
            execution_text = context.tools.switch_app(name=app_name)
            wait_seconds = self.focus_wait_seconds
            action = "focused"
        else:
            return SkillResult(
                success=False,
                error="open_or_focus_app skill was selected without a matching instruction.",
                message="无法识别要操作的应用名称。",
            )

        _raise_if_cancelled(context.should_stop)
        time.sleep(wait_seconds)
        _raise_if_cancelled(context.should_stop)
        screenshot = context.tools.capture_completion_screenshot()
        return SkillResult(
            success=True,
            data={"app_name": app_name, "action": action, "raw_output": execution_text},
            message=f"Skill open_or_focus_app handled: {app_name}",
            screenshot=screenshot,
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

    def execute(self, context: SkillContext) -> SkillResult:
        _raise_if_cancelled(context.should_stop)
        screenshot = context.tools.capture_completion_screenshot()
        return SkillResult(
            success=True,
            data={"captured": True},
            message="Skill capture_desktop_state handled the request.",
            screenshot=screenshot,
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

    def execute(self, context: SkillContext) -> SkillResult:
        _raise_if_cancelled(context.should_stop)
        skill_prompt = f"{self.prompt_prefix} User request: {context.instruction}"
        agent_output = context.fallback_agent.run_instruction(
            skill_prompt,
            context.model,
            context.should_stop,
        )
        return SkillResult(
            success=True,
            data={
                "search_query": context.instruction,
                "agent_response": agent_output.raw_agent_response,
            },
            message=f"Skill browser_search handled: {context.instruction}",
            screenshot=agent_output.screenshot,
        )


class FileOperationSkill:
    """Handle file-system operations: read, write, list, search files."""

    _read_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:读取|阅读|查看|打开文件|read)\s*(?P<path>.+?)\s*$",
        re.IGNORECASE,
    )
    _list_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:列出|显示|list|ls)\s*(?P<path>.+?)\s*$",
        re.IGNORECASE,
    )
    _write_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:写入|保存|创建文件|write)\s*(?P<content>.+?)\s*(?:到|to)\s*(?P<path>.+?)\s*$",
        re.IGNORECASE,
    )

    def __init__(self, *, spec: SkillSpec | None = None, settings: dict[str, Any] | None = None) -> None:
        self.spec = spec or DEFAULT_SKILL_SPECS["file_operation"]
        self.settings = settings or {}

    def match_score(self, instruction: str) -> int:
        if self._read_pattern.match(instruction):
            return 85
        if self._list_pattern.match(instruction):
            return 80
        if self._write_pattern.match(instruction):
            return 75
        return 0

    def execute(self, context: SkillContext) -> SkillResult:
        _raise_if_cancelled(context.should_stop)
        normalized = _trim_instruction(context.instruction)

        read_match = self._read_pattern.match(normalized)
        list_match = self._list_pattern.match(normalized)
        write_match = self._write_pattern.match(normalized)

        if read_match:
            return self._handle_read(read_match, context)
        if list_match:
            return self._handle_list(list_match, context)
        if write_match:
            return self._handle_write(write_match, context)

        return SkillResult(
            success=False,
            error="No file operation pattern matched.",
            message="无法识别的文件操作指令。",
        )

    def _handle_read(self, match: re.Match, context: SkillContext) -> SkillResult:
        path = match.group("path").strip()
        target_path = self._resolve_path(path, context)
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
            return SkillResult(
                success=True,
                data={"path": target_path, "content": content[:5000], "size": len(content)},
                message=f"已读取文件 {target_path}，共 {len(content)} 字符。",
            )
        except FileNotFoundError:
            return SkillResult(success=False, error=f"文件不存在: {target_path}", message=f"找不到文件 {target_path}。")
        except PermissionError:
            return SkillResult(success=False, error=f"无权访问: {target_path}", message=f"没有权限读取 {target_path}。")
        except Exception as exc:
            return SkillResult(success=False, error=str(exc), message=f"读取文件失败: {exc}")

    def _handle_list(self, match: re.Match, context: SkillContext) -> SkillResult:
        path = match.group("path").strip()
        target_path = self._resolve_path(path, context)
        try:
            entries = os.listdir(target_path)
            files = [e for e in entries if os.path.isfile(os.path.join(target_path, e))]
            dirs = [e for e in entries if os.path.isdir(os.path.join(target_path, e))]
            return SkillResult(
                success=True,
                data={"path": target_path, "files": files, "directories": dirs, "total": len(entries)},
                message=f"目录 {target_path} 包含 {len(dirs)} 个文件夹、{len(files)} 个文件。",
            )
        except FileNotFoundError:
            return SkillResult(success=False, error=f"目录不存在: {target_path}", message=f"找不到目录 {target_path}。")
        except PermissionError:
            return SkillResult(success=False, error=f"无权访问: {target_path}", message=f"没有权限访问 {target_path}。")
        except Exception as exc:
            return SkillResult(success=False, error=str(exc), message=f"列出目录失败: {exc}")

    def _handle_write(self, match: re.Match, context: SkillContext) -> SkillResult:
        content = match.group("content").strip()
        path = match.group("path").strip()
        target_path = self._resolve_path(path, context)
        try:
            os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return SkillResult(
                success=True,
                data={"path": target_path, "size": len(content)},
                message=f"已写入 {len(content)} 字符到 {target_path}。",
            )
        except PermissionError:
            return SkillResult(success=False, error=f"无权写入: {target_path}", message=f"没有权限写入 {target_path}。")
        except Exception as exc:
            return SkillResult(success=False, error=str(exc), message=f"写入文件失败: {exc}")

    @staticmethod
    def _resolve_path(path: str, context: SkillContext) -> str:
        """Resolve a path: absolute paths kept as-is, relative paths resolved from Desktop."""
        if os.path.isabs(path):
            return path
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return os.path.join(desktop, path)


class ClipboardOperationSkill:
    """Handle clipboard read and write operations."""

    _get_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:获取|读取|查看|get|read)\s*(?:剪贴板|clipboard).*$",
        re.IGNORECASE,
    )
    _set_pattern = re.compile(
        r"^\s*(?:请|请你|帮我|麻烦|帮忙)?\s*(?:设置|写入|复制|set|write|copy)\s*(?P<text>.+?)\s*(?:到剪贴板|到clipboard|剪贴板|clipboard)\s*$",
        re.IGNORECASE,
    )

    def __init__(self, *, spec: SkillSpec | None = None, settings: dict[str, Any] | None = None) -> None:
        self.spec = spec or DEFAULT_SKILL_SPECS["clipboard_operation"]
        self.settings = settings or {}

    def match_score(self, instruction: str) -> int:
        if self._set_pattern.match(instruction):
            return 90
        if self._get_pattern.match(instruction):
            return 85
        return 0

    def execute(self, context: SkillContext) -> SkillResult:
        _raise_if_cancelled(context.should_stop)
        normalized = _trim_instruction(context.instruction)

        set_match = self._set_pattern.match(normalized)
        get_match = self._get_pattern.match(normalized)

        try:
            import pyperclip
        except ImportError:
            return SkillResult(
                success=False,
                error="pyperclip is not installed.",
                message="剪贴板功能需要安装 pyperclip 包。",
            )

        if set_match:
            text = set_match.group("text").strip()
            pyperclip.copy(text)
            return SkillResult(
                success=True,
                data={"action": "set", "text": text},
                message=f"已将内容写入剪贴板（{len(text)} 字符）。",
            )
        if get_match:
            text = pyperclip.paste()
            return SkillResult(
                success=True,
                data={"action": "get", "text": text, "size": len(text)},
                message=f"剪贴板当前内容（{len(text)} 字符）：\n{text[:500]}",
            )

        return SkillResult(
            success=False,
            error="No clipboard operation pattern matched.",
            message="无法识别的剪贴板操作指令。",
        )


SKILL_BUILDERS: dict[str, type[DesktopSkill]] = {
    "open_or_focus_app": OpenOrFocusAppSkill,
    "capture_desktop_state": CaptureDesktopSkill,
    "browser_search": BrowserSearchSkill,
    "file_operation": FileOperationSkill,
    "clipboard_operation": ClipboardOperationSkill,
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
                input_schema=skill.spec.input_schema or {},
                output_schema=skill.spec.output_schema or {},
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
    input_schema = entry.get("input_schema", default_spec.input_schema)
    output_schema = entry.get("output_schema", default_spec.output_schema)
    return SkillSpec(
        name=default_spec.name,
        description=str(entry.get("description", default_spec.description)),
        examples=tuple(str(example) for example in examples),
        triggers=tuple(str(trigger) for trigger in triggers),
        input_schema=input_schema if isinstance(input_schema, dict) else None,
        output_schema=output_schema if isinstance(output_schema, dict) else None,
    )


def _trim_instruction(instruction: str) -> str:
    return instruction.strip().strip("。！？!?，,、 ")


def _clean_app_name(name: str) -> str:
    cleaned = _trim_instruction(name)
    cleaned = re.sub(r"(这个|一个)?(应用|软件|程序)$", "", cleaned).strip()
    return cleaned
