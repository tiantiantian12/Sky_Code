"""浏览器 Provider 切换时的上下文同步 — 将 App memory 摘要注入首条消息"""

from typing import Optional, List, Dict, Callable

from services.providers.browser_prompt import compose_browser_prompt, is_agent_internal_message

_last_provider: Dict[str, str] = {}
_storage = None

# 短历史直接拼接，超过此长度才调用 LLM 摘要
_DIRECT_TEXT_LIMIT = 600
# 送入 LLM 的单条消息截断
_MSG_TRUNCATE = 300
# 送入 LLM 的历史总字符上限
_HISTORY_CHAR_LIMIT = 6000


def _get_storage():
    global _storage
    if _storage is None:
        from services.core.storage_service import StorageService
        _storage = StorageService()
    return _storage


def _notify(status_callback: Optional[Callable[[str], None]], message: str):
    if status_callback and message:
        try:
            status_callback(message)
        except Exception:
            pass


def reset_provider_tracking(session_id: str):
    """清除会话的 Provider 跟踪（会话删除/清空时调用）"""
    _last_provider.pop(session_id, None)


def _extract_history_for_summary(messages: list) -> List[Dict[str, str]]:
    """提取历史 user/assistant 消息（排除 system 与当前最后一条 user）"""
    last_user_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            last_user_idx = i

    history = []
    for i, m in enumerate(messages):
        if i == last_user_idx:
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        if role == "user" and is_agent_internal_message(content):
            continue
        history.append({"role": role, "content": content})
    return history


def _format_history_text(history: List[Dict[str, str]]) -> str:
    lines = []
    total = 0
    for msg in history:
        role = "用户" if msg["role"] == "user" else "AI"
        text = msg["content"]
        if len(text) > _MSG_TRUNCATE:
            text = text[:_MSG_TRUNCATE] + "…"
        line = f"{role}: {text}"
        if total + len(line) > _HISTORY_CHAR_LIMIT:
            lines.append("…（更早的对话已省略）")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _get_summarizer_model_id() -> str:
    from services.core.api_service import load_config, _is_browser_model

    config = load_config()
    default = config.get("default_model", "mimo-v2.5-pro")
    models = config.get("models", {})

    for mc in models.values():
        if mc.get("model_id") == default and not _is_browser_model(mc):
            if mc.get("model_type") != "image" and mc.get("api_key"):
                return mc["model_id"]

    for mc in models.values():
        if _is_browser_model(mc) or mc.get("model_type") == "image":
            continue
        if mc.get("api_key"):
            return mc.get("model_id", default)

    return default


def _summarize_history(
    history: List[Dict[str, str]],
    status_callback: Optional[Callable[[str], None]] = None,
) -> str:
    text = _format_history_text(history)
    if not text:
        return ""
    if len(text) <= _DIRECT_TEXT_LIMIT:
        return text

    _notify(status_callback, "正在生成对话摘要...")
    model_id = _get_summarizer_model_id()
    prompt = (
        "请将以下对话压缩为简洁摘要，保留关键信息、结论和未完成的任务。"
        "用中文输出，不超过 400 字，不要添加无关说明。\n\n"
        f"{text}"
    )
    try:
        from services.core.api_service import chat_completion
        summary = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_id,
            temperature=0.3,
            max_tokens=500,
            app_session_id=None,
        )
        return summary.strip() if summary else text
    except Exception:
        return text


def _should_inject_context(session_id: str, provider: str, history: list) -> bool:
    if not history:
        return False
    last = _last_provider.get(session_id)
    if last == provider:
        return False
    if last is not None and last != provider:
        return True
    row = _get_storage().get_provider_session(session_id, provider)
    if row and row.get("external_id"):
        return False
    return True


def compose_browser_prompt_with_context(
    messages,
    provider: str,
    app_session_id: Optional[str] = None,
    include_agent_tool_hint: bool = False,
    status_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """
    组装浏览器 prompt；切换 Provider 时将 App memory 历史摘要拼入首条消息。
    """
    base_prompt = compose_browser_prompt(
        messages,
        include_agent_tool_hint=include_agent_tool_hint,
        provider=provider,
    )
    if not base_prompt:
        if app_session_id:
            _last_provider[app_session_id] = provider
        return base_prompt

    if app_session_id:
        history = _extract_history_for_summary(messages)
        if _should_inject_context(app_session_id, provider, history):
            _notify(status_callback, "正在同步上下文…")
            summary = _summarize_history(history, status_callback=status_callback)
            if summary:
                base_prompt = (
                    "【上下文说明】用户此前在其他 AI 中进行了如下对话（摘要），"
                    "请基于此背景继续回答。\n\n"
                    f"{summary}\n\n"
                    "【当前消息】\n"
                    f"{base_prompt}"
                )
                _notify(status_callback, "上下文已同步。")
        _last_provider[app_session_id] = provider

    return base_prompt
