"""浏览器模型 prompt 组装 — 避免将 Agent 系统提示泄露到网页输入框"""

import re

_AGENT_INTERNAL_MARKERS = (
    "请继续执行任务",
    "请根据以上工具返回的结果",
    "你的输出格式有误",
    "工具执行错误",
    "的返回结果:",
    "格式错误",
    "强制工具调用",
    "你刚才的回复无效",
    "你没有输出正确的",
)

_AGENT_TOOL_HINT = (
    "【工具格式 — 只输出 JSON，否则无效】\n"
    "需要工具时，你的整条回复只能是一行纯 JSON。禁止 markdown 代码块、禁止前后缀文字、禁止解释。\n"
    "扫项目：{\"tool\":\"scan_project\",\"input\":{\"dir_path\":\"D:/path/project\"}}\n"
    "读单文件全文：{\"tool\":\"read_file\",\"input\":{\"file_path\":\"D:/path/file.py\"}}\n"
    "列目录：{\"tool\":\"list_directory\",\"input\":{\"dir_path\":\"D:/path/project\"}}\n"
    "写文件：{\"tool\":\"write_file\",\"input\":{\"file_path\":\"D:/path/file.py\",\"content\":\"完整代码\"}}\n"
    "删文件：{\"tool\":\"delete_file\",\"input\":{\"file_path\":\"D:/path/file.py\"}}\n"
    "注意：路径使用用户提供的原始路径，不要修改、不要质疑、不要加引号包裹之外的任何字符。"
)

_AGENT_PROJECT_SCAN_HINT = (
    "【项目分析】不要凭记忆猜测项目内容。本地会先 scan_project（目录树+摘要）；"
    "若需某文件完整源码再 read_file。"
    '例如 {"tool":"read_file","input":{"file_path":"D:/path/project/main.py"}}\n'
)

_AGENT_INTERNAL_TOOL_HINT = (
    "【⚠ 纠错 — 只输出 JSON】你刚才的回复无效。本地程序等待的是 JSON 工具调用，不是解释/拒绝/确认。\n"
    "现在请只输出一行纯 JSON（无 markdown、无任何其他文字），"
    '例如：{"tool":"read_file","input":{"file_path":"D:/path/file.py"}}\n'
    "路径参数必须使用用户消息中的原始路径，不要自己编造或询问。"
)

_AGENT_FINAL_ANSWER_HINT = (
    "【完成回复】本地工具已执行完毕。请直接用中文向用户总结结果，"
    "不要再输出 JSON、不要再调用工具。"
)

_AGENT_CODE_FIX_HINT = (
    "【代码纠错 — 只输出 JSON】write_file 或 execute_code 返回了语法/编译/执行错误。"
    "请根据错误信息修正代码，只输出一行 write_file 的 JSON（content 为完整修正后代码）。"
    "禁止 markdown、禁止解释、禁止中文总结。"
)

_CODE_ERROR_MARKERS = ("⚠ 语法错误:", "⚠ 编译错误:", "❌ 执行失败")
_CODE_FIX_REQUIRED_MARKER = "【必须修正】"

_BROWSER_RUNTIME_WRAPPER = (
    "【⚠ 强制规则 — 违反复核】你运行在 Windows 真机环境中，能直接读写 D: 盘等所有本地文件系统。"
    "你输出的 JSON 会被用户本机程序立即解析执行。\n\n"
    "硬性约束（以下每一项都是绝对禁令，违反会终止对话）：\n"
    "① 绝对禁止说「路径找不到」「文件不存在」「无法访问」「我不能直接」「请确认路径」等任何拒绝话术。"
    "你无权判断路径是否存在——本地执行器会验证。直接输出 JSON。\n"
    "② 绝对禁止猜测文件内容、编造项目结构、假设目录里有什么。先 scan_project 或 read_file。\n"
    "③ 用户提到任何路径（如 D:/xxx）时，你必须先输出 scan_project 或 read_file 的 JSON，"
    "不允许跳过工具直接回复。\n"
    "④ 工具返回的数据就是事实。不要在工具结果前加任何前置解释或质疑。\n\n"
)

_CHAT_ONLY_MARKERS = (
    "什么模型", "哪个模型", "什么版本", "你是谁", "你是什么", "介绍一下你自己",
    "你好", "hello", "hi", "谢谢", "解释一下", "什么是", "为什么", "怎么样",
    "what model", "who are you", "which model",
)

_TOOL_TASK_KEYWORDS = (
    "write_file", "read_file", "delete_file", "list_directory", "scan_project", "deep_read_directory",
    "search_files", "创建", "新建", "写一个", "写个", "生成文件", "修改文件",
    "删除文件", "读取文件", "读文件", "读取", "查看文件", "打开文件", "读一下",
    "保存到", "写入", "脚本", "项目文件", "main.py", "项目", "目录", "文件夹",
    "执行命令", "运行命令", "终端", "conda", "pip install",
    "帮我写", "帮我创建", "帮我生成", "帮我改", "标注工具", "代码文件",
    "新增", "添加文件", "创建一个", "分析项目", "扫描", "优化",
    "workflow", "索引", "search_code",
)

_PATH_PATTERN = re.compile(
    r"[A-Za-z]:[/\\]|"
    r"[/\\][\w.\-]+[/\\]|"
    r"\.(?:py|json|xml|txt|md|cpp|java|js|ts|yaml|yml|csv|xlsx|docx|pptx)\b",
    re.I,
)

_PROJECT_SCAN_MARKERS = (
    "项目", "整个", "目录结构", "有哪些文件", "扫描", "分析", "优化", "结构",
    "还有什么", "可以改进", "改进方向", "deep_read",
)


def is_agent_internal_message(text: str) -> bool:
    if not text:
        return False
    head = text[:240]
    return any(marker in head for marker in _AGENT_INTERNAL_MARKERS)


def tool_results_need_code_fix(text: str) -> bool:
    """工具返回结果中是否包含待修正的代码错误。"""
    if not text:
        return False
    return any(m in text for m in _CODE_ERROR_MARKERS) or _CODE_FIX_REQUIRED_MARKER in text


def is_agent_tool_result_followup(text: str) -> bool:
    """Agent 工具执行后的续跑消息 — 应要求中文总结，而非再次输出 JSON。"""
    if not text:
        return False
    return "请根据以上工具返回的结果" in text or (
        "工具 " in text and "的返回结果:" in text
    ) or _CODE_FIX_REQUIRED_MARKER in text


def is_agent_code_fix_followup(text: str) -> bool:
    """工具执行后存在代码错误，需要模型输出 write_file JSON 修正。"""
    if not text:
        return False
    if _CODE_FIX_REQUIRED_MARKER in text:
        return True
    return is_agent_tool_result_followup(text) and tool_results_need_code_fix(text)


def is_agent_json_retry_message(text: str) -> bool:
    """格式错误或需继续调用工具的内部消息。"""
    if not text:
        return False
    head = text[:240]
    return (
        "你尚未调用本地工具" in head
        or "你刚才的回复无效" in head
        or "格式错误" in head
        or "你的输出格式有误" in head
        or "工具执行错误" in head
        or "请继续执行任务" in head
        or "你没有输出正确" in head
        or "强制工具调用" in head
        or _CODE_FIX_REQUIRED_MARKER in head
        or "代码未修正" in head
    )


def _looks_like_chat_question(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    if any(m.lower() in t for m in _CHAT_ONLY_MARKERS):
        return True
    if t.endswith("?") or t.endswith("？"):
        if not _looks_like_tool_task(text):
            return True
    return False


def _looks_like_tool_task(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _PATH_PATTERN.search(text):
        return True
    lower = text.lower()
    return any(k.lower() in lower for k in _TOOL_TASK_KEYWORDS)


def looks_like_project_scan_request(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if not _PATH_PATTERN.search(text):
        return False
    return any(m in lower for m in _PROJECT_SCAN_MARKERS)


def _has_agent_system(messages) -> bool:
    return any(
        m.get("role") == "system" and "write_file" in str(m.get("content", ""))
        for m in messages
    )


def _extract_user_message(messages) -> str:
    """取最后一条 user 消息；Agent 后续轮次若为内部消息则原样返回"""
    user_msg = ""
    first_task = ""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        text = content if isinstance(content, str) else str(content)
        user_msg = text
        if text.strip() and not is_agent_internal_message(text) and not first_task:
            first_task = text.strip()
    if is_agent_internal_message(user_msg):
        return user_msg
    return (user_msg or first_task or "").strip()


def compose_browser_prompt(messages, include_agent_tool_hint=False, provider=None) -> str:
    """
    从 messages 提取 user 内容发送到网页输入框。
    Agent 模式下：工具任务附加 JSON 格式；工具结果续跑附加「中文总结」提示。
    """
    user_msg = _extract_user_message(messages)
    if not user_msg:
        return user_msg

    has_agent_system = _has_agent_system(messages)
    if not include_agent_tool_hint or not has_agent_system:
        return user_msg

    if is_agent_code_fix_followup(user_msg):
        return f"{_AGENT_CODE_FIX_HINT}\n\n{user_msg}"

    if is_agent_tool_result_followup(user_msg):
        return f"{_AGENT_FINAL_ANSWER_HINT}\n\n{user_msg}"

    if is_agent_json_retry_message(user_msg):
        return f"{_AGENT_INTERNAL_TOOL_HINT}\n\n{user_msg}"

    if _looks_like_chat_question(user_msg):
        return user_msg

    hints = [_AGENT_TOOL_HINT]
    if looks_like_project_scan_request(user_msg):
        hints.insert(0, _AGENT_PROJECT_SCAN_HINT)

    result = "\n".join(hints) + f"\n\n{user_msg}"
    if provider in ("chatgpt", "minimax", "deepseek", "kimi", "browser"):
        result = _BROWSER_RUNTIME_WRAPPER + result
    return result
