"""
API 服务模块
封装 LLM 模型的调用逻辑，支持流式输出
支持 MiniMax Agent（浏览器模式）和 OpenAI 兼容 API
"""

import os
import json
import threading
import requests
from typing import Optional, List, Dict, Generator, Callable

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 加载配置
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "custom_models.json")


def load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def get_model_config(model_display_or_id: str) -> dict:
    config = load_config()
    models = config.get("models", {})
    query = (model_display_or_id or "").strip()

    for model_id, model_config in models.items():
        display_prefix = _get_display_prefix(model_config)
        display = f"{display_prefix} {model_config['name']}"
        if display == query:
            return model_config

    if query:
        query_lower = query.lower()
        for model_id, model_config in models.items():
            model_id_lower = model_id.lower()
            config_model_id = str(model_config.get("model_id", "")).lower()
            if config_model_id == query_lower or model_id_lower == query_lower:
                return model_config
            if model_id_lower.endswith(f"/{query_lower}"):
                return model_config

    default_model = config.get("default_model", "mimo-v2.5-pro")
    for model_id, model_config in models.items():
        if model_config.get("model_id") == default_model:
            return model_config

    if models:
        return list(models.values())[0]
    return {}


def _get_display_prefix(model_config: dict) -> str:
    if model_config.get("model_type") == "image":
        return "\U0001f3a8"
    if model_config.get("provider") == "deepseek":
        return "\U0001f9e0"
    if model_config.get("provider") == "minimax":
        return "\U0001f916"
    if model_config.get("provider") == "chatgpt":
        return "\U0001f4ac"
    if model_config.get("provider") == "kimi":
        return "\U0001f52e"
    return "\u2b50"


def _is_deepseek(model_name_or_config) -> bool:
    """仅 provider == 'deepseek' 才走浏览器模式；
    不以 base_url/名称包含 'deepseek' 判断，避免 API 模型被误判。"""
    if isinstance(model_name_or_config, dict):
        return model_name_or_config.get("provider") == "deepseek"
    # 字符串参数：优先查配置
    name = str(model_name_or_config).lower()
    cfg = get_model_config(name)
    if cfg and cfg.get("provider"):
        return cfg.get("provider") == "deepseek"
    return "deepseek" in name


def _is_chatgpt(model_name_or_config) -> bool:
    if isinstance(model_name_or_config, dict):
        return (
            model_name_or_config.get("provider") == "chatgpt"
            or "chatgpt.com" in str(model_name_or_config.get("base_url", ""))
        )
    name = str(model_name_or_config).lower()
    return "chatgpt" in name


def is_chatgpt_model(model_display_or_id: str) -> bool:
    if not model_display_or_id:
        return False
    return _is_chatgpt(get_model_config(model_display_or_id))


def is_browser_agent_model(model_display_or_id: str) -> bool:
    """MiniMax / DeepSeek / Kimi / ChatGPT 等通过浏览器接入的 Agent 模型（非纯 API）。"""
    cfg = get_model_config(model_display_or_id)
    if not cfg:
        return False
    provider = cfg.get("provider", "")
    if provider not in ("minimax", "deepseek", "chatgpt", "kimi"):
        return False
    if provider == "chatgpt":
        return is_chatgpt_browser_model(model_display_or_id)
    # MiniMax / DeepSeek / Kimi 浏览器：无 api_key 或 base_url 指向网页
    if cfg.get("use_browser"):
        return True
    base = str(cfg.get("base_url", "")).lower()
    if "minimaxi.com" in base or "deepseek.com" in base or "moonshot.cn" in base:
        return not cfg.get("api_key")
    return _is_browser_model(cfg)


def is_chatgpt_browser_model(model_display_or_id: str) -> bool:
    """ChatGPT 通过 Chrome 调试端口接入（非 OpenAI API）"""
    cfg = get_model_config(model_display_or_id)
    if not cfg or not _is_chatgpt(cfg):
        return False
    if cfg.get("use_browser"):
        return True
    return "chatgpt.com" in str(cfg.get("base_url", "")).lower()


def _is_kimi(model_name_or_config) -> bool:
    """判断是否为 Kimi 浏览器模型"""
    if isinstance(model_name_or_config, dict):
        return model_name_or_config.get("provider") == "kimi"
    name = str(model_name_or_config).lower()
    cfg = get_model_config(name)
    if cfg and cfg.get("provider"):
        return cfg.get("provider") == "kimi"
    return "kimi" in name


def _is_browser_model(model_config: dict) -> bool:
    """Check if model uses browser-based service"""
    return _is_minimax(model_config) or _is_deepseek(model_config) or _is_chatgpt(model_config) or _is_kimi(model_config)


def is_api_agent_model(model_display_or_id: str) -> bool:
    """所有通过 HTTP API 接入的文本模型（非浏览器、非图片），统一走 API Agent 路径。"""
    if not model_display_or_id:
        return False
    if is_browser_agent_model(model_display_or_id):
        return False
    cfg = get_model_config(model_display_or_id)
    if not cfg or cfg.get("model_type") == "image":
        return False
    return bool(cfg.get("base_url"))


class ToolCallingNotSupportedError(Exception):
    """API 不支持 tools / function calling"""


def build_openai_tools_schema(tools) -> List[dict]:
    """将 LangChain 工具转为 OpenAI tools 格式"""
    schemas = []
    for t in tools:
        if not getattr(t, "args_schema", None):
            continue
        schema = t.args_schema.model_json_schema()
        props = {}
        required = []
        for pname, pinfo in schema.get("properties", {}).items():
            # LangChain 会把 args 等保留名映射为 v__args；导出给 API 时用真实参数名
            export_name = pname[3:] if pname.startswith("v__") else pname
            if export_name == "args":
                export_name = "script_args"
            export_info = {
                k: v for k, v in pinfo.items()
                if k in ("type", "description", "items", "enum", "default")
            }
            if pname.startswith("v__") and export_info.get("type") == "array":
                export_info["type"] = "string"
                export_info.pop("items", None)
                if not export_info.get("description"):
                    export_info["description"] = "Optional command-line arguments for the script"
            props[export_name] = export_info
        for req in schema.get("required", []):
            export_req = req[3:] if req.startswith("v__") else req
            if export_req == "args":
                export_req = "script_args"
            if export_req in props:
                required.append(export_req)
        desc = (t.description or t.name or "").strip()
        if len(desc) > 480:
            desc = desc[:480] + "…"
        schemas.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": desc.split("\n")[0] or t.name,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return schemas


def _inject_thinking_kwargs(model_config: dict, payload: dict):
    """如果模型配置了 enable_thinking，注入 chat_template_kwargs"""
    if model_config.get("enable_thinking"):
        payload["chat_template_kwargs"] = {"enable_thinking": True}


def chat_completion_with_tools(
    messages: List[Dict],
    tools: List[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    custom_base_url: Optional[str] = None,
    custom_api_key: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> dict:
    """
    OpenAI 兼容 Function Calling。
    返回 {"content": str, "tool_calls": list, "message": dict}
    """
    model_config = get_model_config(model or "")
    if _is_browser_model(model_config):
        raise ToolCallingNotSupportedError("浏览器模型不支持 API Function Calling")

    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto",
    }
    _inject_thinking_kwargs(model_config, payload)

    from services.config import get_agent_config
    config_timeout = get_agent_config().get("timeout", 240)

    connect_timeout = min(30, config_timeout)
    read_timeout = min(120, config_timeout)

    session = requests.Session()
    _watcher = None
    if stop_event is not None:
        def _session_closer():
            if stop_event.wait(timeout=read_timeout + 5):
                try:
                    session.close()
                except Exception:
                    pass
        _watcher = threading.Thread(target=_session_closer, daemon=True)
        _watcher.start()

    try:
        response = session.post(url, headers=headers, json=payload,
                                timeout=(connect_timeout, read_timeout))
        if response.status_code >= 400:
            err = response.text[:400]
            if "tool" in err.lower() or response.status_code == 400:
                raise ToolCallingNotSupportedError(err)
            response.raise_for_status()
        data = response.json()
    except requests.ConnectionError:
        if stop_event is not None and stop_event.is_set():
            raise requests.ConnectionError("用户已取消请求")
        raise
    finally:
        session.close()
    choices = data.get("choices", [])
    if not choices:
        return {"content": "", "tool_calls": [], "message": {"role": "assistant", "content": ""}}

    msg = choices[0].get("message", {})
    return {
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "message": msg,
    }


def chat_completion_with_tools_stream(
    messages: List[Dict],
    tools: List[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    custom_base_url: Optional[str] = None,
    custom_api_key: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> Generator[dict, None, None]:
    """
    OpenAI 兼容 Function Calling 流式版本。

    每次 yield 一个字典:
        {"content": str}                     — 文本增量 chunk
        {"content": ..., "tool_calls": [...], "message": {...}, "done": True}  — 最终完整结果
    """
    model_config = get_model_config(model or "")
    if _is_browser_model(model_config):
        raise ToolCallingNotSupportedError("浏览器模型不支持 API Function Calling")

    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
    }
    _inject_thinking_kwargs(model_config, payload)

    from services.config import get_agent_config
    config_timeout = get_agent_config().get("timeout", 240)
    connect_timeout = min(30, config_timeout)
    # 流式请求的 read timeout 使用完整配置值（不缩减），
    # 因为 LLM 可能花费较长时间思考后才产生第一个 token
    read_timeout = max(config_timeout, 120)

    # 累积内容缓冲区
    full_content = ""
    # 累积 tool_calls delta（OpenAI 流式协议中 tool_calls 以 delta 增量返回）
    tool_calls_by_index = {}  # index -> {"id": str, "function": {"name": str, "arguments": str}}

    session = requests.Session()
    _watcher = None
    if stop_event is not None:
        def _session_closer():
            if stop_event.wait(timeout=read_timeout + 5):
                try:
                    session.close()
                except Exception:
                    pass
        _watcher = threading.Thread(target=_session_closer, daemon=True)
        _watcher.start()

    try:
        response = session.post(url, headers=headers, json=payload, stream=True,
                                timeout=(connect_timeout, read_timeout))
        if response.status_code >= 400:
            err = response.text[:400]
            if "tool" in err.lower() or response.status_code == 400:
                raise ToolCallingNotSupportedError(err)
            response.raise_for_status()

        response.encoding = 'utf-8'
        sse_buffer = ""
        for chunk_bytes in response.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk_bytes:
                continue
            sse_buffer += chunk_bytes
            while "\n" in sse_buffer:
                line, sse_buffer = sse_buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})

                        # 文本增量
                        content_chunk = delta.get("content")
                        if content_chunk:
                            full_content += content_chunk
                            yield {"content": content_chunk}

                        # 工具调用增量
                        tc_deltas = delta.get("tool_calls")
                        if tc_deltas:
                            for tc_delta in tc_deltas:
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_calls_by_index:
                                    tool_calls_by_index[idx] = {
                                        "id": tc_delta.get("id") or "",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                entry = tool_calls_by_index[idx]
                                if tc_delta.get("id"):
                                    entry["id"] = tc_delta["id"]
                                fn = tc_delta.get("function") or {}
                                if fn.get("name"):
                                    entry["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    entry["function"]["arguments"] += fn["arguments"]
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
    except requests.ConnectionError:
        if stop_event is not None and stop_event.is_set():
            raise requests.ConnectionError("用户已取消请求")
        raise
    finally:
        session.close()

    # 组装最终 tool_calls
    tool_calls = []
    for idx in sorted(tool_calls_by_index.keys()):
        entry = tool_calls_by_index[idx]
        tc = {
            "id": entry["id"],
            "type": "function",
            "function": {
                "name": entry["function"]["name"],
                "arguments": entry["function"]["arguments"],
            },
        }
        tool_calls.append(tc)

    message = {"role": "assistant", "content": full_content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    yield {"content": full_content, "tool_calls": tool_calls, "message": message, "done": True}


def _is_minimax(model_name_or_config) -> bool:
    """判断是否为 MiniMax 模型（支持 model_name 字符串或 config dict）"""
    if isinstance(model_name_or_config, dict):
        provider = model_name_or_config.get("provider", "")
        if provider in ("chatgpt", "deepseek"):
            return False
        return provider == "minimax"
    name = str(model_name_or_config).lower()
    return "minimax" in name


def get_api_key(model_config: dict) -> str:
    config_key = model_config.get("api_key", "").strip()
    if config_key:
        return config_key
    env_name = model_config.get("api_key_env", "")
    if env_name:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
    raise ValueError("未配置 API Key。请在 config/custom_models.json 中配置 api_key")


def chat_completion_stream(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    provider_name: Optional[str] = None,
    custom_base_url: Optional[str] = None,
    custom_api_key: Optional[str] = None,
    app_session_id: Optional[str] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Generator[str, None, None]:
    model_config = get_model_config(model or "")
    if _is_deepseek(model_config) or _is_deepseek(model or ""):
        from services.providers.deepseek_service import deepseek_chat
        ds_model = model_config.get("model_id", "deepseek-chat")
        result = deepseek_chat(messages=messages, model=ds_model,
                               temperature=temperature, max_tokens=max_tokens, stream=False,
                               app_session_id=app_session_id,
                               status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            for char in content:
                yield char
        return
    if _is_chatgpt(model_config) or _is_chatgpt(model or ""):
        from services.providers.chatgpt_service import chatgpt_chat
        cg_model = model_config.get("model_id", "gpt-4o")
        result = chatgpt_chat(messages=messages, model=cg_model,
                              temperature=temperature, max_tokens=max_tokens, stream=False,
                              app_session_id=app_session_id, status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            for char in content:
                yield char
        return
    if _is_minimax(model_config) or _is_minimax(model or ""):
        from services.providers.minimax_service import minimax_chat
        minimax_model = model_config.get("model_id", "MiniMax-M3")
        result = minimax_chat(messages=messages, model=minimax_model,
                              temperature=temperature, max_tokens=max_tokens, stream=False,
                              app_session_id=app_session_id,
                              status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            for char in content:
                yield char
        return
    if _is_kimi(model_config) or _is_kimi(model or ""):
        from services.providers.kimi_service import kimi_chat
        kimi_model = model_config.get("model_id", "kimi-k2-thinking")
        # 使用 stream=True 获取真流式输出（轮询时 yield 部分内容）
        gen = kimi_chat(messages=messages, model=kimi_model,
                        temperature=temperature, max_tokens=max_tokens, stream=True,
                        app_session_id=app_session_id,
                        status_callback=status_callback)
        # kimi_chat stream=True 返回生成器，yield 的是全量文本
        # 调用方（send_message_stream）会累加到 full_response
        prev_len = 0
        for full_text in gen:
            if full_text and len(full_text) > prev_len:
                # yield 增量部分（大块，不是逐字）
                yield full_text[prev_len:]
                prev_len = len(full_text)
        return

    # OpenAI 兼容 API
    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    _inject_thinking_kwargs(model_config, payload)

    from services.config import get_agent_config
    config_timeout = get_agent_config().get("timeout", 240)

    # 使用 (connect, read) 元组确保读取超时，防止服务器挂起时永久阻塞
    connect_timeout = min(30, config_timeout)
    read_timeout = max(config_timeout, 120)  # 流式请求使用完整超时值
    response = requests.post(url, headers=headers, json=payload, stream=True,
                             timeout=(connect_timeout, read_timeout))
    response.raise_for_status()
    response.encoding = 'utf-8'

    buffer = ""
    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                return
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("delta", {}).get("content", "")
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
    app_session_id: Optional[str] = None,
    status_callback: Optional[Callable[[str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> str:
    model_config = get_model_config(model or "")
    if _is_deepseek(model_config) or _is_deepseek(model or ""):
        from services.providers.deepseek_service import deepseek_chat
        ds_model = model_config.get("model_id", "deepseek-chat")
        result = deepseek_chat(messages=messages, model=ds_model,
                               temperature=temperature, max_tokens=max_tokens, stream=False,
                               app_session_id=app_session_id,
                               status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    if _is_chatgpt(model_config) or _is_chatgpt(model or ""):
        from services.providers.chatgpt_service import chatgpt_chat
        cg_model = model_config.get("model_id", "gpt-4o")
        result = chatgpt_chat(messages=messages, model=cg_model,
                              temperature=temperature, max_tokens=max_tokens, stream=False,
                              app_session_id=app_session_id, status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    if _is_minimax(model_config) or _is_minimax(model or ""):
        from services.providers.minimax_service import minimax_chat
        minimax_model = model_config.get("model_id", "MiniMax-M3")
        result = minimax_chat(messages=messages, model=minimax_model,
                              temperature=temperature, max_tokens=max_tokens, stream=False,
                              app_session_id=app_session_id,
                              status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    if _is_kimi(model_config) or _is_kimi(model or ""):
        from services.providers.kimi_service import kimi_chat
        kimi_model = model_config.get("model_id", "kimi-k2-thinking")
        result = kimi_chat(messages=messages, model=kimi_model,
                           temperature=temperature, max_tokens=max_tokens, stream=False,
                           app_session_id=app_session_id,
                           status_callback=status_callback)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content

    # OpenAI 兼容 API
    if custom_base_url and custom_api_key:
        base_url = custom_base_url.rstrip("/")
        api_key = custom_api_key
    else:
        base_url = model_config.get("base_url", "").rstrip("/")
        api_key = get_api_key(model_config)
        model = model_config.get("model_id", model)

    config = load_config()
    model = model or config.get("default_model", "mimo-v2.5-pro")

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    _inject_thinking_kwargs(model_config, payload)

    from services.config import get_agent_config
    config_timeout = get_agent_config().get("timeout", 240)

    # 非流式请求使用 (connect, read) 元组防止永久阻塞
    # 使用 Session + stop_event 实现可中断的阻塞调用
    connect_timeout = min(30, config_timeout)
    read_timeout = min(120, config_timeout)  # 限制最长等待 120 秒

    session = requests.Session()
    _watcher = None
    if stop_event is not None:
        def _session_closer():
            if stop_event.wait(timeout=read_timeout + 5):
                try:
                    session.close()
                except Exception:
                    pass
        _watcher = threading.Thread(target=_session_closer, daemon=True)
        _watcher.start()

    try:
        response = session.post(url, headers=headers, json=payload,
                                timeout=(connect_timeout, read_timeout))
        response.raise_for_status()
        data = response.json()
    except requests.ConnectionError:
        if stop_event is not None and stop_event.is_set():
            raise requests.ConnectionError("用户已取消请求")
        raise
    finally:
        session.close()

    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content:
            return content
    return ""


def get_all_models() -> List[Dict[str, str]]:
    """获取所有文本模型（用于聊天，已过滤 image/video 类型）"""
    config = load_config()
    models = config.get("models", {})
    result = []
    for model_id, model_config in models.items():
        model_type = model_config.get("model_type", "text")
        # 仅返回文本模型，图片/视频模型不应出现在聊天模型列表
        if model_type not in ("text",):
            continue
        display_prefix = _get_display_prefix(model_config)
        result.append({
            "provider": model_config.get("provider", ""),
            "model": model_config.get("model_id", ""),
            "display": f"{display_prefix} {model_config['name']}",
            "type": model_type,
            "is_custom": True,
            "base_url": model_config.get("base_url", ""),
            "api_key": model_config.get("api_key", ""),
            "use_browser": model_config.get("use_browser", False),
        })
    return result


def get_model_display_names() -> List[str]:
    return [m["display"] for m in get_all_models()]


def find_model_by_display(display_name: str) -> Optional[Dict[str, str]]:
    for m in get_all_models():
        if m["display"] == display_name:
            return m
    return None


def generate_image(prompt: str, model: str = "Kwai-Kolors/Kolors",
                   image_size: str = "1024x1024") -> str:
    config = load_config()
    models = config.get("models", {})
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
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "image_size": image_size,
               "num_inference_steps": 20, "guidance_scale": 7.5}

    from services.config import get_agent_config
    config_timeout = get_agent_config().get("timeout", 240)

    connect_timeout = min(30, config_timeout)
    read_timeout = config_timeout
    response = requests.post(url, headers=headers, json=payload,
                             timeout=(connect_timeout, read_timeout))
    response.raise_for_status()
    data = response.json()
    images = data.get("images", data.get("data", []))
    if images and len(images) > 0:
        return images[0].get("url", "")
    return ""


def close_deepseek_browser():
    try:
        from services.providers.deepseek_service import close_browser
        close_browser()
    except:
        pass


def close_minimax_browser():
    try:
        from services.providers.minimax_service import close_browser
        close_browser()
    except:
        pass


def close_chatgpt_browser():
    try:
        from services.providers.chatgpt_service import close_browser
        close_browser()
    except:
        pass


def close_kimi_browser():
    try:
        from services.providers.kimi_service import close_browser
        close_browser()
    except:
        pass


def open_chatgpt_login_browser():
    from services.providers.chatgpt_service import launch_chrome_for_login
    launch_chrome_for_login()


