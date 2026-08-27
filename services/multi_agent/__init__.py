"""
多Agent协作模块

用法:
    from services.multi_agent import MultiAgentService
    
    service = MultiAgentService(agent_service)
    
    # 在 ChatService 中使用:
    if chat_service.multi_agent_enabled:
        for event in service.run(...):
            handle_event(event)
"""
from services.multi_agent.models import SubAgentDef, SubTask, SubTaskResult, OrchestrationResult
from services.multi_agent.registry import AgentRegistry, get_registry, reset_registry
from services.multi_agent.sub_agent import SubAgentExecutor
from services.multi_agent.orchestrator import OrchestratorAgent

__all__ = [
    "SubAgentDef",
    "SubTask",
    "SubTaskResult",
    "OrchestrationResult",
    "AgentRegistry",
    "get_registry",
    "reset_registry",
    "SubAgentExecutor",
    "OrchestratorAgent",
]
