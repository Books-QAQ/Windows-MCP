from windows_mcp.desktop.service import Desktop
from windows_mcp.mobile.a2a import DesktopA2AOrchestrator
from windows_mcp.mobile.agent import ClaudeDesktopAgent, InstructionAgent
from windows_mcp.mobile.schemas import SkillView
from windows_mcp.mobile.skills import SkillRegistry, load_skill_registry
from windows_mcp.mobile.tools import DesktopAutomationTools


def default_skill_views(config_path: str | None = None) -> list[SkillView]:
    return load_skill_registry(config_path).to_views()


def create_mobile_runtime(config_path: str | None = None) -> tuple[InstructionAgent, list[SkillView]]:
    desktop = Desktop()
    tools = DesktopAutomationTools(desktop)
    fallback_agent = ClaudeDesktopAgent(tools)
    skill_registry = load_skill_registry(config_path)
    orchestrator = DesktopA2AOrchestrator(
        tools=tools,
        fallback_agent=fallback_agent,
        skill_registry=skill_registry,
    )
    return orchestrator, skill_registry.to_views()


def create_mobile_runtime_with_registry(
    skill_registry: SkillRegistry,
) -> tuple[InstructionAgent, list[SkillView]]:
    desktop = Desktop()
    tools = DesktopAutomationTools(desktop)
    fallback_agent = ClaudeDesktopAgent(tools)
    orchestrator = DesktopA2AOrchestrator(
        tools=tools,
        fallback_agent=fallback_agent,
        skill_registry=skill_registry,
    )
    return orchestrator, skill_registry.to_views()
