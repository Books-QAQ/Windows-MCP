"""Tests for the A2A network layer: registry, gateway, remote adapters."""

import asyncio
import json

import pytest

from windows_mcp.mobile.a2a_network import (
    A2AGateway,
    AgentCard,
    AgentRegistry,
    MultiAgentOrchestrator,
    RemoteSkillAdapter,
    get_registry,
)
from windows_mcp.mobile.dag import DAGExecutor
from windows_mcp.mobile.schemas import ScreenshotPayload, SkillResult, TaskGraph, TaskNode
from windows_mcp.mobile.skills import SkillContext, build_default_skill_registry


# ── helpers ──────────────────────────────────────────────────────────────────

class FakeTools:
    def open_app(self, *, name): return f"{name} launched."
    def switch_app(self, *, name): return f"Switched to {name}."
    def capture_completion_screenshot(self):
        return ScreenshotPayload(base64_data="fake", summary="done")


class FakeFallback:
    def run_instruction(self, instruction, model=None, should_stop=None):
        from windows_mcp.mobile.agent import AgentRunOutput
        return AgentRunOutput(
            message="done",
            screenshot=ScreenshotPayload(base64_data="fake", summary="fb"),
            raw_agent_response="ok",
        )


def make_executor():
    registry = build_default_skill_registry()
    return DAGExecutor(registry, FakeTools(), FakeFallback())


# ── AgentCard ────────────────────────────────────────────────────────────────

class TestAgentCard:
    def test_defaults(self):
        card = AgentCard(name="test")
        assert card.id
        assert card.name == "test"
        assert card.status == "online"

    def test_serialization(self):
        card = AgentCard(
            name="browser-agent", skills=["browser_search", "capture_desktop_state"],
            endpoint="http://localhost:8790",
        )
        d = card.to_dict()
        assert d["name"] == "browser-agent"
        assert "browser_search" in d["skills"]


# ── AgentRegistry ────────────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_register_and_find(self):
        reg = AgentRegistry()
        card = AgentCard(name="a1", skills=["open_or_focus_app"])
        reg.register(card)
        assert len(reg) == 1

        found = reg.find(skill="open_or_focus_app")
        assert len(found) == 1
        assert found[0].name == "a1"

    def test_find_no_match(self):
        reg = AgentRegistry()
        card = AgentCard(name="a1", skills=["capture_desktop_state"])
        reg.register(card)
        found = reg.find(skill="open_or_focus_app")
        assert len(found) == 0

    def test_find_filters_by_status(self):
        reg = AgentRegistry()
        online = AgentCard(id="1", name="online", status="online")
        offline = AgentCard(id="2", name="offline", status="offline")
        reg.register(online)
        reg.register(offline)
        found = reg.find()
        assert len(found) == 1
        assert found[0].name == "online"

    def test_unregister(self):
        reg = AgentRegistry()
        card = AgentCard(id="abc", name="temp")
        reg.register(card)
        reg.unregister("abc")
        assert len(reg) == 0

    def test_list_all(self):
        reg = AgentRegistry()
        reg.register(AgentCard(id="1", name="a"))
        reg.register(AgentCard(id="2", name="b"))
        assert len(reg.list_all()) == 2

    def test_get_by_id(self):
        reg = AgentRegistry()
        card = AgentCard(id="xyz", name="special")
        reg.register(card)
        assert reg.get("xyz") is not None
        assert reg.get("missing") is None


# ── RemoteSkillAdapter ───────────────────────────────────────────────────────

class TestRemoteSkillAdapter:
    def test_builds_spec(self):
        card = AgentCard(id="ra1", name="remote-1", skills=["file_operation"], endpoint="http://x:1")
        adapter = RemoteSkillAdapter(card)
        assert adapter.spec.name == "remote::ra1"
        assert "remote-1" in adapter.spec.description

    def test_no_endpoint_returns_error(self):
        card = AgentCard(id="ra1", name="remote-1")
        adapter = RemoteSkillAdapter(card)
        ctx = SkillContext(instruction="test", tools=FakeTools(), fallback_agent=FakeFallback())
        result = adapter.execute(ctx)
        assert not result.success
        assert "endpoint" in (result.error or "").lower()

    def test_match_score_always_zero(self):
        card = AgentCard(id="ra1", name="r", endpoint="http://x:1")
        adapter = RemoteSkillAdapter(card)
        assert adapter.match_score("anything") == 0


# ── A2AGateway ───────────────────────────────────────────────────────────────

class TestA2AGateway:
    def test_router_creation(self):
        executor = make_executor()
        card = AgentCard(name="test-gw")
        gateway = A2AGateway(executor, card)
        router = gateway.router()
        assert router is not None

    def test_card_endpoint(self):
        """Test the /card endpoint through FastAPI TestClient."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        executor = make_executor()
        card = AgentCard(name="gw-test", skills=["file_operation"])
        gateway = A2AGateway(executor, card)

        app = FastAPI()
        app.include_router(gateway.router(), prefix="/a2a")
        client = TestClient(app)

        resp = client.get("/a2a/card")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gw-test"
        assert "file_operation" in data["skills"]

    def test_delegate_endpoint(self):
        """Test the /tasks endpoint."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        executor = make_executor()
        card = AgentCard(name="gw-test")
        gateway = A2AGateway(executor, card)

        app = FastAPI()
        app.include_router(gateway.router(), prefix="/a2a")
        client = TestClient(app)

        graph = TaskGraph(nodes=[
            TaskNode(id="s1", skill="capture_desktop_state")
        ])
        resp = client.post("/a2a/tasks", json={"graph": graph.model_dump()})
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True
        assert "trace" in data

    def test_list_agents(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        reg = AgentRegistry()
        reg.register(AgentCard(id="a1", name="agent-1"))

        executor = make_executor()
        card = AgentCard(name="gw")
        gateway = A2AGateway(executor, card, registry=reg)

        app = FastAPI()
        app.include_router(gateway.router(), prefix="/a2a")
        client = TestClient(app)

        resp = client.get("/a2a/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert any(a["name"] == "agent-1" for a in data)


# ── MultiAgentOrchestrator ──────────────────────────────────────────────────

class TestMultiAgentOrchestrator:
    def test_find_remote_agent(self):
        reg = AgentRegistry()
        reg.register(AgentCard(id="r1", name="file-worker", skills=["file_operation"]))

        orch = MultiAgentOrchestrator(
            build_default_skill_registry(), FakeTools(), FakeFallback(), registry=reg,
        )
        remote = orch.find_remote_agent("file_operation")
        assert remote is not None
        assert remote.name == "file-worker"

    def test_no_remote_for_unknown_skill(self):
        reg = AgentRegistry()
        orch = MultiAgentOrchestrator(
            build_default_skill_registry(), FakeTools(), FakeFallback(), registry=reg,
        )
        assert orch.find_remote_agent("nonexistent") is None

    def test_build_delegation_graph(self):
        reg = AgentRegistry()
        reg.register(AgentCard(id="r1", name="worker", skills=["file_operation"]))

        orch = MultiAgentOrchestrator(
            build_default_skill_registry(), FakeTools(), FakeFallback(), registry=reg,
        )
        graph = orch.build_delegation_graph("file_operation", "读取test.txt")
        assert graph is not None
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "remote::r1"

    def test_create_remote_adapters(self):
        reg = AgentRegistry()
        reg.register(AgentCard(id="r1", name="w1", skills=["f1"], endpoint="http://a:1"))
        reg.register(AgentCard(id="r2", name="w2", skills=["f2"], endpoint="http://b:1"))

        orch = MultiAgentOrchestrator(
            build_default_skill_registry(), FakeTools(), FakeFallback(), registry=reg,
        )
        adapters = orch.create_remote_adapters()
        assert len(adapters) == 2


# ── end-to-end: two agents exchange tasks ────────────────────────────────────

class TestMultiAgentE2E:
    def test_two_agents_via_registry(self):
        """Two orchestrators register, one delegates to the other."""
        reg = AgentRegistry()

        # Agent B: a "file-worker" that handles file_operation
        card_b = AgentCard(
            id="agent-b", name="file-worker",
            skills=["file_operation", "capture_desktop_state"],
            endpoint="http://agent-b:8787",
        )
        reg.register(card_b)

        # Agent A: queries registry, finds agent B for file_operation
        orch_a = MultiAgentOrchestrator(
            build_default_skill_registry(), FakeTools(), FakeFallback(), registry=reg,
        )
        remote = orch_a.find_remote_agent("file_operation")
        assert remote is not None
        assert remote.id == "agent-b"

        graph = orch_a.build_delegation_graph("file_operation", "读取C:/test.txt")
        assert graph is not None
        assert graph.nodes[0].skill == "remote::agent-b"

    def test_agent_unregister_on_offline(self):
        reg = AgentRegistry()
        card = AgentCard(id="temp", name="temp", status="online")
        reg.register(card)
        assert len(reg.find()) == 1

        card.status = "offline"
        reg.register(card)
        assert len(reg.find()) == 0


# ── singleton registry ──────────────────────────────────────────────────────

class TestSingletonRegistry:
    def test_get_registry_returns_same_instance(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
