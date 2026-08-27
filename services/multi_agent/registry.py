"""
多Agent协作模块 - Agent注册中心
负责Agent的注册、查询和关键词匹配路由
"""
from __future__ import annotations
import logging
from typing import Optional

from services.multi_agent.models import SubAgentDef

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Agent注册中心：管理所有子Agent的定义和路由匹配"""

    def __init__(self):
        self._agents: dict[str, SubAgentDef] = {}
        self._fallback_agent: Optional[str] = None  # 兜底Agent名称

    def register(self, agent_def: SubAgentDef) -> None:
        """注册一个子Agent"""
        self._agents[agent_def.name] = agent_def
        # 没有 trigger_keywords 的视为兜底Agent
        if not agent_def.trigger_keywords:
            if self._fallback_agent is None:
                self._fallback_agent = agent_def.name
                logger.info(f"设置兜底Agent: {agent_def.name}")

    def get(self, name: str) -> Optional[SubAgentDef]:
        """按名称获取Agent"""
        return self._agents.get(name)

    def list_all(self) -> list[SubAgentDef]:
        """列出所有Agent（排除兜底）"""
        return [a for a in self._agents.values() if a.name != self._fallback_agent]

    def get_fallback(self) -> Optional[SubAgentDef]:
        """获取兜底Agent"""
        if self._fallback_agent:
            return self._agents.get(self._fallback_agent)
        return None

    def match_by_keyword(self, user_message: str) -> list[SubAgentDef]:
        """
        基于关键词匹配路由。
        返回所有匹配的Agent列表（可能多个，由编排器决定串/并行）。
        """
        if not user_message:
            return []

        msg_lower = user_message.lower()
        matched = []
        scores: dict[str, int] = {}  # agent_name -> match_score

        for agent in self._agents.values():
            if not agent.trigger_keywords:
                continue  # 跳过兜底Agent
            score = 0
            for kw in agent.trigger_keywords:
                kw_lower = kw.lower()
                if kw_lower in msg_lower:
                    # 长关键词匹配权重更高
                    score += len(kw_lower) * 2
                elif any(word in msg_lower for word in kw_lower.split()):
                    # 部分匹配
                    score += len(kw_lower)
            if score > 0:
                scores[agent.name] = score

        if not scores:
            return []

        # 按分数排序，取前3个（防止过多Agent）
        sorted_names = sorted(scores, key=scores.get, reverse=True)[:3]
        for name in sorted_names:
            agent = self._agents.get(name)
            if agent:
                matched.append(agent)

        logger.debug(f"关键词路由: '{user_message[:50]}...' → {[a.name for a in matched]}")
        return matched

    def all_agent_names(self) -> list[str]:
        """获取所有Agent名称"""
        return list(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)


# 全局单例
_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """获取全局Agent注册中心"""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def reset_registry() -> None:
    """重置注册中心（用于重新加载配置）"""
    global _registry
    _registry = AgentRegistry()
