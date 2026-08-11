"""Tests for the LLM planner and HybridPlanner."""

import json

import pytest

from windows_mcp.mobile.planner import (
    HybridPlanner,
    LLMPlanner,
    PlannerError,
    PlannerNotConfiguredError,
    _build_skill_catalog,
)
from windows_mcp.mobile.schemas import TaskGraph
from windows_mcp.mobile.skills import build_default_skill_registry


@pytest.fixture
def registry():
    return build_default_skill_registry()


# ── _build_skill_catalog ────────────────────────────────────────────────────

class TestBuildSkillCatalog:
    def test_includes_all_skills(self, registry):
        catalog = _build_skill_catalog(registry)
        for skill in registry.skills:
            assert skill.spec.name in catalog

    def test_includes_descriptions(self, registry):
        catalog = _build_skill_catalog(registry)
        assert "file_operation" in catalog
        assert "clipboard_operation" in catalog


# ── LLMPlanner (without API) ────────────────────────────────────────────────

class TestLLMPlannerNoAPI:
    def test_is_configured_false_without_key(self, registry):
        planner = LLMPlanner(registry, api_key=None)
        assert not planner.is_configured

    def test_is_configured_true_with_key(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        assert planner.is_configured

    def test_plan_raises_without_key(self, registry):
        planner = LLMPlanner(registry, api_key=None)
        with pytest.raises(PlannerNotConfiguredError):
            planner.plan("打开QQ")

    def test_env_var_detection(self, registry, monkeypatch):
        monkeypatch.setenv("PLANNER_API_KEY", "sk-env")
        planner = LLMPlanner(registry)
        assert planner.is_configured
        assert planner.api_key == "sk-env"


# ── parse_response ──────────────────────────────────────────────────────────

class TestParseResponse:
    def test_parses_clean_json(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = json.dumps({
            "nodes": [
                {"id": "step_1", "skill": "capture_desktop_state", "params": {}, "depends_on": []}
            ]
        })
        graph = planner._parse_response(text)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "capture_desktop_state"

    def test_parses_markdown_fenced_json(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = '```json\n{"nodes": [{"id": "s1", "skill": "open_or_focus_app", "params": {}, "depends_on": []}]}\n```'
        graph = planner._parse_response(text)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "s1"

    def test_parses_json_with_surrounding_text(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = 'Here is the plan:\n{"nodes": [{"id": "x", "skill": "browser_search", "params": {}, "depends_on": []}]}\nDone.'
        graph = planner._parse_response(text)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "browser_search"

    def test_rejects_unknown_skill(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = json.dumps({
            "nodes": [{"id": "s1", "skill": "launch_nukes", "params": {}, "depends_on": []}]
        })
        with pytest.raises(PlannerError, match="Unknown skill"):
            planner._parse_response(text)
            planner._validate_graph(planner._parse_response(text))

    def test_validates_all_known_skills(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = json.dumps({
            "nodes": [
                {"id": "s1", "skill": "open_or_focus_app", "params": {}, "depends_on": []},
                {"id": "s2", "skill": "capture_desktop_state", "params": {}, "depends_on": ["s1"]},
                {"id": "s3", "skill": "file_operation", "params": {}, "depends_on": ["s2"]},
                {"id": "s4", "skill": "clipboard_operation", "params": {}, "depends_on": []},
                {"id": "s5", "skill": "browser_search", "params": {}, "depends_on": ["s3", "s4"]},
            ]
        })
        graph = planner._parse_response(text)
        planner._validate_graph(graph)  # should not raise
        assert len(graph.nodes) == 5

    def test_accepts_llm_fallback(self, registry):
        planner = LLMPlanner(registry, api_key="sk-test")
        text = json.dumps({
            "nodes": [{"id": "s1", "skill": "llm_fallback", "params": {"instruction": "do magic"}, "depends_on": []}]
        })
        graph = planner._parse_response(text)
        planner._validate_graph(graph)  # should not raise


# ── HybridPlanner ───────────────────────────────────────────────────────────

class TestHybridPlanner:
    def test_falls_back_to_keyword_when_no_api(self, registry):
        planner = HybridPlanner(registry, api_key=None)
        graph = planner.plan("打开QQ")
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "open_or_focus_app"

    def test_falls_back_for_unmatched_instruction(self, registry):
        planner = HybridPlanner(registry, api_key=None)
        graph = planner.plan("做一件很复杂的事情没有任何技能能匹配")
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "llm_fallback"

    def test_falls_back_for_complex_multi_step(self, registry):
        """Even without API, multi-step instructions get a single fallback step."""
        planner = HybridPlanner(registry, api_key=None)
        graph = planner.plan("先打开浏览器，然后搜索资料，最后截图保存")
        # No keyword skill matches this, so it falls back to llm_fallback
        # (the current keyword planner is single-skill only)
        assert len(graph.nodes) == 1

    def test_with_fake_api_that_fails(self, registry):
        """When LLM call fails, fall back to keyword."""
        planner = HybridPlanner(registry, api_key="sk-fake", base_url="http://localhost:1")
        # LLM call will fail (connection refused), fall back to keyword
        graph = planner.plan("打开QQ")
        assert len(graph.nodes) == 1
        assert graph.nodes[0].skill == "open_or_focus_app"
