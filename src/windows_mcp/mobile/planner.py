"""LLM-based semantic planner that produces TaskGraph from natural language.

Supports OpenAI-compatible APIs (DeepSeek, Qwen, etc.) and falls back to
keyword-matching when no LLM is configured.
"""

from typing import Any
import json
import logging
import os

import requests

from windows_mcp.mobile.schemas import TaskGraph, TaskNode
from windows_mcp.mobile.skills import SkillRegistry

logger = logging.getLogger(__name__)

# ── prompt template ──────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are a task planner for a Windows desktop automation system. Your job is to convert a user's natural-language instruction into a JSON task graph.

## Available Skills

{skill_catalog}

## Task Graph Rules

1. Break complex instructions into atomic steps. Each step uses exactly ONE skill.
2. Assign a unique `id` to each step (e.g. "step_1", "step_2").
3. Use `depends_on` to define execution order. Steps without dependencies run first and may run in parallel.
4. Set `on_failure` to "abort" for critical steps, "skip" for optional steps.
5. Fill `params` based on the skill's input_schema. Use `$previous_step.field` to reference upstream results.
6. For instructions that don't match any skill, use `"skill": "llm_fallback"` and put the instruction in `params.instruction`.
7. Output ONLY valid JSON — no explanations, no markdown fences.

## Example

User: "打开Chrome浏览器然后截图"
Output:
{{
  "nodes": [
    {{"id": "step_1", "skill": "open_or_focus_app", "params": {{"instruction": "打开Chrome"}}, "depends_on": []}},
    {{"id": "step_2", "skill": "capture_desktop_state", "params": {{}}, "depends_on": ["step_1"]}}
  ]
}}

User: "搜索Python教程"
Output:
{{
  "nodes": [
    {{"id": "step_1", "skill": "browser_search", "params": {{"instruction": "搜索Python教程"}}, "depends_on": []}}
  ]
}}

Now plan the following instruction. Return ONLY the JSON object:
"""


def _build_skill_catalog(registry: SkillRegistry) -> str:
    """Build a text catalog of available skills for the prompt."""
    lines: list[str] = []
    for skill in registry.skills:
        spec = skill.spec
        lines.append(f"- **{spec.name}**: {spec.description}")
        if spec.examples:
            lines.append(f"  Examples: {', '.join(spec.examples[:3])}")
        if spec.input_schema:
            props = spec.input_schema.get("properties", {})
            if props:
                param_descs = [
                    f"{k} ({v.get('type', 'string')})"
                    for k, v in props.items()
                ]
                lines.append(f"  Params: {', '.join(param_descs)}")
    return "\n".join(lines)


class LLMPlanner:
    """LLM-based task graph planner.

    Uses an OpenAI-compatible chat completion API to convert natural-language
    instructions into a TaskGraph with skill selection, parameter resolution,
    and dependency ordering.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.skill_registry = skill_registry
        self.api_key = api_key or os.getenv("PLANNER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("PLANNER_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
        self.model = model or os.getenv("PLANNER_MODEL") or "deepseek-chat"
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        """True when an API key is available."""
        return bool(self.api_key)

    def plan(self, instruction: str) -> TaskGraph:
        """Convert a natural-language instruction into a TaskGraph."""
        if not self.is_configured:
            raise PlannerNotConfiguredError(
                "LLMPlanner is not configured. Set PLANNER_API_KEY or OPENAI_API_KEY."
            )

        catalog = _build_skill_catalog(self.skill_registry)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(skill_catalog=catalog)

        try:
            response = self._call_llm(system_prompt, instruction)
            graph = self._parse_response(response)
            self._validate_graph(graph)
            return graph
        except Exception as exc:
            logger.warning("LLM planner failed: %s. Falling back to single-step.", exc)
            raise PlannerError(f"LLM planning failed: {exc}") from exc

    def _call_llm(self, system_prompt: str, user_instruction: str) -> str:
        """Call the LLM and return the text response."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_instruction},
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
        }
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()

    def _parse_response(self, text: str) -> TaskGraph:
        """Parse LLM response text into a TaskGraph.

        Handles common LLM formatting quirks: markdown fences, trailing commas,
        and extra text around the JSON.
        """
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            # Find the first newline after opening fence
            nl = text.find("\n")
            if nl > 0:
                text = text[nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        # Find the outermost { } block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

        data = json.loads(text)
        return TaskGraph.model_validate(data)

    def _validate_graph(self, graph: TaskGraph) -> None:
        """Validate that all skill references are known."""
        known_skills = {s.spec.name for s in self.skill_registry.skills}
        known_skills.add("llm_fallback")
        for node in graph.nodes:
            if node.skill not in known_skills:
                raise PlannerError(
                    f"Unknown skill '{node.skill}' in plan. Available: {sorted(known_skills)}"
                )


class PlannerError(RuntimeError):
    """Raised when LLM planning fails."""


class PlannerNotConfiguredError(PlannerError):
    """Raised when the planner has no API key."""


class HybridPlanner:
    """Combines LLMPlanner with keyword-based fallback.

    When LLM is configured and available: use semantic planning.
    When LLM fails or is not configured: fall back to best-single-skill match.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.skill_registry = skill_registry
        self._llm_planner = LLMPlanner(
            skill_registry=skill_registry,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def plan(self, instruction: str) -> TaskGraph:
        """Plan with LLM, falling back to single-skill match."""
        if self._llm_planner.is_configured:
            try:
                return self._llm_planner.plan(instruction)
            except PlannerError:
                pass  # fall through to keyword match

        return self._keyword_plan(instruction)

    def _keyword_plan(self, instruction: str) -> TaskGraph:
        """Fallback: match single best skill by keyword."""
        skill = self.skill_registry.select(instruction)
        if skill is not None:
            return TaskGraph(nodes=[
                TaskNode(
                    id="step_1",
                    skill=skill.spec.name,
                    params={"instruction": instruction},
                )
            ])
        return TaskGraph(nodes=[
            TaskNode(
                id="step_1",
                skill="llm_fallback",
                params={"instruction": instruction},
            )
        ])
