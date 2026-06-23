"""
API 服务模块
封装 LLM 模型的调用逻辑，支持流式输出
"""

import os
import json
import requests
from typing import Optional, List, Dict, Generator

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载配置
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "custom_models.json")


def load_config() -> dict:
    """加载配置文件"""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_model_config(model_display: str) -> dict:
    """根据模型显示名称获取模型配置"""
    config = load_config()
    models = config.get("models", {})

    for model_id, model_config in models.items():
        display_prefix = "🎨" if model_config.get("model_type") == "image" else "⭐"
        display = f"{display_prefix} {model_config['name']}"
        if display == model_display:
            return model_config

    # 如果没找到，返回默认模型
    default_model = config.get("default_model", "mimo-v2.5-pro")
    for model_id, model_config in models.items():
        if model_config.get("model_id") == default_model:
            return model_config

    # 返回第一个模型
    if models:
        return list(models.values())[0]

    return {}


def get_api_key(model_config: dict) -> str:
    """
    获取 API Key，优先级：
    1. 配置中的 api_key 字段
    2. 环境变量
    """
    # 优先从配置读取
    config_key = model_config.get("api_key", "").strip()
    if config_key:
        return config_key

    # 再从环境变量读取
    env_name = model_config.get("api_key_env", "")
    if env_name:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key

    raise ValueError(
        f"未配置 API Key。请在 config/custom_models.json 中配置 api_key"
    )


def chat_completion_stream(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    provider_name: Optional[str] = None,
    custom_base_url: Optional[str] = None,
    custom_api_key: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    流式调用 LLM API（OpenAI 兼容格式）
    逐块 yield 文本内容
    """
    # 支持自定义模型
    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        model_config = get_model_config(model or "")
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    timeout = int(os.environ.get("LLM_REQUEST_TIMEOUT", "120"))

    response = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
    response.raise_for_status()

    # 强制使用 UTF-8 编码解码响应
    response.encoding = 'utf-8'

    buffer = ""
    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                return
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
            except (json.JSONDecodeError, IndexError, KeyError):
                continue


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    provider_name: Optional[str] = None,
    custom_base_url: Optional[str] = None,
    custom_api_key: Optional[str] = None,
) -> str:
    """
    非流式调用 LLM API（用于 Agent 推理等场景）
    返回完整响应文本
    """
    # 支持自定义模型
    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        model_config = get_model_config(model or "")
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    timeout = int(os.environ.get("LLM_REQUEST_TIMEOUT", "120"))

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content:
            return content

    return ""


def get_all_models() -> List[Dict[str, str]]:
    """
    获取配置中所有可用模型
    返回 [{"provider": "mimo", "model": "mimo-v2.5-pro", "display": "⭐ MiMo-V2.5-Pro", "type": "text"}, ...]
    """
    config = load_config()
    models = config.get("models", {})
    result = []

    for model_id, model_config in models.items():
        model_type = model_config.get("model_type", "text")
        display_prefix = "🎨" if model_type == "image" else "⭐"
        result.append({
            "provider": model_config.get("provider", ""),
            "model": model_config.get("model_id", ""),
            "display": f"{display_prefix} {model_config['name']}",
            "type": model_type,
            "is_custom": True,
            "base_url": model_config.get("base_url", ""),
            "api_key": model_config.get("api_key", ""),
        })

    return result


def get_model_display_names() -> List[str]:
    """获取所有模型的显示名称列表"""
    return [m["display"] for m in get_all_models()]


def find_model_by_display(display_name: str) -> Optional[Dict[str, str]]:
    """根据显示名称查找模型"""
    for m in get_all_models():
        if m["display"] == display_name:
            return m
    return None


def generate_image(
    prompt: str,
    model: str = "Kwai-Kolors/Kolors",
    image_size: str = "1024x1024",
) -> str:
    """
    调用图片生成 API
    返回图片 URL
    """
    # 从配置中获取图片模型的 API 信息
    config = load_config()
    models = config.get("models", {})

    # 查找图片模型配置
    model_config = None
    for model_id, mc in models.items():
        if mc.get("model_id") == model or model in model_id:
            model_config = mc
            break

    if not model_config:
        raise ValueError(f"未找到图片模型: {model}")

    base_url = model_config.get("base_url", "").rstrip("/")
    api_key = get_api_key(model_config)

    url = f"{base_url}/images/generations"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "image_size": image_size,
        "num_inference_steps": 20,
        "guidance_scale": 7.5,
    }

    timeout = int(os.environ.get("LLM_REQUEST_TIMEOUT", "120"))

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    images = data.get("images", data.get("data", []))
    if images and len(images) > 0:
        return images[0].get("url", "")

    return ""
