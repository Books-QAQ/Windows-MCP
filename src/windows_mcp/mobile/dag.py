"""DAG (Directed Acyclic Graph) executor for multi-step skill orchestration.

Executes task graphs with topological ordering, parallel execution of
independent nodes, shared context passing, and configurable failure recovery.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio
import logging
import time

from windows_mcp.mobile.agent import AgentRunOutput
from windows_mcp.mobile.schemas import (
    ExecutionTrace,
    NodeTrace,
    SkillResult,
    TaskGraph,
    TaskNode,
)
from windows_mcp.mobile.skills import DesktopSkill, SkillContext, SkillRegistry
from windows_mcp.mobile.tools import DesktopAutomationTools
from windows_mcp.mobile.validator import SmartValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class DAGExecutionContext:
    """Mutable shared context that flows through DAG execution."""

    shared_data: dict[str, Any] = field(default_factory=dict)
    node_results: dict[str, SkillResult] = field(default_factory=dict)
    should_stop: Callable[[], bool] | None = None

    def resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resolve parameter references like '$node_id.field'.

        Supports:
          '$node_id'      → the full result.data dict of that node
          '$node_id.key'  → result.data['key'] of that node
        """
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                ref = value[1:]
                if "." in ref:
                    node_id, field = ref.split(".", 1)
                    node_result = self.node_results.get(node_id)
                    if node_result and node_result.success:
                        resolved[key] = node_result.data.get(field)
                    else:
                        resolved[key] = None
                else:
                    node_result = self.node_results.get(ref)
                    if node_result and node_result.success:
                        resolved[key] = node_result.data
                    else:
                        resolved[key] = None
            else:
                resolved[key] = value
        return resolved


class DAGExecutor:
    """Execute a TaskGraph with topological ordering and parallel batches."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        tools: DesktopAutomationTools,
        fallback_agent: Callable[..., AgentRunOutput] | None = None,
        validator: SmartValidator | None = None,
    ) -> None:
        self.skill_registry = skill_registry
        self.tools = tools
        self.fallback_agent = fallback_agent
        self.validator = validator

    async def execute(self, graph: TaskGraph) -> ExecutionTrace:
        """Execute all nodes in the graph and return a trace."""
        context = DAGExecutionContext(
            shared_data=dict(graph.global_context),
        )
        nodes_by_id: dict[str, TaskNode] = {n.id: n for n in graph.nodes}
        traces: dict[str, NodeTrace] = {}
        completed: set[str] = set()
        aborted: bool = False

        while len(completed) + len(
            [t for t in traces.values() if not t.success
             and nodes_by_id[t.node_id].on_failure != "skip"]
        ) < len(graph.nodes):
            if aborted:
                break

            # Find nodes whose dependencies are all completed
            ready_nodes: list[TaskNode] = []
            for node in graph.nodes:
                if node.id in completed:
                    continue
                if node.id in traces and not traces[node.id].success:
                    # Allow skipped nodes to not block dependents
                    node_obj = nodes_by_id.get(node.id)
                    if node_obj is None or node_obj.on_failure != "skip":
                        continue  # already failed
                if all(dep in completed for dep in node.depends_on):
                    ready_nodes.append(node)

            if not ready_nodes:
                # No progress possible — either done or deadlock
                remaining = [n.id for n in graph.nodes if n.id not in completed
                             and (n.id not in traces or traces[n.id].success)]
                if remaining:
                    # Check if remaining nodes depend on failed nodes (deadlock)
                    blocked = []
                    for nid in remaining:
                        node = nodes_by_id[nid]
                        failed_deps = [d for d in node.depends_on
                                       if d in traces and not traces[d].success]
                        if failed_deps:
                            blocked.append((nid, failed_deps))
                    if blocked:
                        for nid, fdeps in blocked:
                            traces[nid] = NodeTrace(
                                node_id=nid,
                                skill=nodes_by_id[nid].skill,
                                success=False,
                                error=f"Blocked by failed dependencies: {fdeps}",
                            )
                break

            # Execute ready nodes in parallel
            batch_tasks = [
                self._execute_node_with_timeout(node, context, nodes_by_id)
                for node in ready_nodes
            ]
            batch_traces = await asyncio.gather(*batch_tasks)

            for node, trace in zip(ready_nodes, batch_traces):
                traces[node.id] = trace
                if trace.success:
                    # Run validation if available
                    if self.validator and trace.result:
                        validation = self.validator.validate(
                            skill_name=node.skill,
                            result=trace.result,
                        )
                        if not validation.success:
                            logger.warning(
                                "Validation failed for node %s: %s. Confidence=%.2f, action=%s",
                                node.id, validation.reason,
                                validation.confidence, validation.suggested_action,
                            )
                            if validation.suggested_action == "retry" and trace.attempts < node.retry_count:
                                # Mark for retry — don't add to completed
                                retry_trace = await self._execute_node_with_timeout(
                                    node, context, nodes_by_id
                                )
                                traces[node.id] = retry_trace
                                if retry_trace.success:
                                    completed.add(node.id)
                                    if retry_trace.result:
                                        context.node_results[node.id] = retry_trace.result
                                else:
                                    # Retry failed, follow on_failure
                                    if node.on_failure == "skip":
                                        completed.add(node.id)
                                    elif node.on_failure == "abort":
                                        aborted = True
                                continue
                            elif validation.suggested_action in ("abort", "replan"):
                                aborted = True
                            # "skip" or "proceed" after validation failure: accept it
                    completed.add(node.id)
                    if trace.result:
                        context.node_results[node.id] = trace.result
                else:
                    # Handle failure based on strategy
                    strategy = node.on_failure
                    if strategy == "retry":
                        # Retries happen inside _execute_node_with_timeout already
                        pass
                    elif strategy == "skip":
                        completed.add(node.id)  # treat as completed to unblock dependents
                    elif strategy == "fallback":
                        if self.fallback_agent:
                            try:
                                fb_result = await self._run_fallback(node, context)
                                traces[node.id] = NodeTrace(
                                    node_id=node.id,
                                    skill="llm_fallback",
                                    success=fb_result.success,
                                    result=fb_result,
                                    attempts=trace.attempts + 1,
                                )
                                if fb_result.success:
                                    completed.add(node.id)
                                    context.node_results[node.id] = fb_result
                            except Exception:
                                aborted = True
                        else:
                            aborted = True
                    else:  # "abort"
                        aborted = True

        overall_success = all(
            t.success for t in traces.values()
            if nodes_by_id[t.node_id].on_failure != "skip"
        )
        return ExecutionTrace(
            nodes=list(traces.values()),
            final_context=context.shared_data,
            overall_success=overall_success,
        )

    async def _execute_node_with_timeout(
        self,
        node: TaskNode,
        context: DAGExecutionContext,
        nodes_by_id: dict[str, TaskNode],
    ) -> NodeTrace:
        """Execute a single node with retry logic."""
        attempts = 0
        last_error: str | None = None
        start = time.monotonic()

        for attempt in range(1, node.retry_count + 2):  # initial + retries
            if context.should_stop and context.should_stop():
                return NodeTrace(
                    node_id=node.id, skill=node.skill,
                    success=False, attempts=attempts,
                    error="Task cancelled by user.",
                )

            attempts = attempt
            try:
                result = await asyncio.wait_for(
                    self._execute_single_node(node, context),
                    timeout=node.timeout_seconds,
                )
                return NodeTrace(
                    node_id=node.id,
                    skill=node.skill,
                    success=result.success,
                    result=result,
                    attempts=attempts,
                    error=result.error if not result.success else None,
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            except asyncio.TimeoutError:
                last_error = f"Timed out after {node.timeout_seconds}s"
            except Exception as exc:
                last_error = str(exc)

            if node.on_failure != "retry":
                break

        return NodeTrace(
            node_id=node.id,
            skill=node.skill,
            success=False,
            attempts=attempts,
            error=last_error,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _execute_single_node(
        self, node: TaskNode, context: DAGExecutionContext
    ) -> SkillResult:
        """Execute one node: resolve params → find skill → execute."""
        resolved_params = context.resolve_params(node.params)

        if node.skill == "llm_fallback":
            return await self._run_fallback(node, context)

        skill = self._find_skill(node.skill)
        if skill is None:
            return SkillResult(
                success=False,
                error=f"Unknown skill: {node.skill}",
                message=f"未找到技能 {node.skill}。",
            )

        skill_context = SkillContext(
            instruction=resolved_params.get("instruction", ""),
            tools=self.tools,
            fallback_agent=self.fallback_agent,
            model=resolved_params.get("model"),
            should_stop=context.should_stop,
        )
        # Inject resolved params into the instruction for parameterized skills
        if resolved_params:
            param_str = " ".join(
                f"{k}={v}" for k, v in resolved_params.items()
                if k not in ("instruction", "model")
            )
            if param_str:
                skill_context.instruction = (
                    f"{skill_context.instruction} {param_str}"
                )

        # Run in thread to avoid blocking the event loop
        return await asyncio.to_thread(skill.execute, skill_context)

    async def _run_fallback(
        self, node: TaskNode, context: DAGExecutionContext
    ) -> SkillResult:
        """Delegate to the general LLM agent."""
        if self.fallback_agent is None:
            return SkillResult(
                success=False,
                error="No fallback agent configured.",
                message="未配置回退 Agent。",
            )
        resolved = context.resolve_params(node.params)
        instruction = resolved.get("instruction", "")
        agent_output = await asyncio.to_thread(
            self.fallback_agent.run_instruction,
            instruction,
            resolved.get("model"),
            context.should_stop,
        )
        return SkillResult(
            success=True,
            data={"agent_response": agent_output.raw_agent_response},
            message=agent_output.message,
            screenshot=agent_output.screenshot,
        )

    def _find_skill(self, name: str) -> DesktopSkill | None:
        """Find a skill by name in the registry."""
        for skill in self.skill_registry.skills:
            if skill.spec.name == name:
                return skill
        return None


def build_simple_graph(skills: list[dict[str, Any]]) -> TaskGraph:
    """Build a simple sequential task graph from a list of skill specs.

    Each entry: {"skill": "open_or_focus_app", "params": {"app_name": "Chrome"}}

    Nodes are chained sequentially by default. Add "depends_on" to override.
    """
    nodes: list[TaskNode] = []
    for i, entry in enumerate(skills):
        node_id = entry.get("id", f"step_{i + 1}")
        depends = entry.get("depends_on")
        if depends is None and i > 0:
            # Default: chain sequentially when no explicit depends_on
            depends = [nodes[-1].id]
        elif depends is None:
            depends = []
        nodes.append(TaskNode(
            id=node_id,
            skill=entry["skill"],
            params=entry.get("params", {}),
            depends_on=depends,
            on_failure=entry.get("on_failure", "abort"),
            retry_count=entry.get("retry_count", 2),
        ))
    return TaskGraph(nodes=nodes)
