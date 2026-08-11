"""Smart validation layer for skill execution results.

Provides rule-based heuristic checks per skill type and optional vision-model
verification. Returns structured ValidationResult with confidence scores and
suggested recovery actions.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
import base64
import logging
import os

from windows_mcp.mobile.schemas import ScreenshotPayload, SkillResult

logger = logging.getLogger(__name__)

# ── data models ──────────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """Result of validating a skill execution."""

    success: bool
    confidence: float = 1.0  # 0.0–1.0
    method: str = "rule"  # "rule" | "vision" | "assumed"
    reason: str = ""
    suggested_action: str = "proceed"  # "proceed" | "retry" | "skip" | "abort" | "replan"
    evidence: dict[str, Any] = field(default_factory=dict)


# ── rule-based checks per skill ──────────────────────────────────────────────


def _check_open_or_focus(result: SkillResult) -> ValidationResult:
    """Check if the target app process is running."""
    app_name = result.data.get("app_name", "")
    if not app_name:
        return ValidationResult(
            success=False, confidence=0.5, reason="No app_name in skill result.",
            suggested_action="retry",
        )
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                if app_name.lower() in pname or pname.startswith(app_name.lower()):
                    return ValidationResult(
                        success=True, confidence=0.9, reason=f"Process matching '{app_name}' found.",
                        evidence={"process_name": pname},
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return ValidationResult(
            success=False, confidence=0.3, reason=f"No process matching '{app_name}'.",
            suggested_action="retry",
        )
    except ImportError:
        return ValidationResult(success=True, confidence=0.5, reason="psutil not available, assuming success.")


def _check_file_operation(result: SkillResult) -> ValidationResult:
    """Check file existence and expected size."""
    path = result.data.get("path", "")
    if not path:
        return ValidationResult(success=True, confidence=0.3, reason="No path in result, assuming success.")
    if os.path.exists(path):
        expected_size = result.data.get("size")
        actual_size = os.path.getsize(path)
        evidence = {"path": path, "size": actual_size}
        if expected_size is not None and expected_size != actual_size:
            return ValidationResult(
                success=False, confidence=0.7,
                reason=f"Size mismatch: expected {expected_size}, got {actual_size}.",
                suggested_action="retry", evidence=evidence,
            )
        return ValidationResult(
            success=True, confidence=0.9, reason=f"File exists at {path}.",
            evidence=evidence,
        )
    return ValidationResult(
        success=False, confidence=0.8, reason=f"File not found: {path}.",
        suggested_action="retry",
    )


def _check_clipboard(result: SkillResult) -> ValidationResult:
    """Check clipboard content matches expected."""
    action = result.data.get("action", "")
    if action == "set":
        expected = result.data.get("text", "")
        try:
            import pyperclip
            actual = pyperclip.paste()
            if expected in actual or actual in expected:
                return ValidationResult(
                    success=True, confidence=0.9,
                    reason="Clipboard content matches.",
                    evidence={"expected_len": len(expected), "actual_len": len(actual)},
                )
            return ValidationResult(
                success=False, confidence=0.7,
                reason="Clipboard content does not match.",
                suggested_action="retry",
            )
        except ImportError:
            return ValidationResult(success=True, confidence=0.5, reason="pyperclip not available.")
    return ValidationResult(success=True, confidence=0.6, reason="Clipboard read — cannot verify content.")


def _check_capture(result: SkillResult) -> ValidationResult:
    """Screenshot capture always succeeds if SkillResult is success."""
    if result.screenshot and result.screenshot.base64_data:
        return ValidationResult(success=True, confidence=0.95, reason="Screenshot captured.")
    return ValidationResult(success=True, confidence=0.5, reason="No screenshot in result, assuming success.")


def _check_fallback(result: SkillResult) -> ValidationResult:
    """LLM fallback: we trust the agent's self-report."""
    return ValidationResult(success=True, confidence=0.4, reason="LLM fallback — assuming success.", method="assumed")


RULE_CHECKS: dict[str, Callable[[SkillResult], ValidationResult]] = {
    "open_or_focus_app": _check_open_or_focus,
    "capture_desktop_state": _check_capture,
    "browser_search": lambda r: ValidationResult(success=True, confidence=0.4, reason="Browser search — assuming success."),
    "file_operation": _check_file_operation,
    "clipboard_operation": _check_clipboard,
    "llm_fallback": _check_fallback,
}


# ── SmartValidator ───────────────────────────────────────────────────────────


class SmartValidator:
    """Multi-layered skill execution validator.

    Layer 1 (fast, free): Rule-based heuristic checks per skill type.
    Layer 2 (slow, API): Vision model comparison of before/after screenshots.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        enable_vision: bool = False,
    ) -> None:
        self.api_key = api_key or os.getenv("VALIDATOR_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("VALIDATOR_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
        self.model = model or os.getenv("VALIDATOR_MODEL") or "deepseek-chat"
        self.enable_vision = enable_vision

    def validate(
        self,
        skill_name: str,
        result: SkillResult,
        before_screenshot: ScreenshotPayload | None = None,
        after_screenshot: ScreenshotPayload | None = None,
    ) -> ValidationResult:
        """Validate a skill execution result."""
        # Layer 1: rule-based
        checker = RULE_CHECKS.get(skill_name)
        if checker:
            rule_result = checker(result)
            if rule_result.confidence >= 0.8:
                return rule_result
        else:
            rule_result = ValidationResult(
                success=True, confidence=0.3,
                reason=f"No rule check for skill '{skill_name}', assuming success.",
            )

        # Layer 2: vision model (if enabled and screenshots available)
        if self.enable_vision and self.api_key and before_screenshot and after_screenshot:
            try:
                vision_result = self._vision_check(
                    skill_name, before_screenshot, after_screenshot
                )
                if vision_result.confidence > rule_result.confidence:
                    return vision_result
            except Exception as exc:
                logger.warning("Vision validation failed: %s", exc)

        return rule_result

    def _vision_check(
        self,
        skill_name: str,
        before: ScreenshotPayload,
        after: ScreenshotPayload,
    ) -> ValidationResult:
        """Use a vision model to compare screenshots."""
        import requests

        prompt = (
            f"You are validating that a desktop automation skill '{skill_name}' "
            f"was executed successfully. Compare these two screenshots (before and after). "
            f"Did the intended action succeed? Answer with ONLY one word: YES or NO."
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{before.base64_data}",
                            "detail": "low",
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{after.base64_data}",
                            "detail": "low",
                        },
                    },
                ],
            }
        ]

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "messages": messages, "max_tokens": 10, "temperature": 0},
            timeout=30,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
        success = "YES" in answer
        return ValidationResult(
            success=success,
            confidence=0.85 if success else 0.7,
            method="vision",
            reason=f"Vision model answer: {answer}",
            suggested_action="proceed" if success else "retry",
        )
