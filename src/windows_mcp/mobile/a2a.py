from dataclasses import dataclass
from typing import Callable
import json

from windows_mcp.mobile.agent import AgentRunOutput, InstructionAgent
from windows_mcp.mobile.schemas import SkillResult
from windows_mcp.mobile.skills import SkillContext, SkillRegistry
from windows_mcp.mobile.tools import DesktopAutomationTools


@dataclass
class A2ADecision:
    route: str
    planner_notes: str
    selected_skill: str | None = None


class PlanningAgent:
    def __init__(self, skill_registry: SkillRegistry) -> None:
        self.skill_registry = skill_registry

    def plan(self, instruction: str) -> A2ADecision:
        skill = self.skill_registry.select(instruction)
        if skill is not None:
            return A2ADecision(
                route="skill",
                selected_skill=skill.spec.name,
                planner_notes=(
                    f"Matched high-confidence skill '{skill.spec.name}' for the incoming instruction."
                ),
            )
        return A2ADecision(
            route="fallback_llm",
            planner_notes="No built-in skill matched. Route to the general desktop agent.",
        )


class ExecutionAgent:
    def __init__(
        self,
        *,
        skill_registry: SkillRegistry,
        tools: DesktopAutomationTools,
        fallback_agent: InstructionAgent,
    ) -> None:
        self.skill_registry = skill_registry
        self.tools = tools
        self.fallback_agent = fallback_agent

    def execute(
        self,
        decision: A2ADecision,
        *,
        instruction: str,
        model: str | None,
        should_stop: Callable[[], bool] | None,
    ) -> AgentRunOutput:
        if decision.route == "skill":
            skill = self.skill_registry.select(instruction)
            if skill is None:
                raise RuntimeError(
                    f"Planner selected skill '{decision.selected_skill}', but no handler matched at execution time."
                )
            context = SkillContext(
                instruction=instruction,
                tools=self.tools,
                fallback_agent=self.fallback_agent,
                model=model,
                should_stop=should_stop,
            )
            skill_result = skill.execute(context)
            return self._skill_result_to_agent_output(skill_result)

        return self.fallback_agent.run_instruction(instruction, model, should_stop)

    @staticmethod
    def _skill_result_to_agent_output(result: SkillResult) -> AgentRunOutput:
        return AgentRunOutput(
            message=result.message,
            screenshot=result.screenshot,
            raw_agent_response=json.dumps(result.data, ensure_ascii=False) if result.data else None,
        )


class ValidationAgent:
    def __init__(self, tools: DesktopAutomationTools) -> None:
        self.tools = tools

    def validate(self, decision: A2ADecision, result: AgentRunOutput) -> AgentRunOutput:
        screenshot = result.screenshot or self.tools.capture_completion_screenshot()
        trace = {
            "planner_route": decision.route,
            "selected_skill": decision.selected_skill,
            "planner_notes": decision.planner_notes,
            "delegate_result": result.raw_agent_response,
        }
        return AgentRunOutput(
            message="操作已完成，当前界面状态如下：",
            screenshot=screenshot,
            raw_agent_response=json.dumps(trace, ensure_ascii=False),
        )


class DesktopA2AOrchestrator:
    def __init__(
        self,
        *,
        tools: DesktopAutomationTools,
        fallback_agent: InstructionAgent,
        skill_registry: SkillRegistry,
    ) -> None:
        self.planner = PlanningAgent(skill_registry)
        self.executor = ExecutionAgent(
            skill_registry=skill_registry,
            tools=tools,
            fallback_agent=fallback_agent,
        )
        self.validator = ValidationAgent(tools)

    def run_instruction(
        self,
        instruction: str,
        model: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentRunOutput:
        decision = self.planner.plan(instruction)
        result = self.executor.execute(
            decision,
            instruction=instruction,
            model=model,
            should_stop=should_stop,
        )
        return self.validator.validate(decision, result)
