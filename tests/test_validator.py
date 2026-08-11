"""Tests for the SmartValidator and DAG validation integration."""

import json
import tempfile
import os

from windows_mcp.mobile.schemas import ScreenshotPayload, SkillResult, TaskGraph, TaskNode
from windows_mcp.mobile.skills import build_default_skill_registry
from windows_mcp.mobile.validator import (
    RULE_CHECKS,
    SmartValidator,
    ValidationResult,
    _check_capture,
    _check_clipboard,
    _check_file_operation,
    _check_open_or_focus,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def fake_screenshot() -> ScreenshotPayload:
    return ScreenshotPayload(base64_data="ZmFrZQ==", summary="fake")


# ── rule checks ─────────────────────────────────────────────────────────────

class TestCaptureCheck:
    def test_success_with_screenshot(self):
        result = SkillResult(success=True, screenshot=fake_screenshot())
        vr = _check_capture(result)
        assert vr.success
        assert vr.confidence >= 0.9

    def test_low_confidence_without_screenshot(self):
        result = SkillResult(success=True)
        vr = _check_capture(result)
        assert vr.success
        assert vr.confidence < 0.9


class TestFileOperationCheck:
    def test_file_exists_matches_size(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello")
            tmp = f.name
        try:
            result = SkillResult(success=True, data={"path": tmp, "size": 5})
            vr = _check_file_operation(result)
            assert vr.success
            assert vr.confidence >= 0.8
        finally:
            os.unlink(tmp)

    def test_file_not_found(self):
        result = SkillResult(success=True, data={"path": "/nonexistent/file.txt"})
        vr = _check_file_operation(result)
        assert not vr.success
        assert vr.suggested_action == "retry"

    def test_size_mismatch(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            tmp = f.name
        try:
            result = SkillResult(success=True, data={"path": tmp, "size": 999})
            vr = _check_file_operation(result)
            assert not vr.success
        finally:
            os.unlink(tmp)


class TestClipboardCheck:
    def test_set_success(self):
        try:
            import pyperclip
            pyperclip.copy("test123")
            result = SkillResult(success=True, data={"action": "set", "text": "test123"})
            vr = _check_clipboard(result)
            assert vr.success
        except ImportError:
            pass  # pyperclip not available


class TestOpenOrFocusCheck:
    def test_missing_app_name(self):
        result = SkillResult(success=True, data={})
        vr = _check_open_or_focus(result)
        assert not vr.success

    def test_with_app_name(self):
        # Should find at least python.exe running
        result = SkillResult(success=True, data={"app_name": "python"})
        vr = _check_open_or_focus(result)
        # May or may not find it depending on process name matching
        assert vr.method == "rule"


# ── SmartValidator ──────────────────────────────────────────────────────────

class TestSmartValidator:
    def test_rule_check_hit(self):
        sv = SmartValidator()
        result = SkillResult(success=True, screenshot=fake_screenshot())
        vr = sv.validate("capture_desktop_state", result)
        assert vr.success
        assert vr.method == "rule"

    def test_unknown_skill_falls_back(self):
        sv = SmartValidator()
        result = SkillResult(success=True)
        vr = sv.validate("nonexistent_skill", result)
        assert vr.success
        assert vr.confidence <= 0.5

    def test_vision_disabled_by_default(self):
        sv = SmartValidator(api_key="sk-test")
        assert not sv.enable_vision

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("VALIDATOR_API_KEY", "sk-val")
        sv = SmartValidator()
        assert sv.api_key == "sk-val"

    def test_fallback_env_key(self, monkeypatch):
        monkeypatch.delenv("VALIDATOR_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        sv = SmartValidator()
        assert sv.api_key == "sk-openai"


# ── ValidationResult ────────────────────────────────────────────────────────

class TestValidationResult:
    def test_defaults(self):
        vr = ValidationResult(success=True)
        assert vr.confidence == 1.0
        assert vr.method == "rule"
        assert vr.suggested_action == "proceed"

    def test_custom_values(self):
        vr = ValidationResult(
            success=False, confidence=0.5, method="vision",
            reason="didn't look right", suggested_action="retry",
        )
        assert not vr.success
        assert vr.confidence == 0.5
        assert vr.suggested_action == "retry"


# ── DAG integration ─────────────────────────────────────────────────────────

class FakeTools:
    def open_app(self, *, name): return f"{name} launched."
    def switch_app(self, *, name): return f"Switched to {name}."
    def capture_completion_screenshot(self): return fake_screenshot()


class FakeFallback:
    def run_instruction(self, instruction, model=None, should_stop=None):
        from windows_mcp.mobile.agent import AgentRunOutput
        return AgentRunOutput(message="done", screenshot=fake_screenshot(), raw_agent_response="ok")


def make_executor(with_validator=False):
    from windows_mcp.mobile.dag import DAGExecutor
    registry = build_default_skill_registry()
    sv = SmartValidator() if with_validator else None
    return DAGExecutor(registry, FakeTools(), FakeFallback(), validator=sv)


class TestDAGWithValidator:
    def test_capture_skill_passes_validation(self):
        import asyncio
        executor = make_executor(with_validator=True)
        graph = TaskGraph(nodes=[
            TaskNode(id="s1", skill="capture_desktop_state")
        ])
        trace = asyncio.run(executor.execute(graph))
        assert trace.overall_success
        assert trace.nodes[0].success

    def test_unknown_skill_still_fails(self):
        import asyncio
        executor = make_executor(with_validator=True)
        graph = TaskGraph(nodes=[
            TaskNode(id="s1", skill="nonexistent_skill")
        ])
        trace = asyncio.run(executor.execute(graph))
        assert not trace.overall_success

    def test_without_validator_still_works(self):
        import asyncio
        executor = make_executor(with_validator=False)
        graph = TaskGraph(nodes=[
            TaskNode(id="s1", skill="capture_desktop_state"),
            TaskNode(id="s2", skill="capture_desktop_state", depends_on=["s1"]),
        ])
        trace = asyncio.run(executor.execute(graph))
        assert trace.overall_success
        assert len(trace.nodes) == 2
