"""
services 服务模块

目录结构:
  - core/       核心服务 (agent, api, chat, memory, storage)
  - providers/   模型厂商服务 (chatgpt, deepseek, kimi, minimax, browser)
  - multi_agent/ 多Agent协作
  - tools/       Agent工具集
  - config/      配置文件
  - utils/       辅助服务
"""

# 核心服务
from services.core.agent_service import AgentService
from services.core.chat_service import ChatService
from services.core.memory_service import MemoryService
from services.core.storage_service import StorageService
from services.core.api_service import chat_completion, chat_completion_stream
from services.core.custom_model_service import CustomModelService

# 多Agent
from services.multi_agent import OrchestratorAgent, SubAgentExecutor

# 工具
from services.tools import get_all_tools

__all__ = [
    "AgentService",
    "ChatService", 
    "MemoryService",
    "StorageService",
    "chat_completion",
    "chat_completion_stream",
    "CustomModelService",
    "OrchestratorAgent",
    "SubAgentExecutor",
    "get_all_tools",
]
