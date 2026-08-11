"""Tests for the DAG executor and orchestration layer."""

import pytest

from windows_mcp.mobile.a2a import DesktopA2AOrchestrator
from windows_mcp.mobile.dag import DAGExecutor, DAGExecutionContext, build_simple_graph
from windows_mcp.mobile.schemas import (
    ExecutionTrace,
    SkillResult,
    TaskGraph,
    TaskNode,
)
from windows_mcp.mobile.skills import (
    SkillSpec,
    build_default_skill_registry,
)


# ── helpers ──────────────────────────────────────────────────────────────────

class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def open_app(self, *, name: str) -> str:
        self.calls.append(("open_app", {"name": name}))
        return f"{name} launched."

    def switch_app(self, *, name: str) -> str:
        self.calls.append(("switch_app", {"name": name}))
        return f"Switched to {name}."

    def capture_completion_screenshot(self):
        from windows_mcp.mobile.schemas import ScreenshotPayload
        return ScreenshotPayload(base64_data="fake", summary="done")


class FakeFallbackAgent:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    def run_instruction(self, instruction, model=None, should_stop=None):
        from windows_mcp.mobile.agent import AgentRunOutput
        from windows_mcp.mobile.schemas import ScreenshotPayload
        self.instructions.append(instruction)
        return AgentRunOutput(
            message="done",
            screenshot=ScreenshotPayload(base64_data="fake", summary="fallback"),
            raw_agent_response="fallback-result",
        )


def make_orchestrator():
    tools = FakeTools()
    fallback = FakeFallbackAgent()
    registry = build_default_skill_registry()
    return DesktopA2AOrchestrator(
        tools=tools, fallback_agent=fallback, skill_registry=registry
    )


# ── DAGExecutionContext tests ────────────────────────────────────────────────

class TestExecutionContext:
    def test_resolves_direct_references(self):
        ctx = DAGExecutionContext()
        ctx.node_results["step_1"] = SkillResult(success=True, data={"app": "Chrome"})
        resolved = ctx.resolve_params({"target": "$step_1"})
        assert resolved["target"] == {"app": "Chrome"}

    def test_resolves_field_references(self):
        ctx = DAGExecutionContext()
        ctx.node_results["step_1"] = SkillResult(success=True, data={"app": "Chrome"})
        resolved = ctx.resolve_params({"target": "$step_1.app"})
        assert resolved["target"] == "Chrome"

    def test_returns_none_for_failed_node(self):
        ctx = DAGExecutionContext()
        ctx.node_results["step_1"] = SkillResult(success=False, error="boom")
        resolved = ctx.resolve_params({"target": "$step_1.app"})
        assert resolved["target"] is None

    def test_returns_none_for_missing_node(self):
        ctx = DAGExecutionContext()
        resolved = ctx.resolve_params({"target": "$step_99.app"})
        assert resolved["target"] is None

    def test_passes_through_non_references(self):
        ctx = DAGExecutionContext()
        resolved = ctx.resolve_params({"plain": "hello", "num": 42})
        assert resolved == {"plain": "hello", "num": 42}


# ── build_simple_graph tests ─────────────────────────────────────────────────

class TestBuildSimpleGraph:
    def test_single_node(self):
        graph = build_simple_graph([{"skill": "open_or_focus_app"}])
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "step_1"
        assert graph.nodes[0].depends_on == []

    def test_sequential_chain(self):
        graph = build_simple_graph([
            {"skill": "open_or_focus_app"},
            {"skill": "capture_desktop_state"},
            {"skill": "browser_search"},
        ])
        assert len(graph.nodes) == 3
        assert graph.nodes[0].depends_on == []
        assert graph.nodes[1].depends_on == ["step_1"]
        assert graph.nodes[2].depends_on == ["step_2"]

    def test_explicit_dependencies(self):
        graph = build_simple_graph([
            {"skill": "open_or_focus_app", "id": "open"},
            {"skill": "capture_desktop_state", "id": "capture", "depends_on": []},
            {"skill": "browser_search", "id": "search", "depends_on": ["open", "capture"]},
        ])
        assert graph.nodes[0].depends_on == []
        assert graph.nodes[1].depends_on == []
        assert set(graph.nodes[2].depends_on) == {"open", "capture"}


# ── TaskGraph model tests ────────────────────────────────────────────────────

class TestTaskGraphModel:
    def test_validation_rejects_invalid_on_failure(self):
        with pytest.raises(ValueError):
            TaskNode(id="n1", skill="open_or_focus_app", on_failure="invalid")

    def test_validation_rejects_negative_retry(self):
        with pytest.raises(ValueError):
            TaskNode(id="n1", skill="open_or_focus_app", retry_count=-1)

    def test_validation_rejects_zero_timeout(self):
        with pytest.raises(ValueError):
            TaskNode(id="n1", skill="open_or_focus_app", timeout_seconds=0)

    def test_serialization_roundtrip(self):
        graph = TaskGraph(
            nodes=[
                TaskNode(id="step_1", skill="open_or_focus_app", params={"app_name": "QQ"}),
                TaskNode(id="step_2", skill="capture_desktop_state", depends_on=["step_1"]),
            ]
        )
        data = graph.model_dump()
        restored = TaskGraph.model_validate(data)
        assert len(restored.nodes) == 2
        assert restored.nodes[0].id == "step_1"
        assert restored.nodes[1].depends_on == ["step_1"]


# ── DAG executor integration tests ──────────────────────────────────────────

class TestDAGExecutor:
    def test_executes_single_node(self):
        orch = make_orchestrator()
        graph = TaskGraph(nodes=[
            TaskNode(id="step_1", skill="capture_desktop_state")
        ])
        trace = orch.run_graph_sync(graph)
        assert trace.overall_success
        assert len(trace.nodes) == 1
        assert trace.nodes[0].success

    def test_executes_sequential_chain(self):
        orch = make_orchestrator()
        graph = build_simple_graph([
            {"skill": "open_or_focus_app", "params": {"instruction": "打开QQ"}},
            {"skill": "capture_desktop_state"},
        ])
        trace = orch.run_graph_sync(graph)
        assert trace.overall_success
        assert len(trace.nodes) == 2
        assert all(n.success for n in trace.nodes)

    def test_executes_parallel_branch(self):
        orch = make_orchestrator()
        graph = TaskGraph(nodes=[
            TaskNode(id="root", skill="capture_desktop_state"),
            TaskNode(id="branch_a", skill="capture_desktop_state", depends_on=["root"]),
            TaskNode(id="branch_b", skill="capture_desktop_state", depends_on=["root"]),
            TaskNode(id="merge", skill="capture_desktop_state", depends_on=["branch_a", "branch_b"]),
        ])
        trace = orch.run_graph_sync(graph)
        assert trace.overall_success
        assert len(trace.nodes) == 4
        # branch_a and branch_b should have completed before merge
        assert all(n.success for n in trace.nodes)

    def test_failure_aborts_by_default(self):
        orch = make_orchestrator()
        graph = TaskGraph(nodes=[
            TaskNode(id="step_1", skill="nonexistent_skill"),
            TaskNode(id="step_2", skill="capture_desktop_state", depends_on=["step_1"]),
        ])
        trace = orch.run_graph_sync(graph)
        assert not trace.overall_success
        assert not trace.nodes[0].success
        # step_2 should not have run at all (or be blocked)
        step2 = [n for n in trace.nodes if n.node_id == "step_2"]
        assert not step2 or not step2[0].success

    def test_failure_skip_strategy(self):
        orch = make_orchestrator()
        graph = TaskGraph(nodes=[
            TaskNode(id="step_1", skill="nonexistent_skill", on_failure="skip"),
            TaskNode(id="step_2", skill="capture_desktop_state", depends_on=["step_1"]),
        ])
        trace = orch.run_graph_sync(graph)
        # skip means step_1 is "completed" to unblock step_2
        assert len(trace.nodes) >= 2
        step2 = [n for n in trace.nodes if n.node_id == "step_2"]
        assert step2 and step2[0].success

    def test_unknown_skill_returns_error(self):
        orch = make_orchestrator()
        graph = TaskGraph(nodes=[
            TaskNode(id="step_1", skill="nonexistent_skill"),
        ])
        trace = orch.run_graph_sync(graph)
        assert not trace.overall_success
        assert not trace.nodes[0].success
        assert "nonexistent_skill" in (trace.nodes[0].error or "")

    def test_run_sequential_convenience(self):
        orch = make_orchestrator()
        trace = orch.run_sequential([
            {"skill": "capture_desktop_state"},
            {"skill": "capture_desktop_state"},
        ])
        assert trace.overall_success
        assert len(trace.nodes) == 2
