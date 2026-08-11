"""Agent-to-Agent (A2A) network layer for multi-agent collaboration.

Provides:
  - AgentCard: self-describing capability advertisement
  - AgentRegistry: in-process discovery (extensible to Redis/HTTP)
  - A2AGateway: FastAPI routes for remote task delegation
  - RemoteSkillAdapter: wraps a remote agent as a local DesktopSkill
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from uuid import uuid4
import logging

import requests

from windows_mcp.mobile.agent import AgentRunOutput
from windows_mcp.mobile.dag import DAGExecutor
from windows_mcp.mobile.schemas import (
    ExecutionTrace,
    SkillResult,
    TaskGraph,
    TaskNode,
)
from windows_mcp.mobile.skills import DesktopSkill, SkillContext, SkillRegistry

logger = logging.getLogger(__name__)

# ── AgentCard ────────────────────────────────────────────────────────────────


@dataclass
class AgentCard:
    """Self-describing advertisement published by each agent."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    name: str = "unnamed"
    description: str = ""
    skills: list[str] = field(default_factory=list)
    endpoint: str = ""  # HTTP URL, e.g. "http://localhost:8790"
    status: str = "online"  # "online" | "offline" | "busy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "skills": self.skills,
            "endpoint": self.endpoint,
            "status": self.status,
        }


# ── AgentRegistry ────────────────────────────────────────────────────────────


class AgentRegistry:
    """In-process registry for agent discovery.

    Agents register themselves; orchestrators query to find suitable
    delegates for tasks they cannot handle locally.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        """Register or update an agent card."""
        self._agents[card.id] = card
        logger.info("Agent registered: %s (%s) — %d skills", card.name, card.id, len(card.skills))

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry."""
        self._agents.pop(agent_id, None)

    def find(self, *, skill: str | None = None, status: str = "online") -> list[AgentCard]:
        """Find agents matching criteria."""
        results = []
        for card in self._agents.values():
            if card.status != status:
                continue
            if skill and skill not in card.skills:
                continue
            results.append(card)
        return results

    def get(self, agent_id: str) -> AgentCard | None:
        """Get a specific agent by ID."""
        return self._agents.get(agent_id)

    def list_all(self) -> list[AgentCard]:
        """List all registered agents."""
        return list(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)


# ── default singleton ────────────────────────────────────────────────────────

_default_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Get or create the default agent registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = AgentRegistry()
    return _default_registry


# ── A2AGateway ───────────────────────────────────────────────────────────────


class A2AGateway:
    """FastAPI-compatible router for A2A task delegation.

    Usage:
        from fastapi import FastAPI
        app = FastAPI()
        gateway = A2AGateway(executor=..., card=...)
        app.include_router(gateway.router(), prefix="/a2a")
    """

    def __init__(
        self,
        executor: DAGExecutor,
        card: AgentCard,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.executor = executor
        self.card = card
        self.registry = registry or get_registry()

    def router(self):
        """Build a FastAPI APIRouter with A2A endpoints."""
        from fastapi import APIRouter, HTTPException
        from pydantic import BaseModel

        router = APIRouter()
        gateway = self  # capture for closure

        class DelegateRequest(BaseModel):
            graph: dict  # TaskGraph as JSON dict

        @router.get("/card")
        async def get_card():
            return gateway.card.to_dict()

        @router.post("/tasks")
        async def delegate_task(req: DelegateRequest):
            try:
                graph = TaskGraph.model_validate(req.graph)
                trace = await gateway.executor.execute(graph)
                return {
                    "accepted": True,
                    "trace": trace.model_dump(),
                }
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @router.get("/agents")
        async def list_agents():
            return [a.to_dict() for a in gateway.registry.list_all()]

        return router


# ── RemoteSkillAdapter ───────────────────────────────────────────────────────


class RemoteSkillAdapter:
    """Wraps a remote agent as a local DesktopSkill.

    When the local planner can't handle a task, it delegates to a remote
    agent that advertises the required skill.
    """

    def __init__(
        self,
        remote_card: AgentCard,
        *,
        timeout: int = 60,
    ) -> None:
        self.spec = self._build_spec(remote_card)
        self._endpoint = remote_card.endpoint
        self._timeout = timeout

    @staticmethod
    def _build_spec(card: AgentCard):
        from windows_mcp.mobile.skills import SkillSpec
        return SkillSpec(
            name=f"remote::{card.id}",
            description=f"Delegate to remote agent '{card.name}' ({', '.join(card.skills[:3])})",
            examples=(),
            triggers=(),
        )

    def match_score(self, instruction: str) -> int:
        """Always returns 0 — RemoteSkillAdapter is used explicitly, not matched."""
        return 0

    def execute(self, context: SkillContext) -> SkillResult:
        """Send a task graph to the remote agent for execution."""
        if not self._endpoint:
            return SkillResult(
                success=False,
                error="Remote agent has no endpoint configured.",
                message="远程 Agent 未配置端点。",
            )

        # Build a simple single-node graph from the instruction
        graph = TaskGraph(nodes=[
            TaskNode(
                id="delegated_1",
                skill="llm_fallback",
                params={"instruction": context.instruction},
            )
        ])

        try:
            resp = requests.post(
                f"{self._endpoint}/a2a/tasks",
                json={"graph": graph.model_dump()},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            trace = ExecutionTrace.model_validate(data["trace"])
            if trace.nodes and trace.nodes[0].result:
                return trace.nodes[0].result
            return SkillResult(
                success=trace.overall_success,
                message=f"Remote agent completed: {trace.overall_success}",
                data={"remote_trace": trace.model_dump()},
            )
        except requests.ConnectionError:
            return SkillResult(
                success=False,
                error=f"Cannot reach remote agent at {self._endpoint}",
                message=f"无法连接到远程 Agent: {self._endpoint}",
            )
        except Exception as exc:
            return SkillResult(
                success=False,
                error=str(exc),
                message=f"远程 Agent 调用失败: {exc}",
            )


# ── MultiAgentOrchestrator ───────────────────────────────────────────────────


class MultiAgentOrchestrator:
    """Orchestrator that can delegate to remote agents via the registry.

    Falls back through three tiers:
      1. Local skills (keyword/LLM planner)
      2. Remote agents (matching skill in registry)
      3. Local LLM fallback
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        tools: Any,
        fallback_agent: Any,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.skill_registry = skill_registry
        self.tools = tools
        self.fallback_agent = fallback_agent
        self.registry = registry or get_registry()

    def find_remote_agent(self, skill_name: str) -> AgentCard | None:
        """Find a remote agent that can handle the given skill."""
        cards = self.registry.find(skill=skill_name, status="online")
        return cards[0] if cards else None

    def build_delegation_graph(
        self, skill_name: str, instruction: str
    ) -> TaskGraph | None:
        """Build a TaskGraph that delegates to a remote agent."""
        remote = self.find_remote_agent(skill_name)
        if remote is None:
            return None
        return TaskGraph(nodes=[
            TaskNode(
                id="delegate",
                skill=f"remote::{remote.id}",
                params={"instruction": instruction},
            )
        ])

    def create_remote_adapters(self) -> list[DesktopSkill]:
        """Create RemoteSkillAdapter for all online remote agents."""
        adapters: list[DesktopSkill] = []
        for card in self.registry.find():
            adapters.append(RemoteSkillAdapter(card))
        return adapters
