"""
多Agent协作模块 - 配置加载器
从 multi_agent_config.json 加载配置并初始化Agent注册中心
"""
from __future__ import annotations
import json
import os
import logging
from typing import Optional

from services.multi_agent.models import SubAgentDef
from services.multi_agent.registry import get_registry, reset_registry

logger = logging.getLogger(__name__)

# 配置文件路径
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_CONFIG_PATH = os.path.join(
    os.path.dirname(CONFIG_DIR), "config", "multi_agent_config.json"
)


def load_multi_agent_config(config_path: str = None) -> tuple[dict, list[SubAgentDef]]:
    """
    加载多Agent配置并初始化注册中心。

    返回: (global_config, agent_defs)
    """
    if config_path is None:
        config_path = MULTI_AGENT_CONFIG_PATH

    if not os.path.exists(config_path):
        logger.warning(f"多Agent配置文件不存在: {config_path}")
        return {}, []

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载多Agent配置失败: {e}")
        return {}, []

    global_config = config.get("global", {})
    agents_data = config.get("agents", [])
    routing_rules = config.get("routing_rules", [])

    # 重置注册中心
    reset_registry()
    registry = get_registry()

    agent_defs = []
    for agent_data in agents_data:
        agent_def = SubAgentDef.from_dict(agent_data)
        agent_defs.append(agent_def)
        registry.register(agent_def)
        logger.info(f"注册子Agent: {agent_def.name} ({agent_def.display_name})")

    logger.info(f"多Agent配置加载完成: {len(agent_defs)} 个Agent, "
                f"{len(routing_rules)} 条路由规则, "
                f"enabled={global_config.get('enabled', False)}")

    return global_config, agent_defs


def get_multi_agent_global_config() -> dict:
    """获取多Agent全局配置（不重新加载）"""
    try:
        with open(MULTI_AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("global", {})
    except Exception:
        return {}


def is_multi_agent_enabled() -> bool:
    """检查多Agent模式是否启用"""
    config = get_multi_agent_global_config()
    return config.get("enabled", False)
