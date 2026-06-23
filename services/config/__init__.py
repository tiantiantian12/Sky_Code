"""
配置模块
"""

import json
import os

# 配置文件目录路径
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(config_name: str) -> dict:
    """加载指定名称的配置文件"""
    config_path = os.path.join(CONFIG_DIR, f"{config_name}.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"配置文件不存在: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"配置文件格式错误: {e}")
        return {}


def get_agent_config() -> dict:
    """获取 Agent 配置"""
    default_config = {
        "max_steps": 10,
        "llm_params": {
            "temperature": 0.3,
            "max_tokens": 2048
        },
        "timeout": 240
    }
    
    config = load_config("agent_config")
    agent_config = config.get("agent", {})
    
    # 合并配置，确保所有必要的键都存在
    return {
        "max_steps": agent_config.get("max_steps", default_config["max_steps"]),
        "llm_params": {
            "temperature": agent_config.get("llm_params", {}).get("temperature", default_config["llm_params"]["temperature"]),
            "max_tokens": agent_config.get("llm_params", {}).get("max_tokens", default_config["llm_params"]["max_tokens"])
        },
        "timeout": agent_config.get("timeout", default_config["timeout"])
    }