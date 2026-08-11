from typing import Any
import base64

from windows_mcp.desktop.service import Desktop
from windows_mcp.mobile.schemas import ScreenshotPayload
from windows_mcp.tools._snapshot_helpers import build_snapshot_response, capture_desktop_state


class DesktopAutomationTools:
    """High-level tool adapter reused by the mobile Claude agent."""

    def __init__(self, desktop: Desktop) -> None:
        self.desktop = desktop

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "snapshot",
                "description": (
                    "Inspect the current desktop state before taking actions. "
                    "Returns focused window, open windows, interactive elements, and scrollable areas."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "use_dom": {
                            "type": "boolean",
                            "description": "Use browser DOM extraction when the active window is a browser.",
                            "default": False,
                        }
                    },
                },
            },
            {
                "name": "click",
                "description": "Click a UI element by label from snapshot output, or click coordinates.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "default": "left",
                        },
                        "clicks": {"type": "integer", "default": 1, "minimum": 0, "maximum": 2},
                    },
                },
            },
            {
                "name": "type_text",
                "description": "Type text into an input by label or coordinates.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "label": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "clear": {"type": "boolean", "default": False},
                        "press_enter": {"type": "boolean", "default": False},
                        "caret_position": {
                            "type": "string",
                            "enum": ["start", "idle", "end"],
                            "default": "idle",
                        },
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "open_app",
                "description": "Launch an application from the Windows start menu.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "switch_app",
                "description": "Switch focus to an already-open application window by name.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "shortcut",
                "description": "Press a keyboard shortcut like ctrl+l, ctrl+c, alt+tab, or enter.",
                "input_schema": {
                    "type": "object",
                    "properties": {"shortcut": {"type": "string"}},
                    "required": ["shortcut"],
                },
            },
            {
                "name": "wait",
                "description": "Pause briefly to let the UI update.",
                "input_schema": {
                    "type": "object",
                    "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 30}},
                    "required": ["seconds"],
                },
            },
        ]

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        match tool_name:
            case "snapshot":
                return self.snapshot(use_dom=bool(tool_input.get("use_dom", False)))
            case "click":
                return self.click(
                    x=tool_input.get("x"),
                    y=tool_input.get("y"),
                    label=tool_input.get("label"),
                    button=tool_input.get("button", "left"),
                    clicks=int(tool_input.get("clicks", 1)),
                )
            case "type_text":
                return self.type_text(
                    text=tool_input["text"],
                    x=tool_input.get("x"),
                    y=tool_input.get("y"),
                    label=tool_input.get("label"),
                    clear=bool(tool_input.get("clear", False)),
                    press_enter=bool(tool_input.get("press_enter", False)),
                    caret_position=tool_input.get("caret_position", "idle"),
                )
            case "open_app":
                return self.open_app(name=tool_input["name"])
            case "switch_app":
                return self.switch_app(name=tool_input["name"])
            case "shortcut":
                return self.shortcut(shortcut=tool_input["shortcut"])
            case "wait":
                return self.wait(seconds=int(tool_input["seconds"]))
            case _:
                raise ValueError(f"Unsupported tool: {tool_name}")

    def snapshot(self, *, use_dom: bool = False) -> str:
        capture_result = capture_desktop_state(
            self.desktop,
            use_vision=False,
            use_dom=use_dom,
            use_annotation=False,
            use_ui_tree=True,
            width_reference_line=None,
            height_reference_line=None,
            display=None,
            tool_name="Mobile snapshot",
        )
        response = build_snapshot_response(capture_result, include_ui_details=True)
        return str(response[0]).strip()

    def click(
        self,
        *,
        x: int | None = None,
        y: int | None = None,
        label: int | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> str:
        loc = self._resolve_loc(x=x, y=y, label=label)
        self.desktop.click(loc=loc, button=button, clicks=clicks)
        action = {0: "Hovered", 1: "Clicked", 2: "Double-clicked"}.get(clicks, "Clicked")
        return f"{action} {button} button at ({loc[0]},{loc[1]})."

    def type_text(
        self,
        *,
        text: str,
        x: int | None = None,
        y: int | None = None,
        label: int | None = None,
        clear: bool = False,
        press_enter: bool = False,
        caret_position: str = "idle",
    ) -> str:
        loc = self._resolve_loc(x=x, y=y, label=label)
        self.desktop.type(
            loc=loc,
            text=text,
            clear=clear,
            press_enter=press_enter,
            caret_position=caret_position,
        )
        return f"Typed text at ({loc[0]},{loc[1]})."

    def open_app(self, *, name: str) -> str:
        if self.desktop.is_app_running(name):
            response = self.desktop.app("switch", name, None, None)
            response_text = str(response)
            if "not found" not in response_text.lower():
                return response_text

        response = self.desktop.app("launch", name, None, None)
        return str(response)

    def switch_app(self, *, name: str) -> str:
        response = self.desktop.app("switch", name, None, None)
        return str(response)

    def shortcut(self, *, shortcut: str) -> str:
        self.desktop.shortcut(shortcut)
        return f"Pressed shortcut {shortcut}."

    def wait(self, *, seconds: int) -> str:
        import time

        time.sleep(seconds)
        return f"Waited for {seconds} seconds."

    def capture_completion_screenshot(self) -> ScreenshotPayload:
        capture_result = capture_desktop_state(
            self.desktop,
            use_vision=True,
            use_dom=False,
            use_annotation=False,
            use_ui_tree=False,
            width_reference_line=None,
            height_reference_line=None,
            display=None,
            tool_name="Mobile completion screenshot",
        )
        summary_text = build_snapshot_response(
            capture_result,
            include_ui_details=False,
            ui_detail_note="UI Tree: Skipped for final completion snapshot.",
        )[0]
        screenshot_bytes = capture_result["screenshot_bytes"]
        if not isinstance(screenshot_bytes, bytes):
            raise RuntimeError("Expected screenshot bytes for completion state.")
        return ScreenshotPayload(
            base64_data=base64.b64encode(screenshot_bytes).decode("ascii"),
            summary=str(summary_text).strip(),
        )

    def _resolve_loc(
        self,
        *,
        x: int | None,
        y: int | None,
        label: int | None,
    ) -> list[int]:
        if label is not None:
            if self.desktop.desktop_state is None:
                raise ValueError("No desktop state available. Call snapshot before using a label.")
            rx, ry = self.desktop.get_coordinates_from_label(label)
            return [rx, ry]
        if x is None or y is None:
            raise ValueError("Either label or both x and y must be provided.")
        return [x, y]
