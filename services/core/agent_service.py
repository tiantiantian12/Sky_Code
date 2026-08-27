"""
Agent 服务模块
自定义 ReAct 循环，直接调用 LLM API + 工具执行
不依赖 LangChain 的文本格式解析，更健壮
"""

import json
import logging
import os
import re
import time
import queue
import threading
import requests
from typing import Generator, Optional, Callable

from services.core.api_service import (
    chat_completion,
    chat_completion_stream,
    chat_completion_with_tools,
    chat_completion_with_tools_stream,
    build_openai_tools_schema,
    find_model_by_display,
    is_api_agent_model,
    ToolCallingNotSupportedError,
)
from services.providers.browser_prompt import _looks_like_tool_task, looks_like_project_scan_request
from services.tools import get_all_tools
from services.config import get_agent_config, CONFIG_DIR
from services.utils.agent_finalize_policy import (
    TaskIntent,
    parse_task_intent,
    should_auto_finalize,
    build_auto_continue_calls,
    followup_after_execute,
)

logger = logging.getLogger(__name__)


# ── 自定义异常类 ────────────────────────────────────────────
class AgentError(Exception):
    """Agent 模块基础异常"""


class AgentNetworkError(AgentError):
    """网络请求 / 超时异常"""


class AgentAPIError(AgentError):
    """API 返回错误（限流、认证、服务端错误等）"""


class AgentParseError(AgentError):
    """模型输出解析失败"""


class AgentToolError(AgentError):
    """工具执行异常"""

# 浏览器模型（MiniMax 等）经网页 API 发消息时有 payload 上限；项目扫描结果常 >300KB
# 48KB 过小会导致 read_file 等工具返回结果频繁被截断，模型会提示"内容被截断"
_BROWSER_SUMMARY_MAX_CHARS = 200_000


def _truncate_browser_summary(text: str, max_chars: int = _BROWSER_SUMMARY_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head - 120
    return (
        text[:head]
        + f"\n\n... [工具输出已截断：原 {len(text)} 字符，保留首尾供分析] ...\n\n"
        + text[-tail:]
    )


def _pre_agent_context(user_message: str, workspace_path: str = None, max_chars: int = 6000) -> str:
    """在 LLM 调用前自动搜集相关上下文，避免 ReAct 多轮搜索/读取。

    提取用户消息中提到的文件名和符号名，自动执行：
    - 文件搜索 (search_files)
    - 文件读取 (read_file) + 符号表 (get_document_symbols)
    - RAG 语义检索 (search_code)

    将搜集到的上下文注入到用户消息中，使 LLM 在第一轮就能看到所需全部信息。
    """

    def _call(func, *args):
        """安全调用 LangChain @tool 装饰的函数，绕过包装器"""
        f = func.func if hasattr(func, 'func') else func
        return f(*args)

    if not workspace_path or not os.path.isdir(workspace_path):
        return ""

    context_parts = []
    start_time = time.time()
    _TIMEOUT = 5.0  # 预处理超时 5 秒，防止阻塞

    # ── 1. 提取文件名 ──
    # 注意：不能用 \b，因为 Python 中文字符也属于 \w，导致 \b 在中英文边界失效
    file_exts = "(?:py|js|ts|tsx|jsx|html|css|json|md|txt|yml|yaml|toml|ini|cfg|sh|bat|ps1|sql|csv|cpp|c|h|java|go|rs)"
    file_pattern = rf'(?<![a-zA-Z0-9_])([a-zA-Z0-9_\-]+\.{file_exts})(?![a-zA-Z0-9_])'
    mentioned_files = list(set(re.findall(file_pattern, user_message, re.IGNORECASE)))

    # 提取显式路径
    path_pattern = r'([a-zA-Z]:[\\\/][^\s\"\'\<\>]*\.\w+)'
    explicit_paths = re.findall(path_pattern, user_message)
    mentioned_files.extend(explicit_paths)
    mentioned_files = mentioned_files[:5]  # 最多 5 个文件

    # ── 2. 搜索并读取文件 ──
    found_and_read = set()
    for fname in mentioned_files:
        if time.time() - start_time > _TIMEOUT:
            break
        try:
            from services.tools.file_tools import search_files as _sf, read_file as _rf
            result = _call(_sf, workspace_path, fname)
            # 仅跳过明确失败：以"错误"开头或完全为空
            if not result.strip() or result.startswith("错误"):
                continue
            found_paths = re.findall(r'\[文件名\] (.+)', result)
            for fp in found_paths[:2]:
                if fp in found_and_read:
                    continue
                found_and_read.add(fp)
                if time.time() - start_time > _TIMEOUT:
                    break
                content = _call(_rf, fp)
                # 仅跳过明确失败：以"错误"开头或完全为空（不检查内容中是否包含"错误"二字）
                if not content.strip() or content.startswith("错误"):
                    continue
                if len(content) > 3000:
                    content = content[:3000] + "\n...(已截断，完整内容请按需调用 read_file)"
                context_parts.append(f"【文件: {fp}】\n```\n{content}\n```")

                # 符号表
                try:
                    from services.tools.lsp_tools import get_document_symbols as _gds
                    symbols = _call(_gds, fp)
                    if symbols.strip() and not symbols.startswith("错误"):
                        context_parts.append(f"【{fp} 符号表】\n{symbols[:2000]}")
                except Exception:
                    pass
        except Exception:
            pass

    # ── 3. RAG 语义搜索 ──
    if time.time() - start_time < _TIMEOUT:
        try:
            from services.tools.rag_tools import search_code as _sc
            rag_result = _call(_sc, user_message, top_k=3)
            if rag_result and "没有找到" not in rag_result and not rag_result.startswith("错误"):
                rag_text = rag_result[:2500]
                context_parts.append(f"【语义相关代码片段】\n{rag_text}")
        except Exception:
            pass

    if not context_parts:
        return ""

    context = "\n\n".join(context_parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...(上下文已截断)"

    return (
        "\n\n【预处理上下文（以下内容已在收到你的请求后自动搜索并读取，"
        "请直接基于这些信息进行分析和操作，无需重复调用 search_files / read_file）】\n"
        f"{context}\n"
    )


_PROMPT_TEMPLATE_PATH = os.path.join(CONFIG_DIR, "system_prompt.md")
_PROMPT_API_TEMPLATE_PATH = os.path.join(CONFIG_DIR, "system_prompt_api.md")


def _build_system_prompt(tools, workspace_path: str = None, api_mode: bool = False) -> str:
    """构建系统提示词：读取 md 模板，注入工具描述、工具名列表和工作区路径"""
    lines = []
    for t in tools:
        schema = t.args_schema.model_json_schema() if hasattr(t, 'args_schema') and t.args_schema else {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        param_parts = []
        for pname, pinfo in props.items():
            mark = "（必填）" if pname in required else "（可选）"
            param_parts.append(f"{pname}: {pinfo.get('description', '')}{mark}")
        param_desc = "; ".join(param_parts) if param_parts else "无参数"
        desc_first = t.description.strip().split(chr(10))[0]
        lines.append(f"- {t.name}: {desc_first}\n  参数: {param_desc}")

    tool_desc = "\n".join(lines)
    tool_names = ", ".join(t.name for t in tools)

    workspace_context = ""
    if workspace_path and isinstance(workspace_path, str) and os.path.isdir(workspace_path):
        # 尝试从缓存获取项目概览（目录树+统计），避免 Agent 反复调用 scan_project
        try:
            from services.tools import get_cached_project_overview, get_rag_status_for_prompt
            overview = get_cached_project_overview(workspace_path)
            rag_status = get_rag_status_for_prompt(workspace_path)
            extra_parts = []
            if overview:
                overview_short = overview[:3000]  # 限制注入长度，避免爆 token
                extra_parts.append(
                    f"以下为该项目的扫描概览（已缓存，后续轮次可直接基于此信息操作，无需重复扫描）：\n"
                    f"{overview_short}"
                )
            if rag_status:
                extra_parts.append(rag_status)
            if extra_parts:
                workspace_context = f"\n【当前工作区】{workspace_path}\n" + "\n".join(extra_parts) + "\n"
            else:
                workspace_context = (
                    f"\n【当前工作区】{workspace_path}\n"
                    f"用户未指定路径时，默认在此目录下操作。用户提到的「项目」「这个项目」即指此目录。"
                    f"首次分析项目时应调用 scan_project 了解项目结构。\n"
                )
        except Exception:
            workspace_context = f"\n【当前工作区】{workspace_path}\n用户未指定路径时，默认在此目录下操作。用户提到的「项目」「这个项目」即指此目录。\n"

    template_path = _PROMPT_API_TEMPLATE_PATH if api_mode else _PROMPT_TEMPLATE_PATH
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    return template.format(tool_desc=tool_desc, tool_names=tool_names, workspace_context=workspace_context)


def _repair_malformed_tool_json(text: str) -> str:
    """修复模型常见的畸形工具 JSON"""
    if not text:
        return text
    t = text.strip()
    t = re.sub(r'"parameter\s*name"\s*>\s*"file_path"', '"file_path"', t, flags=re.I)
    t = re.sub(r'"parameter\s*name"\s*>\s*file_path', '"file_path"', t, flags=re.I)
    t = re.sub(r'"parameters"\s*:', '"input":', t)
    t = re.sub(r'\}+"\s*$', '}', t)
    return t


def _infer_file_path_from_user_message(user_message: str, content: str = "") -> str:
    """从用户请求推断 write_file 目标路径"""
    if not user_message:
        return ""
    msg = user_message.replace("\\", "/")
    dir_match = re.search(
        r"(?:在|于)?\s*([A-Za-z]:(?:/[A-Za-z0-9_.\-]+)+)",
        msg,
    )
    base_dir = dir_match.group(1).rstrip("/") if dir_match else ""

    explicit = re.search(r"([A-Za-z]:(?:/[^/\s\"']+)+\.py)", msg)
    if explicit:
        return explicit.group(1)

    sort_names = {
        "快速排序": "quick_sort.py",
        "希尔排序": "shell_sort.py",
        "冒泡排序": "bubble_sort.py",
        "插入排序": "insertion_sort.py",
        "选择排序": "selection_sort.py",
        "归并排序": "merge_sort.py",
        "堆排序": "heap_sort.py",
    }
    filename = ""
    for key, fname in sort_names.items():
        if key in user_message:
            filename = fname
            break
    if not filename:
        py_match = re.search(r"([\w_]+\.py)", msg)
        if py_match:
            filename = py_match.group(1)
        elif "quick_sort" in content:
            filename = "quick_sort.py"
        else:
            filename = "script.py"

    if base_dir:
        return f"{base_dir}/{filename}"
    return filename


def _fix_python_boilerplate(content: str) -> str:
    """修复模型生成的常见 Python 语法错误"""
    if not content:
        return content
    return re.sub(r"if\s+name\s*==\s*['\"]main['\"]", "if __name__ == '__main__'", content)


_BROWSER_REFUSAL_MARKERS = (
    # 原有
    "无法访问", "不能直接", "不能按你这个", "没有那个执行",
    "无法按你这个", "没有连接", "missing_ok=True", "unlink(",
    "在当前对话环境里", "没有那个执行器", "也不会被实际运行",
    "如果你是在做一个 Agent", "我不能直接",
    # 新增：路径/文件不存在类拒绝
    "路径找不到", "找不到文件", "文件找不到", "找不到路径",
    "目录名不对", "路径有问题", "路径可能", "可能不存在",
    "似乎不存在", "看起来不存在", "我找不到",
    # 新增：确认/质疑类拒绝
    "你能确认", "确认一下", "你能提供", "请提供",
    "是否确定", "是不是写错了", "路径是否正确",
    "检查一下路径", "核实一下", "请检查",
    # 新增：能力声明类拒绝
    "我无法直接访问", "无法直接访问你的", "我无法读取",
    "没有权限访问", "不能读取", "不能访问本地",
    "我无法查看", "我看不到",
    # 新增：要求替代方案
    "你可以手动", "请手动", "建议你手动", "请你自己",
    "换个方式", "换个模型",
)

# —— 正则拒绝模式（部分拒绝用模糊匹配） ——
_BROWSER_REFUSAL_PATTERNS = [
    re.compile(r"(?:路径|目录|文件|文件夹).{0,10}(?:找不|不存|有误|错误|不对|无效|非法)", re.I),
    re.compile(r"(?:找不到|无法找到|未找到).{0,10}(?:路径|目录|文件|文件夹)", re.I),
    re.compile(r"(?:确认|核实|检查|验证).{0,10}(?:路径|目录名|文件名)", re.I),
    re.compile(r"(?:我|系统|环境).{0,6}(?:没有|无法|不能|无权|不可以).{0,6}(?:访问|读取|查看|打开|操作|写入|执行)", re.I),
    re.compile(r"(?:你确定|你确认|你真的).{0,15}(?:路径|目录|文件|存在)", re.I),
]

_TOOL_SUCCESS_PREFIX = "成功:"
_CODE_ERROR_MARKERS = ("⚠ 语法错误:", "⚠ 编译错误:", "❌ 执行失败")


def _is_browser_model_refusal(text: str) -> bool:
    if not text:
        return False
    # 子串精确匹配
    if any(marker in text for marker in _BROWSER_REFUSAL_MARKERS):
        return True
    # 正则模糊匹配
    for pat in _BROWSER_REFUSAL_PATTERNS:
        if pat.search(text):
            return True
    return False


# API 模型拒绝执行代码的典型话术
_CODE_EXEC_REFUSAL_MARKERS = (
    "我无法直接执行代码", "我无法执行代码", "我没有运行环境",
    "我无法运行代码", "我不能执行代码", "我不能运行代码",
    "无法直接执行", "没有执行能力", "无法在本地运行",
    "请在本地环境中运行", "你可以在本地", "建议你在本地",
    "我无法直接运行", "我没有代码执行", "我无法访问你的系统",
    "由于我无法直接", "作为一个 ai", "作为 ai 助手",
    "我没有终端", "我无法访问终端", "无法调用系统命令",
)

_CODE_EXEC_REFUSAL_PATTERNS = [
    re.compile(r"(?:我|AI|模型).{0,6}(?:无法|不能|没有|不可以).{0,6}(?:执行|运行|跑).{0,6}(?:代码|脚本|程序|文件)", re.I),
    re.compile(r"(?:无法|不能).{0,6}(?:直接)?(?:执行|运行).{0,6}(?:代码|脚本|程序)", re.I),
    re.compile(r"(?:建议|请|你可以).{0,6}(?:在本地|手动|自己).{0,6}(?:运行|执行|测试)", re.I),
]


def _is_code_execution_refusal(text: str) -> bool:
    """检测 API 模型是否拒绝执行代码（应调用 execute_code 但没有）"""
    if not text:
        return False
    if any(marker in text for marker in _CODE_EXEC_REFUSAL_MARKERS):
        return True
    for pat in _CODE_EXEC_REFUSAL_PATTERNS:
        if pat.search(text):
            return True
    return False


def _looks_like_delete_request(msg: str) -> bool:
    lower = (msg or "").lower()
    return any(k in lower for k in ("删除", "delete", "remove", "删掉"))


def _infer_delete_path_from_user_message(user_message: str) -> str:
    if not user_message:
        return ""
    msg = user_message.replace("\\", "/")
    patterns = (
        r"删除\s*([A-Za-z]:(?:/[^/\s\"']+)+)",
        r"delete\s+([A-Za-z]:(?:/[^/\s\"']+)+)",
        r"([A-Za-z]:(?:/[^/\s\"']+\.(?:py|txt|json|md|xml|js|ts|cpp|java|yaml|yml)))",
    )
    for pat in patterns:
        m = re.search(pat, msg, re.I)
        if m:
            return m.group(1)
    return ""


def _looks_like_read_request(msg: str) -> bool:
    lower = (msg or "").lower()
    return any(k in lower for k in (
        "读取", "读文件", "读一下", "读本地", "查看文件", "打开文件",
        "read_file", "read file", "show file", "open file", "看看", "看下",
    )) or (bool(re.search(r"(?:读|查看|打开)\s*[A-Za-z]:", msg or "", re.I)))


def _looks_like_project_scan_request(msg: str) -> bool:
    return looks_like_project_scan_request(msg)


def _infer_read_path_from_user_message(user_message: str) -> str:
    if not user_message:
        return ""
    msg = user_message.replace("\\", "/")
    patterns = (
        r"(?:读取|读|查看|打开|read|open|show)\s*([A-Za-z]:(?:/[^/\s\"']+)+)",
        r"([A-Za-z]:(?:/[^/\s\"']+)+\.(?:py|txt|json|md|xml|js|ts|cpp|java|yaml|yml|csv|html|css|toml|ini|cfg|bat|sh))",
    )
    for pat in patterns:
        m = re.search(pat, msg, re.I)
        if m:
            return m.group(1)
    return _infer_delete_path_from_user_message(user_message)


def _infer_directory_from_user_message(user_message: str, workspace_path: str = None) -> str:
    """从用户消息推断目标目录，若无明确路径则回退到工作区"""
    if not user_message:
        return workspace_path or ""
    msg = user_message.replace("\\", "/")
    candidates = re.findall(r"([A-Za-z]:(?:/[A-Za-z0-9_.\-]+)+)", msg)
    if not candidates:
        return workspace_path or ""
    for cand in reversed(candidates):
        cand = cand.rstrip("/")
        if os.path.isdir(cand):
            return cand.replace("\\", "/")
    best = candidates[-1].rstrip("/")
    while best:
        if os.path.isdir(best):
            return best.replace("\\", "/")
        parent = os.path.dirname(best)
        if not parent or parent == best:
            break
        best = parent
    return workspace_path or candidates[-1].rstrip("/")


def _response_claims_file_missing(text: str) -> bool:
    if not text:
        return False
    markers = (
        "文件不存在", "找不到", "不存在", "无法读取", "无法打开",
        "does not exist", "not found", "no such file", "cannot read",
    )
    lower = text.lower()
    return any(m in text or m in lower for m in markers)


def _should_direct_tool_fallback(response: str, model_display: str, user_message: str) -> bool:
    if _parse_tool_call(response, user_message):
        return False
    if not (
        _looks_like_tool_task(user_message)
        or _looks_like_project_scan_request(user_message)
        or _user_requires_tool_first(user_message)
    ):
        return False
    if _is_browser_model_refusal(response) or _response_claims_file_missing(response):
        return True
    if _response_mentions_tool_without_json(response):
        return True
    if _user_requires_tool_first(user_message):
        return True
    try:
        from services.core.api_service import is_browser_agent_model
        if is_browser_agent_model(model_display):
            return True
    except ImportError:
        pass
    return False


def _response_mentions_tool_without_json(response: str) -> bool:
    """模型用自然语言提到工具名但未输出 JSON"""
    if not response or _parse_tool_call(response):
        return False
    return any(
        name in response
        for name in ("read_file", "write_file", "scan_project", "list_directory", "delete_file")
    )


def _looks_like_fix_or_modify_request(msg: str) -> bool:
    if not msg:
        return False
    lower = msg.lower()
    fix_keywords = (
        "修复", "改正", "修正", "改代码", "修改代码", "修改文件", "改文件",
        "语法错误", "编译错误", "fix", "优化代码", "重构代码", "重写",
        "改一下", "修改一下", "更新代码", "纠正",
    )
    if not any(k in lower for k in fix_keywords):
        return False
    return bool(
        _infer_read_path_from_user_message(msg)
        or re.search(r"[\w\-]+\.(?:py|js|ts|java|cpp|go|rs)", lower)
    )


def _looks_like_pure_read_request(msg: str) -> bool:
    """仅查看文件内容，不涉及修改"""
    if _looks_like_fix_or_modify_request(msg):
        return False
    return _looks_like_read_request(msg)


def _looks_like_context_dependent_question(msg: str) -> bool:
    """检测是否是需要读取现有文件/项目上下文才能回答的问题（非纯代码生成/通用知识）"""
    if not msg:
        return False
    # 1. 明确包含文件路径
    if re.search(r'[A-Za-z]:[/\\]', msg):
        return True
    # 2. 引用了具体文件名（.py / .json / .md 等）
    if re.search(r'\b[\w\-]+\.(?:py|js|ts|java|cpp|c|h|go|rs|html|css|json|xml|yaml|yml|md|txt|toml|ini|cfg|db)\b', msg.lower()):
        return True
    # 3. 语境依赖表述 — 需要先看文件才能回答
    ctx_markers = [
        "这个项目", "当前项目", "项目里", "我的项目",
        "这个文件", "这个代码", "这段代码", "这份代码",
        "这里", "这里的问题", "这里怎么",
        "帮我看看", "帮我看下", "检查一下", "审查一下",
        "是什么原因", "为什么会这样", "怎么回事", "哪里有问题", "哪里不对",
        "为什么报错", "为什么失败了", "怎么修复",
        "这个函数", "这个类", "这个模块", "这个方法",
        "修改一下", "改一下", "优化一下", "重构一下",
        "代码里", "源码里", "项目结构",
        "分析一下这个", "看看这个项目",
    ]
    if any(m in msg for m in ctx_markers):
        return True
    # 4. 指代现有代码的表述
    deictic_patterns = [
        r'(?:这个|那个|这些|那些)\s*(?:文件|代码|函数|类|模块|项目|bug|错误|问题|逻辑|实现)',
        r'(?:这里|那里|上面|前面)(?:\s*(?:的|有|怎么|什么|为什么))',
        r'(?:帮我|给我|替我)\s*(?:看看|检查|分析|审查|修复|修改|优化|重构)',
    ]
    if any(re.search(p, msg) for p in deictic_patterns):
        return True
    return False


def _looks_like_multiple_tasks(user_message: str) -> bool:
    """检测用户消息是否包含多个独立任务（用于判断是否应 auto-finalize）"""
    if not user_message:
        return False
    msg = user_message.strip()
    # 数字编号列表
    if re.search(r'(?:^|\n)\s*\d+\s*[\.．、\)]\s*.{4,}', msg):
        return True
    # 中文编号
    if re.search(r'[第一二三四五六七八九十][一二三四五六七八九十\d]?[、．\.:：]', msg):
        return True
    # 并列连词 + 独立动词
    if re.search(r'[,，;；]\s*(?:并且|同时|另外|此外|然后|接着|之后|还有|以及)\s*.{4,}', msg):
        return True
    # 多个以分号分隔的长片段
    parts = [p for p in re.split(r'[;；]', msg) if p.strip() and len(p.strip()) > 8]
    if len(parts) >= 3:
        return True
    # "并"连接的复合任务："生成...并运行""写入...并执行""创建...并测试"等
    compound_patterns = [
        r'(?:生成|创建|写入|实现|编写|写一个|写段|制作)\s*.{2,}?\s*并\s*(?:运行|执行|测试|验证)',
        r'(?:修改|更新|修复)\s*.{2,}?\s*并\s*(?:运行|执行|测试|验证)',
        r'(?:写|创建|生成)\s*.{2,}?\s*[，,然后接着]\s*(?:运行|执行|跑|测试)',
        r'(?:运行|执行|测试)\s*.{2,}?\s*并\s*(?:给出|输出|返回|查看)\s*(?:结果|输出)',
    ]
    for pat in compound_patterns:
        if re.search(pat, msg):
            return True
    return False


def _looks_like_false_file_completion(content: str, has_written_file: bool = False) -> bool:
    """检测模型是否假称已完成文件写入但并未实际调用 write_file 工具。
    仅检测「写入/创建/修改/保存」类声明，排除「读取/运行/执行」类声明。"""
    if not content or has_written_file:
        return False
    content_lower = content.lower()

    # 排除：读取/运行/执行类声明（这些不是写入幻觉）
    read_execute_markers = (
        "已成功读取", "已读取", "成功读取", "读取成功", "已成功运行", "已运行",
        "成功运行", "已成功执行", "已执行", "成功执行", "执行成功",
        "已成功扫描", "已扫描", "成功扫描", "扫描成功",
        "已成功分析", "已分析", "成功分析",
    )
    # 如果内容仅声称读取/运行/执行成功，不算写入幻觉
    # 检测是否有写入类声明
    write_claim_patterns = (
        "已经添加", "已经修改", "已修改", "已创建", "已写入",
        "已经写入", "已更新", "已经更新", "已保存", "已经保存",
        "成功写入", "已生成", "已经生成", "已添加", "成功创建",
        "已创建文件", "已写入文件", "已保存文件", "已修改文件",
    )
    # 通用的「已成功」只在后面紧跟写入相关词时才匹配
    has_write_claim = any(k in content_lower for k in write_claim_patterns)
    # 特殊处理「已成功」：检查上下文是否为写入操作
    if not has_write_claim and "已成功" in content_lower:
        # 查找「已成功」后面的 10 个字符，判断是否为写入操作
        for m in re.finditer(r"已成功", content_lower):
            after = content_lower[m.end():m.end() + 10]
            if any(w in after for w in ("写入", "创建", "修改", "保存", "添加", "生成", "更新")):
                has_write_claim = True
                break
    if not has_write_claim:
        return False
    # 如果同时存在读取/运行声明且不存在明确的写入声明，跳过
    has_read_only = any(k in content_lower for k in read_execute_markers)
    if has_read_only and not has_write_claim:
        return False
    # 必须同时包含代码块或文件引用（排除纯知识回答）
    has_code = "```" in content
    has_file_ref = bool(re.search(r"文件|file|\.py\b|\.js\b|\.ts\b|\.java\b|\.cpp\b", content_lower))
    return has_code or has_file_ref


def _looks_like_run_request(user_message: str) -> bool:
    """检测用户是否想要运行/执行某个文件"""
    if not user_message:
        return False
    msg = user_message.lower()
    run_kw = ("运行", "执行", "跑一下", "跑一遍", "跑起来", "测试一下", "测试用例",
              "输出结果", "生成结果", "看看结果", "run", "execute", "test it",
              "运行看看", "执行一下", "跑个结果", "直接运行出结果")
    # 命中运行关键词
    if any(k in msg for k in run_kw):
        # 有具体文件路径
        if re.search(r"[A-Za-z]:[/\\].+\.py", user_message, re.I) or ".py" in msg:
            return True
        # 无路径但明确提到要运行的代码/程序/脚本/文件
        if any(k in msg for k in ("代码", "这段", "这个脚本", "这个程序", "脚本",
                                    "程序", "应用", "文件", "代码段",
                                    "the code", "this code", "this script", "app",
                                    "program", "script", "file", "code")):
            return True
        # 提到了"刚刚""之前""上次""刚才"等时间引用，很可能指向历史中刚创建的文件
        if any(k in msg for k in ("刚刚", "之前", "上次", "刚才", "前面",
                                    "just", "before", "previous", "earlier")):
            return True
    return False


def _infer_run_path_from_history(history: list, user_message: str, workspace_path: str = "") -> str:
    """从对话历史中推断用户想运行的 .py 文件路径。
    当用户说"运行刚刚写的程序"但未提供路径时，查找最近的 .py 文件。
    策略：
    1. 从 write_file 工具调用中提取 file_path
    2. 从 assistant 消息中查找绝对路径 .py 文件
    3. 从 assistant 消息中查找创建/写了/ wrote/created 等动词后的相对路径
    4. 把相对路径和 workspace 拼接
    """
    if not history:
        return ""
    if workspace_path:
        workspace_path = workspace_path.replace("\\", "/").rstrip("/")

    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content", ""))
        if not content:
            continue

        # 策略 1：查找 write_file 工具调用的 file_path（历史消息中可能包含 JSON）
        # 匹配常见的 JSON 格式: "file_path": "xxx.py"
        fp_matches = re.findall(
            r'["\']file_path["\']\s*[:=]\s*["\']([^"\']+\.py)["\']',
            content
        )
        if fp_matches:
            candidate = fp_matches[-1]
            # 如果是绝对路径，直接返回
            if re.match(r'[A-Za-z]:[/\\]', candidate):
                return candidate.replace("\\", "/")
            # 相对路径，拼接 workspace
            if workspace_path:
                resolved = os.path.join(workspace_path, candidate).replace("\\", "/")
                if os.path.exists(resolved):
                    return resolved
                return resolved

        # 策略 2：查找绝对路径 .py 文件
        abs_matches = re.findall(r'([A-Za-z]:(?:[/\\][^/\s"\']+)+\.py)', content, re.I)
        if abs_matches:
            return abs_matches[-1].replace("\\", "/")

        # 策略 3：查找"创建/写入/写了/wrote/created"上下文中的相对 .py 路径
        rel_matches = re.findall(
            r'(?:创建|写入|写了|生成|保存在|位于|路径|在|wrote|created|saved|written|at|in|path)'
            r'\s*[:：]?\s*["\']?([^/\s"\'<>]+/[^/\s"\'<>]+\.py)["\']?',
            content
        )
        if rel_matches:
            candidate = rel_matches[-1].strip().replace("\\", "/")
            if workspace_path:
                resolved = os.path.join(workspace_path, candidate).replace("\\", "/")
                if os.path.exists(resolved):
                    return resolved
                return resolved

        # 策略 4：查找任何 .py 文件路径（相对或绝对）
        any_py = re.findall(r'([^\s"\'<>\[\](){}]+\.py)', content)
        if any_py:
            # 优先绝对路径
            for p in any_py:
                p_clean = p.strip().replace("\\", "/")
                if re.match(r'[A-Za-z]:[/\\]', p_clean):
                    return p_clean
            # 返回最长匹配（大概率是完整路径而非碎片），拼接 workspace
            longest = max(any_py, key=len).strip().replace("\\", "/")
            if workspace_path and not re.match(r'[A-Za-z]:[/\\]', longest):
                resolved = os.path.join(workspace_path, longest).replace("\\", "/")
                if os.path.exists(resolved):
                    return resolved
                return resolved

    return ""


def _user_requires_tool_first(user_message: str) -> bool:
    # 明确需要工具的操作
    if (_looks_like_read_request(user_message)
        or _looks_like_delete_request(user_message)
        or _looks_like_project_scan_request(user_message)
        or _looks_like_run_request(user_message)
        or bool(_infer_read_path_from_user_message(user_message))):
        return True
    # 广义检测：涉及现有文件/项目上下文的提问，若模型首轮没调工具则提醒
    if _looks_like_context_dependent_question(user_message):
        return True
    return False


def _build_direct_tool_calls(user_message: str, workspace_path: str = None, history: list = None) -> list:
    """根据用户意图构造本地工具调用（不依赖模型 JSON 输出），工作区路径作为兜底。"""
    if _looks_like_delete_request(user_message):
        path = _infer_delete_path_from_user_message(user_message)
        if path:
            return [{"tool": "delete_file", "input": {"file_path": path}}]

    if _looks_like_project_scan_request(user_message):
        dir_path = _infer_directory_from_user_message(user_message, workspace_path)
        if dir_path:
            return [{"tool": "scan_project", "input": {"dir_path": dir_path}}]

    # 运行请求：直接调用 execute_code 执行文件并显示终端结果
    if _looks_like_run_request(user_message):
        path = _infer_read_path_from_user_message(user_message)
        # 无路径时从历史中查找
        if not path and history:
            path = _infer_run_path_from_history(history, user_message, workspace_path or "")
        if path:
            return [{"tool": "execute_code", "input": {"file_path": path}}]

    path = _infer_read_path_from_user_message(user_message)
    if path and (
        _looks_like_fix_or_modify_request(user_message)
        or _looks_like_read_request(user_message)
        or (
            _looks_like_context_dependent_question(user_message)
            and re.search(r"\.(?:py|js|ts|java|cpp|go|rs)\b", path, re.I)
        )
    ):
        return [{"tool": "read_file", "input": {"file_path": path}}]

    lower = (user_message or "").lower()
    if any(k in lower for k in ("列出目录", "列目录", "目录结构", "有哪些文件", "list_directory")):
        dir_path = _infer_directory_from_user_message(user_message, workspace_path)
        if dir_path:
            return [{"tool": "list_directory", "input": {"dir_path": dir_path}}]

    return []


def _try_direct_tool_fallbacks(user_message: str, response: str, model_display: str, workspace_path: str = None, history: list = None) -> list:
    """模型未输出 JSON 时，根据用户意图直接构造本地工具调用。"""
    if not _should_direct_tool_fallback(response, model_display, user_message):
        return []
    return _build_direct_tool_calls(user_message, workspace_path, history=history)


def _try_direct_delete_fallback(user_message: str, response: str, model_display: str) -> list:
    """兼容旧调用：委托给统一 fallback。"""
    if not _looks_like_delete_request(user_message):
        return []
    return _try_direct_tool_fallbacks(user_message, response, model_display)


def _tool_result_success(tool_result: str) -> bool:
    res = str(tool_result or "")
    if not res.startswith(_TOOL_SUCCESS_PREFIX):
        return False
    # 即使开头是「成功:」，如果包含语法/执行错误也不视为成功
    if any(m in res for m in _CODE_ERROR_MARKERS):
        return False
    return True


def _tool_results_need_code_fix(tool_results: list) -> bool:
    body = "\n\n".join(str(r) for r in (tool_results or []))
    return any(m in body for m in _CODE_ERROR_MARKERS)


def _sanitize_tool_input(tool_name: str, tool_input: dict) -> dict:
    """清理 LangChain schema 与 LLM 幻觉产生的无效参数名。"""
    if not isinstance(tool_input, dict):
        return tool_input
    cleaned = dict(tool_input)
    if tool_name == "execute_code":
        vargs = cleaned.pop("v__args", None)
        if vargs is not None and "script_args" not in cleaned:
            if isinstance(vargs, list):
                cleaned["script_args"] = " ".join(str(x) for x in vargs if x is not None and str(x))
            elif isinstance(vargs, str):
                cleaned["script_args"] = vargs
        legacy_args = cleaned.pop("args", None)
        if legacy_args is not None and "script_args" not in cleaned:
            cleaned["script_args"] = legacy_args
    for key in list(cleaned.keys()):
        if key.startswith("v__") or key.startswith("_"):
            cleaned.pop(key, None)
    return cleaned

def _build_tool_followup_user_message(tool_results: list, user_message: str = "") -> str:
    body = "\n\n".join(tool_results)
    if _tool_results_need_code_fix(tool_results):
        return (
            body
            + "\n\n【必须修正】上述代码存在语法/编译/执行错误。"
            "请根据错误信息修正后重新调用 write_file（只输出一行 JSON），"
            "直到语法检查通过。"
        )
    # 检测多步任务：已写文件但还需要运行
    intent = parse_task_intent(user_message) if user_message else TaskIntent()
    is_multi_step = intent.is_multi_step
    needs_run = intent.needs_run
    has_execute_result = any('execute_code' in r for r in tool_results)
    has_write_result = any('write_file' in r for r in tool_results)
    summary_hint = followup_after_execute(intent, has_execute_result)
    if summary_hint:
        return body + f"\n\n用户原始问题：{user_message}\n" + summary_hint
    if is_multi_step and has_write_result and needs_run and not has_execute_result:
        return (
            body
            + f"\n\n用户原始问题：{user_message}\n"
            "文件已写入成功。用户还要求运行代码并给出结果。"
            "请立即调用 execute_code 工具运行刚写入的文件（只输出一行 JSON）。"
        )
    if is_multi_step and needs_run and not has_execute_result:
        return (
            body
            + f"\n\n用户原始问题：{user_message}\n"
            "用户要求运行代码并给出结果，请调用 execute_code 工具运行代码。"
        )
    user_context = f"\n\n用户原始问题：{user_message}\n" if user_message else "\n\n"
    return (
        body
        + user_context
        + "请根据以上工具返回的结果，用中文直接回复用户。"
        "若任务已完成，输出最终总结，不要再调用工具。"
    )


def _build_fix_followup_user_message(user_message: str, tool_results: list) -> str:
    body = "\n\n".join(tool_results)
    file_path = _infer_read_path_from_user_message(user_message) or _infer_file_path_from_user_message(user_message)
    path_hint = file_path or "用户消息中的文件路径"
    return (
        body
        + f"\n\n用户原始问题：{user_message}\n\n"
        "请根据以上文件内容和用户要求完成修改。"
        "你必须只输出一行纯 JSON 调用 write_file，content 为完整修正后的文件内容：\n"
        f'{{"tool":"write_file","input":{{"file_path":"{path_hint}","content":"完整代码"}}}}\n'
        "禁止 markdown 代码块、禁止任何解释文字。"
    )


def _messages_pending_code_fix(messages: list) -> bool:
    for m in reversed(messages or []):
        role = m.get("role")
        if role == "user":
            content = str(m.get("content", ""))
            return "【必须修正】" in content or (
                _tool_results_need_code_fix([content]) and "的返回结果:" in content
            )
        if role == "assistant":
            break
    return False


def _auto_finalize_message(tool_name: str, tool_call: dict, tool_result: str) -> str:
    inp = tool_call.get("input") or {}
    path = inp if isinstance(inp, str) else inp.get("file_path") or inp.get("path") or ""
    if tool_name == "delete_file" and _tool_result_success(tool_result):
        return f"已成功删除文件 {path or tool_result.split(' ', 2)[-1]}"
    if tool_name == "write_file" and _tool_result_success(tool_result):
        return f"已成功写入文件 {path}"
    if tool_name == "execute_code" and _tool_result_success(tool_result):
        fname = os.path.basename(path) if path else "脚本"
        return f"✅ 已成功运行 {fname}，执行结果请查看终端面板。\n\n```\n{tool_result[:3000]}\n```"
    return str(tool_result)


def _parse_tool_call(text: str, user_message: str = "") -> dict:
    """从 LLM 输出中解析单个工具调用。优先从 markdown 代码块提取，再尝试逐字符扫描。"""
    text = str(text) if not isinstance(text, str) else text
    text = _repair_malformed_tool_json(text)

    # 1. 优先从 markdown 代码块提取
    code_block_re = re.compile(r'```(?:json|python|javascript)?\s*\n?(.*?)\n?```', re.DOTALL)
    for m in code_block_re.finditer(text):
        candidate = m.group(1).strip()
        result = _safe_json_loads(candidate)
        if result:
            return result

    # 2. 逐字符扫描 {} 块
    result = _try_parse_json_object(text)
    if result:
        return result

    # 3. write_file 兜底：从含裸换行的 JSON 中提取完整 content
    wf = _extract_write_file_input(text, _infer_file_path_from_user_message(user_message))
    if wf:
        return wf

    return None


def _parse_all_tool_calls(text: str) -> list:
    """从 LLM 输出中解析所有工具调用（支持多个 JSON），使用 _safe_json_loads"""
    text = str(text) if not isinstance(text, str) else text
    results = []

    # 优先从 markdown 代码块中提取
    code_block_re = re.compile(r'```(?:json|python|javascript)?\s*\n?(.*?)\n?```', re.DOTALL)
    for m in code_block_re.finditer(text):
        candidate = m.group(1).strip()
        result = _safe_json_loads(candidate)
        if result:
            results.append(result)

    if results:
        return results

    # 从代码块外逐字符扫描 {} 块
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            in_string = False
            escape_next = False
            for j in range(i, len(text)):
                ch = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j + 1]
                        result = _safe_json_loads(candidate)
                        if result:
                            results.append(result)
                        i = j + 1
                        break
                else:
                    pass
            else:
                i += 1
        else:
            i += 1
    
    return results


def _parse_tool_calls(text: str, user_message: str = "") -> list:
    """解析工具调用，含 write_file 容错"""
    text = str(text) if not isinstance(text, str) else text
    text = _repair_malformed_tool_json(text)
    default_path = _infer_file_path_from_user_message(user_message) if user_message else ""
    results = _parse_all_tool_calls(text)
    if results:
        fixed = []
        for item in results:
            item = _normalize_tool_call(item)
            if item.get("tool") == "write_file" and isinstance(item.get("input"), dict):
                inp = item["input"]
                if not inp.get("file_path") and default_path:
                    inp["file_path"] = default_path
                if inp.get("content"):
                    inp["content"] = _fix_python_boilerplate(inp["content"])
            fixed.append(item)
        return fixed
    wf = _extract_write_file_input(text, default_path)
    if wf:
        wf["input"]["content"] = _fix_python_boilerplate(wf["input"]["content"])
        return [wf]
    return []


def _try_parse_json_object(text: str) -> dict:
    """从文本中找到第一个合法的 {"tool": ..., "input": ...} JSON 对象"""
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            in_string = False
            escape_next = False
            for j in range(i, len(text)):
                ch = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j + 1]
                        result = _safe_json_loads(candidate)
                        if result:
                            return result
                        break
        i += 1
    return None


def _normalize_tool_call(data):
    """Normalize tool call format - handle DeepSeek/MiniMax/OpenAI differences"""
    if "input" not in data:
        # DeepSeek uses "parameters" instead of "input"
        if "parameters" in data:
            data["input"] = data.pop("parameters")
        elif "args" in data:
            data["input"] = data.pop("args")
        elif "content" in data or "file_path" in data:
            data["input"] = {
                "file_path": data.pop("file_path", ""),
                "content": data.pop("content", ""),
            }
        else:
            data["input"] = {}
    
    inp = data["input"]
    if isinstance(inp, dict):
        # DeepSeek uses "path" instead of "file_path"
        if "path" in inp and "file_path" not in inp:
            inp["file_path"] = inp.pop("path")
        # DeepSeek uses "dir" instead of "dir_path"
        if "dir" in inp and "dir_path" not in inp:
            inp["dir_path"] = inp.pop("dir")
        # 清理 LLM 幻觉产生的无效参数（v__ 前缀、下划线前缀等）
        data["input"] = _sanitize_tool_input(data.get("tool", ""), inp)
    
    return data


def _extract_thinking_content(text: str) -> str:
    """提取思考模型的内部推理内容，返回提取的思考文本（可能为空）。

    支持:
      - Kimi: <｜begin▁of▁thinking｜> ... <｜end▁of▁thinking｜>
      - XML 风格: <thinking>...</thinking>, <reasoning>...</reasoning> 等
    """
    if not text:
        return ""
    parts = []

    # Kimi 标记
    for m in re.finditer(
        r'<｜begin▁of▁thinking｜>(.*?)<｜end▁of▁thinking｜>',
        text, flags=re.DOTALL | re.IGNORECASE,
    ):
        parts.append(m.group(1).strip())
    # 只有结束标记的情况（思考内容在标记之前）
    if '<｜end▁of▁thinking｜>' in text:
        idx = text.rfind('<｜end▁of▁thinking｜>')
        before = text[:idx]
        if before.strip() and not any(
            '<｜begin▁of▁thinking｜>' in before for _ in [None]
        ):
            # 去掉已有的 Kimi 完整块匹配部分
            remaining = re.sub(
                r'<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>',
                '', before, flags=re.DOTALL | re.IGNORECASE,
            )
            if remaining.strip():
                parts.append(remaining.strip())

    # XML 风格标签
    tags = ("thinking", "reasoning", "think", "analysis", "thought")
    for tag in tags:
        for m in re.finditer(
            rf'<{tag}(?:\s[^>]*)?>(.*?)</{tag}>',
            text, flags=re.DOTALL | re.IGNORECASE,
        ):
            content = m.group(1).strip()
            if content:
                parts.append(content)

    return "\n\n".join(parts)


def _strip_thinking_tokens(text: str) -> str:
    """移除思考模型的内部推理内容（thinking/reasoning 标签），防止泄漏到 UI。
    Kimi K2.6 思考、DeepSeek R1 等模型会在响应中嵌入 `thinking` 或 `reasoning` 块。
    """
    if not text:
        return text
    # 移除 Kimi 特有的 Unicode 标记: <｜begin▁of▁thinking｜> ... <｜end▁of▁thinking｜>
    text = re.sub(
        r'<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>',
        '',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 如果只有结束标记，移除它及其之前的所有内容（思考内容在标记之前）
    if '<｜end▁of▁thinking｜>' in text:
        idx = text.rfind('<｜end▁of▁thinking｜>')
        text = text[idx + len('<｜end▁of▁thinking｜>'):]
    # 移除 <｜begin▁of▁thinking｜> 到文本末尾（无结束标记的情况）
    text = re.sub(r'<｜begin▁of▁thinking｜>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
    # 移除残留的单独标记
    text = text.replace('<｜begin▁of▁thinking｜>', '').replace('<｜end▁of▁thinking｜>', '')

    # 移除 XML 风格的 thinking/reasoning/think/analysis 标签块
    tags = ("thinking", "reasoning", "think", "analysis", "thought")
    for tag in tags:
        # 完整标签: <tag>...</tag> 或 <tag attr>...</tag>
        text = re.sub(
            rf'<{tag}(?:\s[^>]*)?>.*?</{tag}>',
            '',
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # 自闭合或无结束标签: <tag>... (到文本末尾)
        text = re.sub(
            rf'<{tag}>\s*$',
            '',
            text,
            flags=re.IGNORECASE,
        )
    # 移除可能残留的空标签
    for tag in tags:
        text = text.replace(f'</{tag}>', '').replace(f'<{tag}>', '')
    # 移除 <｜end▁of▁thinking｜> (Kimi 思考结束标记，旧版兼容)
    text = re.sub(r'<｜end▁of▁thinking｜>\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


def _safe_json_loads(text: str) -> dict:
    """尝试解析 JSON 工具调用，仅两层回退：直接解析 → 修复反斜杠后解析"""
    text = _repair_malformed_tool_json(text)

    # 第1层：直接 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool" in data:
            return _normalize_tool_call(data)
    except json.JSONDecodeError:
        pass

    # 第2层：修复未转义的反斜杠后解析
    try:
        fixed = _fix_backslashes(text)
        data = json.loads(fixed)
        if isinstance(data, dict) and "tool" in data:
            return _normalize_tool_call(data)
    except json.JSONDecodeError:
        pass

    return None


def _escape_multiline_json_strings(text: str) -> str:
    """将 JSON 字符串值内的裸换行转义为 \\n"""
    result = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escape:
            result.append(ch)
            escape = False
            i += 1
            continue
        if ch == '\\' and in_string:
            result.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if in_string and ch == '\n':
            result.append('\\n')
            i += 1
            continue
        if in_string and ch == '\r':
            i += 1
            continue
        if in_string and ch == '\t':
            result.append('\\t')
            i += 1
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _scan_json_string_content(text: str, start: int) -> str:
    """从 opening quote 之后扫描 JSON 字符串，支持裸换行"""
    parts = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '\\' and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == 'n':
                parts.append('\n')
                i += 2
                continue
            if nxt == 't':
                parts.append('\t')
                i += 2
                continue
            if nxt == '"':
                parts.append('"')
                i += 2
                continue
            if nxt == '\\':
                parts.append('\\')
                i += 2
                continue
            parts.append(ch)
            i += 1
            continue
        if ch == '"':
            rest = text[i + 1:].lstrip()
            if not rest or rest[0] in ',}':
                break
            parts.append(ch)
            i += 1
            continue
        parts.append(ch)
        i += 1
    return ''.join(parts)


def _unescape_json_string(s: str) -> str:
    """还原 JSON 字符串中的常见转义"""
    return (s.replace('\\n', '\n').replace('\\t', '\t')
            .replace('\\"', '"').replace('\\\\', '\\'))


def _extract_write_file_content(text: str, start: int) -> str:
    """提取 write_file 的 content，兼容 content 内含未转义引号"""
    best = None
    pos = start
    while True:
        idx = text.find('"', pos)
        if idx == -1:
            break
        after = text[idx + 1:].lstrip()
        if after.startswith('}') or after.startswith('}}'):
            best = text[start:idx]
        pos = idx + 1
    if best is not None:
        return _unescape_json_string(best)
    scanned = _scan_json_string_content(text, start)
    return _unescape_json_string(scanned) if scanned else ""


def _extract_write_file_input(text: str, default_file_path: str = "") -> dict:
    """从含多行代码的 write_file JSON 中提取完整 content"""
    text = _repair_malformed_tool_json(text)
    if not re.search(r'"tool"\s*:\s*"write_file"', text):
        return None

    file_path = ""
    fp_match = re.search(r'"file_path"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if fp_match:
        file_path = _unescape_json_string(fp_match.group(1))
    elif default_file_path:
        file_path = default_file_path

    content_match = re.search(r'"content"\s*:\s*"', text)
    if not content_match:
        return None
    content = _extract_write_file_content(text, content_match.end())
    if not content or len(content.strip()) < 10:
        return None
    if not file_path:
        file_path = _infer_file_path_from_user_message("", content)
    if not file_path:
        return None
    return {"tool": "write_file", "input": {"file_path": file_path, "content": content}}


def _extract_json_value(text: str, start: int):
    """从指定位置提取 JSON 值（对象、字符串、数字等）"""
    if start >= len(text):
        return None
    ch = text[start]

    # 对象 {...}
    if ch == '{':
        return _extract_braced_object(text, start)

    # 字符串 "..."
    if ch == '"':
        end = start + 1
        while end < len(text):
            if text[end] == '\\':
                end += 2
                continue
            if text[end] == '"':
                raw = text[start + 1:end]
                # 尝试修复反斜杠后作为 JSON 字符串解析
                try:
                    return json.loads('"' + _fix_backslashes(raw) + '"')
                except Exception:
                    return raw
            end += 1
        return text[start + 1:]

    # 其他（数字、bool、null）
    end = start
    while end < len(text) and text[end] not in (',', '}', ']', '\n'):
        end += 1
    raw = text[start:end].strip()
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _extract_braced_object(text: str, start: int) -> dict:
    """用括号匹配提取完整的 {} 对象，然后尝试解析"""
    depth = 0
    in_string = False
    escape_next = False
    for j in range(start, len(text)):
        ch = text[j]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                candidate = text[start:j + 1]
                # 尝试直接解析
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
                # 修复反斜杠后解析
                try:
                    fixed = _fix_backslashes(candidate)
                    data = json.loads(fixed)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
                return None
    return None


def _fix_backslashes(text: str) -> str:
    """修复 JSON 字符串中未转义的反斜杠"""
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string:
            if i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch in ('"', '\\', '/', 'b', 'f', 'n', 'r', 't'):
                    result.append(ch)
                    result.append(next_ch)
                    i += 2
                    continue
                elif next_ch == 'u':
                    # \u 后必须跟 4 位十六进制才是合法 JSON 转义
                    hex_part = text[i+2:i+6] if i+6 <= len(text) else ""
                    if len(hex_part) == 4 and all(c in '0123456789abcdefABCDEF' for c in hex_part):
                        result.append(ch)
                        result.append(next_ch)
                        i += 2
                        continue
                    else:
                        result.append('\\\\')
                        i += 1
                        continue
                else:
                    result.append('\\\\')
                    i += 1
                    continue
            else:
                result.append('\\\\')
                i += 1
                continue
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


def _is_incomplete_file_content(content: str) -> bool:
    """检测 write_file 的 content 是否明显不完整（仅拦截几乎为空的内容）"""
    if not content or len(content.strip()) < 3:
        return True
    return False


def _looks_like_tool_call(text: str) -> bool:
    """检测响应是否看起来像是一个工具调用（但解析失败了）"""
    if not text:
        return False
    text = text.strip()
    if '"tool"' not in text:
        return False
    if '"write_file"' in text and '"content"' in text:
        return True
    if '"input"' in text or '"file_path"' in text or '"content"' in text:
        return True
    return '```' in text and '"tool"' in text


def split_user_tasks(user_message: str, model_display: str = "MiMo-V2-Flash") -> list:
    """
    分析用户输入，判断是否包含多个任务。
    如果是，返回拆分后的任务列表；否则返回空列表（表示单任务）。

    Returns:
        list[str] — 拆分出的任务列表；空列表或长度<=1 表示不需要拆分
    """
    if not user_message or len(user_message.strip()) < 10:
        return []

    # 快速启发式检测：明显包含多个独立任务的标记
    msg = user_message.strip()
    multi_task_markers = 0

    # 中文编号：第一、第二、第三 / 1、2、3 / 一、二、三
    if re.search(r'[第一二三四五六七八九十][一二三四五六七八九十\d][、\.．:：]', msg):
        multi_task_markers += 1

    # 数字+点号列表：1. xxx 2. xxx 3. xxx
    if re.search(r'(?:^|\n)\s*\d+\s*[\.．、\)]', msg):
        multi_task_markers += 2

    # 并列连词 + 动词结构：并且...、同时...、另外...
    if re.search(r'[,，]\s*(?:并且|同时|另外|此外|然后|接着|之后|还有|以及|和|与)\s*.{4,}', msg):
        multi_task_markers += 1

    # 多个独立句子（每句都有明确动词）
    sentences = re.split(r'[。！？!?\n]+', msg)
    verb_sentences = [s for s in sentences if len(s.strip()) > 5 and any(
        v in s for v in ('添加', '修改', '删除', '创建', '实现', '优化',
                        '重构', '增加', '增强', '替换', '调整', '改进'))]
    if len(verb_sentences) >= 3:
        multi_task_markers += 1

    # 分号分隔的多个长片段
    semicolon_parts = [p for p in re.split(r'[;；]', msg) if p.strip() and len(p.strip()) > 6]
    if len(semicolon_parts) >= 3:
        multi_task_markers += 1

    # 标记不足 → 不拆分
    if multi_task_markers < 2:
        return []

    try:
        from services.core.api_service import is_browser_agent_model
        if is_browser_agent_model(model_display):
            return []
    except ImportError:
        pass

    # 调用 LLM 进行精确拆分
    split_prompt = (
        "你是一个任务分析助手。请分析用户输入中是否包含多个独立的任务/需求。\n\n"
        "用户输入：\n{user_msg}\n\n"
        "要求：\n"
        "- 如果包含 2 个及以上独立任务，请将每个任务用简洁中文描述（每条不超过50字），"
        "以 JSON 数组格式输出，如：[\"任务一描述\", \"任务二描述\"]\n"
        "- 如果只有 1 个或不清楚，输出空数组：[]\n"
        "- 只输出 JSON，不要其他内容。\n"
    ).format(user_msg=msg[:800])

    try:
        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else None

        response = ""
        for chunk in chat_completion(
            messages=[{"role": "user", "content": split_prompt}],
            model=model_name,
            temperature=0.1,
            max_tokens=300,
        ):
            response += chunk

        response = response.strip()
        # 清理 markdown 代码块包裹
        m = re.search(r'\[.*\]', response, re.DOTALL)
        if m:
            response = m.group(0)

        tasks = json.loads(response)
        if isinstance(tasks, list) and len(tasks) >= 2:
            # 过滤空项并限制数量
            clean = [t for t in tasks if t and isinstance(t, str) and t.strip()]
            return clean[:8]  # 最多 8 个子任务
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return []


class AgentService:
    """Agent 服务 — 自定义 ReAct 循环"""

    def __init__(self):
        self._tools = get_all_tools()
        self._tool_map = {t.name: t for t in self._tools}
        self._config = get_agent_config()

    def clear_cache(self):
        pass

    def run(self, user_message: str, model_display: str = "MiMo-V2-Flash",
            history: list = None, max_steps: int = None, app_session_id: str = None,
            workspace_path: str = None) -> dict:
        results = list(self._run_iter(user_message, model_display, history, max_steps, app_session_id, workspace_path))
        output, steps = "", []
        for event in results:
            if event["type"] == "step":
                steps.append({"tool": event["tool"], "input": event["input"], "output": event["output"]})
            elif event["type"] in ("result", "error"):
                output = event["output"]
        return {"output": output, "steps": steps}

    def run_stream(self, user_message: str, model_display: str = "MiMo-V2-Flash",
                   on_step=None, history: list = None, max_steps: int = None,
                   app_session_id: str = None, status_callback=None,
                   workspace_path: str = None,
                   stop_event: Optional[threading.Event] = None) -> Generator:
        """在调用方线程中同步执行，便于 Qt 信号回调更新 UI"""
        yield {"type": "thinking", "output": "正在处理你的请求..."}
        try:
            for event in self._run_iter(
                user_message, model_display, history, max_steps, app_session_id,
                status_callback=status_callback,
                workspace_path=workspace_path,
                stop_event=stop_event,
            ):
                yield event
        except Exception as e:
            logger.error("Agent 运行异常", exc_info=True)
            yield {"type": "error", "output": f"Agent 错误: {e}"}

    def _run_preemptive_browser_tools(
        self,
        user_message: str,
        model_display: str,
        system_prompt: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        direct: list = None,
        custom_base_url=None,
        custom_api_key=None,
        app_session_id=None,
        status_callback=None,
        workspace_path: str = None,
    ) -> Generator:
        """
        浏览器 Agent 快速路径：先在本地执行工具，再按需只发 1 条浏览器消息做总结。
        避免 ReAct 每轮都往网页重复粘贴同一段工具格式 + 用户指令。
        """
        # 使用外部传入的 direct，不再内部重新构建（外部可能做了兜底构造）
        if not direct:
            return

        yield {
            "type": "thought",
            "output": "Agent：先在本地执行工具，再请模型处理结果。",
        }

        intent = parse_task_intent(user_message)
        tool_results = []
        auto_finalize = None
        for tool_call in direct:
            tool_name = tool_call.get("tool", "")
            tool_input = tool_call.get("input", "")
            if tool_name not in self._tool_map:
                tool_results.append(f"错误: 工具 '{tool_name}' 不存在。")
                continue
            # 通知 UI 开始编辑文件（写文件/编辑文件）
            if tool_name in ("write_file", "edit_file"):
                fp = tool_input if isinstance(tool_input, str) else (
                    tool_input.get("file_path", "") if isinstance(tool_input, dict) else "")
                if fp:
                    yield {"type": "tool_start", "tool": tool_name, "file_path": str(fp)}
            try:
                tool_result = self._invoke_tool_call(tool_name, tool_input)
            except Exception as e:
                tool_result = f"工具执行错误: {e}"

            yield {
                "type": "step",
                "tool": tool_name,
                "input": str(tool_input)[:200],
                "output": str(tool_result)[:500],
            }
            tool_results.append(f"工具 {tool_name} 的返回结果:\n{tool_result}")
            intent.record_tool(tool_name, tool_input, tool_result)

            if should_auto_finalize(tool_name, tool_result, intent, len(direct)):
                auto_finalize = (tool_name, tool_call, tool_result)

        if not auto_finalize:
            for tc in build_auto_continue_calls(intent):
                tool_name = tc.get("tool", "")
                tool_input = tc.get("input", "")
                yield {
                    "type": "thought",
                    "output": f"用户要求运行验证，正在本地执行 {tool_input.get('file_path', '脚本')}…",
                }
                # 通知 UI 开始编辑文件
                if tool_name in ("write_file", "edit_file"):
                    fp = tool_input.get("file_path", "") if isinstance(tool_input, dict) else str(tool_input)
                    if fp:
                        yield {"type": "tool_start", "tool": tool_name, "file_path": str(fp)}
                try:
                    tool_result = self._invoke_tool_call(tool_name, tool_input)
                except Exception as e:
                    tool_result = f"工具执行错误: {e}"
                yield {
                    "type": "step",
                    "tool": tool_name,
                    "input": str(tool_input)[:200],
                    "output": str(tool_result)[:500],
                }
                tool_results.append(f"工具 {tool_name} 的返回结果:\n{tool_result}")
                intent.record_tool(tool_name, tool_input, tool_result)
                if should_auto_finalize(tool_name, tool_result, intent, 1):
                    auto_finalize = (tool_name, tc, tool_result)
                    break

        if auto_finalize:
            tn, tc, tr = auto_finalize
            yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
            return

        if (
            len(direct) == 1
            and direct[0].get("tool") == "read_file"
            and tool_results
            and _looks_like_pure_read_request(user_message)
        ):
            tr = tool_results[0].split(":\n", 1)[-1]
            if not tr.startswith("错误"):
                yield {"type": "result", "output": tr}
                return

        summary_body = "\n\n".join(tool_results)
        summary_body = _truncate_browser_summary(summary_body)
        is_fix_task = _looks_like_fix_or_modify_request(user_message)
        if is_fix_task:
            summary_user = _build_fix_followup_user_message(user_message, tool_results)
        else:
            summary_user = _build_tool_followup_user_message(tool_results, user_message)
            if not _tool_results_need_code_fix(tool_results):
                summary_user = (
                    summary_body
                    + f"\n\n用户原始问题：{user_message}\n\n"
                    "请根据以上工具返回的结果，用中文直接回复用户。"
                    "若任务已完成，输出最终总结，不要再调用工具。"
                )
            else:
                summary_user = (
                    summary_user
                    + f"\n\n用户原始问题：{user_message}"
                )
        try:
            response = chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_user},
                ],
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                custom_base_url=custom_base_url,
                custom_api_key=custom_api_key,
                app_session_id=app_session_id,
                status_callback=status_callback,
            )
        except Exception as e:
            yield {"type": "error", "output": f"LLM 调用失败: {e}"}
            return

        if not response:
            yield {"type": "error", "output": "LLM 返回空响应。"}
            return

        # 如果 Preemptive 模式下 MiniMax 仍然拒绝，用更强硬提示重试一次
        if _is_browser_model_refusal(response):
            yield {"type": "thought", "output": "浏览器模型拒绝工具结果，正在强制重试..."}
            retry_user = (
                "【⚠ 工具已成功执行，以下结果真实有效】\n"
                + summary_body
                + "\n\n你的上一轮回复被判定为拒绝/质疑，已被自动忽略。\n"
                + f"用户原始问题：{user_message}\n\n"
                "现在你必须：① 认可上述工具结果为事实；"
                "② 基于工具结果用中文直接回复用户。"
                "禁止说「路径找不到」「无法访问」「请确认」等任何拒绝话术。"
            )
            try:
                response = chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": retry_user},
                    ],
                    model=model_name,
                    temperature=max(temperature - 0.2, 0.0),
                    max_tokens=max_tokens,
                    custom_base_url=custom_base_url,
                    custom_api_key=custom_api_key,
                    app_session_id=app_session_id,
                    status_callback=status_callback,
                )
            except Exception:
                pass

        if _tool_results_need_code_fix(tool_results) or is_fix_task:
            fix_rounds = min(3, self._config.get("max_steps", 10))
            fix_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": summary_user},
                {"role": "assistant", "content": response},
            ]
            for fix_i in range(fix_rounds):
                fix_calls = _parse_tool_calls(response, user_message)
                if not fix_calls:
                    tc = _parse_tool_call(response, user_message)
                    fix_calls = [tc] if tc else []
                if not fix_calls or fix_calls[0].get("tool") != "write_file":
                    if is_fix_task and fix_i == 0:
                        retry_user = _build_fix_followup_user_message(user_message, tool_results)
                        try:
                            response = chat_completion(
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": retry_user},
                                ],
                                model=model_name,
                                temperature=max(temperature - 0.1, 0.0),
                                max_tokens=max_tokens,
                                custom_base_url=custom_base_url,
                                custom_api_key=custom_api_key,
                                app_session_id=app_session_id,
                                status_callback=status_callback,
                            )
                        except Exception:
                            break
                        if response:
                            fix_messages = [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": retry_user},
                                {"role": "assistant", "content": response},
                            ]
                            continue
                    break
                tool_call = fix_calls[0]
                tool_input = tool_call.get("input", {})
                fp = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
                if fp:
                    yield {"type": "tool_start", "tool": "write_file", "file_path": str(fp)}
                try:
                    tool_result = self._invoke_tool_call("write_file", tool_input)
                except Exception as e:
                    tool_result = f"工具执行错误: {e}"
                yield {
                    "type": "step",
                    "tool": "write_file",
                    "input": str(tool_input)[:200],
                    "output": str(tool_result)[:500],
                }
                fp = tool_input if isinstance(tool_input, str) else tool_input.get("file_path", "")
                if fp and str(fp).lower().endswith(".py"):
                    tr_str = str(tool_result)
                    has_err = any(m in tr_str for m in ("⚠ 语法错误:", "⚠ 编译错误:"))
                    yield {
                        "type": "code_event",
                        "event": "syntax_check",
                        "file": str(fp),
                        "ok": not has_err,
                        "detail": tr_str.split("\n")[-1] if "\n" in tr_str else "",
                        "result": str(tool_result),
                    }
                if _tool_result_success(tool_result):
                    yield {"type": "result", "output": _auto_finalize_message("write_file", tool_call, tool_result)}
                    return
                tool_results = [f"工具 write_file 的返回结果:\n{tool_result}"]
                fix_user = _build_tool_followup_user_message(tool_results) + f"\n\n用户原始问题：{user_message}"
                fix_messages.append({"role": "user", "content": fix_user})
                try:
                    response = chat_completion(
                        messages=fix_messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        custom_base_url=custom_base_url,
                        custom_api_key=custom_api_key,
                        app_session_id=app_session_id,
                        status_callback=status_callback,
                    )
                except Exception as e:
                    yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                    return
                if not response:
                    break
                fix_messages.append({"role": "assistant", "content": response})

        yield {"type": "result", "output": response}

    def _invoke_tool_call(self, tool_name: str, tool_input):
        if tool_name not in self._tool_map:
            logger.warning(f"[TOOL_CALL_DIAG] 工具 '{tool_name}' 不在 tool_map 中！可用工具: {list(self._tool_map.keys())}")
            return f"错误: 工具 '{tool_name}' 不存在。"
        tool = self._tool_map[tool_name]
        if isinstance(tool_input, dict):
            orig_keys = set(tool_input.keys())
            tool_input = _sanitize_tool_input(tool_name, tool_input)
            tool_input = self._filter_tool_input(tool_name, tool_input)
            filtered_keys = set(tool_input.keys())
            removed = orig_keys - filtered_keys
            if removed:
                logger.warning(f"[TOOL_CALL_DIAG] _filter_tool_input 从 {tool_name} 中移除了参数: {removed}")
            try:
                result = tool.invoke(tool_input)
                logger.info(f"[TOOL_CALL_DIAG] {tool_name} 执行成功, 返回值前100字符={str(result)[:100]!r}")
                return result
            except TypeError as e:
                if "unexpected keyword argument" in str(e):
                    import inspect
                    func = getattr(tool, "func", None)
                    if func:
                        sig = inspect.signature(func)
                        valid = {k: v for k, v in tool_input.items() if k in sig.parameters}
                        result = func(**valid)
                        logger.info(f"[TOOL_CALL_DIAG] {tool_name} 通过TypeError回退执行成功")
                        return result
                raise
        else:
            logger.warning(f"[TOOL_CALL_DIAG] {tool_name} 的 input 类型是 {type(tool_input).__name__} 而非 dict: {str(tool_input)[:200]!r}")
        return tool.invoke(tool_input)

    def _filter_tool_input(self, tool_name: str, tool_input: dict) -> dict:
        """过滤掉 LLM 幻觉产生的无效参数，只保留工具函数实际接受的参数"""
        tool_input = _sanitize_tool_input(tool_name, tool_input)
        tool = self._tool_map.get(tool_name)
        if not tool:
            return tool_input
        # 从 args_schema 获取工具函数接受的参数名列表
        schema = getattr(tool, "args_schema", None)
        if schema is None:
            # 无 schema，尝试用 func 签名
            import inspect
            func = getattr(tool, "func", None)
            if func:
                try:
                    sig = inspect.signature(func)
                    valid_params = set(sig.parameters.keys())
                    return {k: v for k, v in tool_input.items() if k in valid_params}
                except Exception:
                    return tool_input
            return tool_input
        try:
            valid_params = set(schema.model_fields.keys() if hasattr(schema, "model_fields") else schema.__fields__.keys())
        except Exception:
            # schema 获取失败时，用 func 签名兜底
            import inspect
            func = getattr(tool, "func", None)
            if func:
                try:
                    sig = inspect.signature(func)
                    valid_params = set(sig.parameters.keys())
                except Exception:
                    return tool_input
            else:
                return tool_input
        # 同时允许函数签名中的参数（args_schema 可能不包含所有）
        import inspect
        func = getattr(tool, "func", None)
        if func:
            try:
                sig = inspect.signature(func)
                valid_params |= set(sig.parameters.keys())
            except Exception:
                pass
        # 过滤并记录被丢弃的参数
        filtered = {k: v for k, v in tool_input.items() if k in valid_params}
        dropped = [k for k in tool_input if k not in valid_params]
        if dropped:
            print(f"[Agent] 工具 {tool_name} 忽略了无效参数: {dropped}")
        # 修复：即使 filtered 为空也返回过滤后的结果，避免回退到含幻觉参数的原始输入
        return filtered

    def _execute_tool_steps(self, tool_name: str, tool_input, user_message: str = ""):
        """执行单个工具，yield step/code_event，返回 tool_result 字符串"""
        # 通知 UI 工具正在执行（用于状态行显示）
        tool_display_name = {
            'web_search': '正在搜索网络',
            'read_file': '正在读取文件',
            'write_file': '正在写入文件',
            'edit_file': '正在编辑文件',
            'execute_code': '正在执行代码',
            'list_files': '正在列出文件',
            'file_search': '正在搜索文件',
            'browser_action': '正在执行浏览器操作',
            'api_call': '正在调用 API',
        }.get(tool_name, f'正在执行 {tool_name}')

        yield {"type": "agent_status", "tool": tool_name, "action": tool_display_name}

        # 通知 UI 开始编辑文件
        if tool_name in ("write_file", "edit_file"):
            fp = tool_input if isinstance(tool_input, str) else (
                tool_input.get("file_path", "") if isinstance(tool_input, dict) else "")
            if fp:
                yield {"type": "tool_start", "tool": tool_name, "file_path": str(fp)}

        try:
            if tool_name == "write_file" and isinstance(tool_input, dict):
                content = tool_input.get("content", "")
                logger.info(
                    f"[TOOL_CALL_DIAG] write_file 准备执行: file_path={tool_input.get('file_path', '?')}, "
                    f"content长度={len(content)}, content前80字符={content[:80]!r}"
                )
                if _is_incomplete_file_content(content):
                    logger.warning(f"[TOOL_CALL_DIAG] write_file 被拦截: content不完整 (仅{len(content)}字符)")
                    tool_result = (
                        f"错误: 文件内容不完整（仅 {len(content)} 字符），"
                        "请重新调用 write_file，在 content 中写入完整代码。"
                    )
                else:
                    tool_result = self._invoke_tool_call(tool_name, tool_input)
            else:
                tool_result = self._invoke_tool_call(tool_name, tool_input)
        except Exception as e:
            tool_result = f"工具执行错误: {e}"

        yield {
            "type": "step",
            "tool": tool_name,
            "input": str(tool_input)[:200],
            "output": str(tool_result)[:500],
        }

        if tool_name == "write_file":
            fp = tool_input if isinstance(tool_input, str) else tool_input.get("file_path", "")
            if fp and str(fp).lower().endswith(".py"):
                tr_str = str(tool_result)
                has_err = any(m in tr_str for m in ("⚠ 语法错误:", "⚠ 编译错误:"))
                yield {
                    "type": "code_event",
                    "event": "syntax_check",
                    "file": str(fp),
                    "ok": not has_err,
                    "detail": tr_str.split("\n")[-1] if "\n" in tr_str else "",
                    "result": str(tool_result),
                }

        if tool_name == "edit_file":
            fp = tool_input if isinstance(tool_input, str) else tool_input.get("file_path", "")
            if fp and str(fp).lower().endswith(".py"):
                tr_str = str(tool_result)
                has_err = any(m in tr_str for m in ("⚠ 语法错误:", "⚠ 编译错误:"))
                yield {
                    "type": "code_event",
                    "event": "syntax_check",
                    "file": str(fp),
                    "ok": not has_err,
                    "detail": tr_str.split("\n")[-1] if "\n" in tr_str else "",
                    "result": str(tool_result),
                }

        if tool_name == "execute_code":
            fp = tool_input if isinstance(tool_input, str) else tool_input.get("file_path", "")
            tr_str = str(tool_result)
            ok = tr_str.startswith("成功:")
            yield {
                "type": "code_event",
                "event": "execution",
                "file": str(fp),
                "ok": ok,
                "detail": tr_str[:500],
                "result": tr_str,
            }

        return tool_result

    def _parse_text_tool_calls(self, content: str, user_message: str) -> list:
        """模型把工具 JSON 写在 content 里（非 API tool_calls）时解析"""
        if not content:
            return []
        calls = _parse_tool_calls(content, user_message)
        if calls:
            return calls
        single = _parse_tool_call(content, user_message)
        return [single] if single else []

    def _collect_tool_calls_from_response(self, content: str, api_tool_calls: list, user_message: str) -> list:
        """统一提取工具调用：API tool_calls 或 content 中的 JSON 文本（所有 API 模型通用）"""
        parsed = []
        for tc in api_tool_calls or []:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            if not name:
                continue
            raw = fn.get("arguments") or "{}"
            try:
                inp = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                inp = raw
            if isinstance(inp, dict):
                inp = _sanitize_tool_input(name, inp)
            parsed.append({"tool": name, "input": inp})
        if parsed:
            logger.info(f"[TOOL_CALL_DIAG] 从 API tool_calls 解析到 {len(parsed)} 个工具: {[p['tool'] for p in parsed]}")
            return parsed
        text_parsed = self._parse_text_tool_calls(content, user_message)
        if text_parsed:
            logger.info(f"[TOOL_CALL_DIAG] 从文本 JSON 解析到 {len(text_parsed)} 个工具: {[p['tool'] for p in text_parsed]}")
        else:
            # 诊断：为什么没解析到工具调用
            has_tool_keyword = '"tool"' in (content or "")
            has_write_file = '"write_file"' in (content or "")
            has_api_calls = bool(api_tool_calls)
            logger.info(
                f"[TOOL_CALL_DIAG] 未解析到任何工具调用（可能由本地意图兜底接管），api_tool_calls={has_api_calls}, "
                f"content中有'tool'={has_tool_keyword}, content中有'write_file'={has_write_file}, "
                f"content前200字符={str(content)[:200] if content else 'EMPTY'}"
            )
        return text_parsed

    def _run_tool_batch(self, tool_calls_list: list, user_message: str, intent: TaskIntent = None):
        """执行一批工具，必要时 write→execute 自动衔接。返回 (tool_results, auto_finalize)"""
        if intent is None:
            intent = parse_task_intent(user_message)
        tool_results = []
        auto_finalize = None

        def _execute_one(tc: dict, batch_size: int):
            nonlocal auto_finalize
            tool_name = tc.get("tool", "")
            tool_input = tc.get("input", "")
            gen = self._execute_tool_steps(tool_name, tool_input, user_message)
            try:
                while True:
                    event = next(gen)
                    yield event
            except StopIteration as stop:
                tool_result = stop.value
            intent.record_tool(tool_name, tool_input, tool_result)
            tool_results.append(f"工具 {tool_name} 的返回结果:\n{tool_result}")
            if should_auto_finalize(tool_name, tool_result, intent, batch_size):
                auto_finalize = (tool_name, tc, tool_result)

        for tc in tool_calls_list:
            yield from _execute_one(tc, len(tool_calls_list))

        if auto_finalize:
            return tool_results, auto_finalize

        for tc in build_auto_continue_calls(intent):
            yield {
                "type": "thought",
                "output": f"用户要求运行验证，正在本地执行 {tc.get('input', {}).get('file_path', '脚本')}…",
            }
            yield from _execute_one(tc, 1)

        return tool_results, auto_finalize

    def _call_api_llm(
        self,
        messages,
        model_name,
        temperature,
        max_tokens,
        custom_base_url,
        custom_api_key,
        openai_tools,
        use_tools_api: bool,
        stop_event: Optional[threading.Event] = None,
    ):
        """调用 API：优先 Function Calling，不支持则回退普通 chat（所有 OpenAI 兼容 API 通用）"""
        if use_tools_api and openai_tools:
            try:
                return chat_completion_with_tools(
                    messages=messages,
                    tools=openai_tools,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    custom_base_url=custom_base_url,
                    custom_api_key=custom_api_key,
                    stop_event=stop_event,
                ), True
            except ToolCallingNotSupportedError:
                logger.info("API 不支持 Function Calling，回退到普通 chat")
            except (requests.ConnectionError, requests.Timeout) as e:
                raise AgentNetworkError(f"网络连接失败: {e}") from e
            except requests.HTTPError as e:
                raise AgentAPIError(f"API 返回错误 ({e.response.status_code if e.response else '?'}): {e}") from e

        try:
            content = chat_completion(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                custom_base_url=custom_base_url,
                custom_api_key=custom_api_key,
                app_session_id=None,
                stop_event=stop_event,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            raise AgentNetworkError(f"网络连接失败: {e}") from e
        except requests.HTTPError as e:
            raise AgentAPIError(f"API 返回错误: {e}") from e

        return {
            "content": content or "",
            "tool_calls": [],
            "message": {"role": "assistant", "content": content or ""},
        }, False

    def _append_tool_followup_messages(self, messages, tool_results, user_message, assistant_content="", is_pre_loop: bool = False):
        body = "\n\n".join(tool_results)
        if assistant_content.strip():
            messages.append({"role": "assistant", "content": assistant_content.strip()})

        intent = parse_task_intent(user_message)
        is_multi_step = intent.is_multi_step
        needs_run = intent.needs_run

        # 检查已执行的工具结果中是否已包含 execute_code
        has_execute_result = any('execute_code' in r for r in tool_results)
        has_write_result = any('write_file' in r for r in tool_results)

        summary_hint = followup_after_execute(intent, has_execute_result)

        # 根据用户意图判断后续指令
        if is_pre_loop and _looks_like_fix_or_modify_request(user_message):
            # 预执行 read_file 后用户意图是修改文件 → 要求模型继续调用工具完成修改
            followup = (
                f"{body}\n\n用户原始问题：{user_message}\n"
                "请根据以上文件内容，**调用 write_file 工具**完成用户的修改请求。"
                "必须实际调用工具写入文件，不要只描述你要做什么。"
            )
        elif summary_hint and not is_pre_loop:
            followup = f"{body}\n\n用户原始问题：{user_message}\n{summary_hint}"
        elif is_multi_step and has_write_result and needs_run and not has_execute_result:
            # 多步任务：已写文件但还需要运行
            followup = (
                f"{body}\n\n用户原始问题：{user_message}\n"
                "文件已写入成功。用户还要求运行代码并给出结果。"
                "请立即调用 execute_code 工具运行刚写入的文件，然后总结运行结果。"
            )
        elif is_pre_loop:
            # 预执行后的首次对话 → 要求模型基于工具结果继续
            followup = (
                f"{body}\n\n用户原始问题：{user_message}\n"
                "请根据以上工具执行结果回复用户。如果还需要调用其他工具，请继续调用。"
            )
        elif is_multi_step and needs_run and not has_execute_result:
            # ReAct 循环中，多步任务还没执行过 execute_code
            followup = (
                f"{body}\n\n用户原始问题：{user_message}\n"
                "请根据以上工具执行结果判断：任务是否已完成？\n"
                "注意：用户要求运行代码并给出结果，请调用 execute_code 工具运行代码。\n"
                "如果已经运行完毕，用中文总结运行结果。"
            )
        else:
            # ReAct 循环中的工具结果 → 要求模型判断是否还需要更多工具
            followup = (
                f"{body}\n\n用户原始问题：{user_message}\n"
                "请根据以上工具执行结果判断：任务是否已完成？如果是，用中文总结；"
                "如果还需要调用其他工具，请继续调用。"
            )

        messages.append({"role": "user", "content": followup})

    def _run_api_tool_iter(
        self,
        user_message: str,
        model_display: str,
        history: list = None,
        max_steps: int = None,
        app_session_id: str = None,
        status_callback=None,
        workspace_path: str = None,
        stop_event: Optional[threading.Event] = None,
    ) -> Generator:
        """所有 API 模型统一 Agent：本地执行工具 + 自然语言回复（不依赖某一家的 Function Calling）"""
        if max_steps is None:
            max_steps = self._config.get("max_steps", 10)

        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else "mimo-v2.5"
        system_prompt = _build_system_prompt(self._tools, workspace_path, api_mode=True)

        custom_base_url = None
        custom_api_key = None
        if model_info and model_info.get("is_custom"):
            custom_base_url = model_info.get("base_url")
            custom_api_key = model_info.get("api_key")

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # ── 预处理：在 LLM 调用前自动搜集相关上下文 ──
        pre_context = _pre_agent_context(user_message, workspace_path)
        if pre_context and len(pre_context) > 20:
            # 将搜集到的文件/代码上下文注入到用户消息尾部
            messages[-1]["content"] = user_message + pre_context
            logger.info(
                "[PRE_CONTEXT] 预处理上下文已注入，%d 字符",
                len(pre_context),
            )

        llm_params = self._config.get("llm_params", {})
        temperature = llm_params.get("temperature", 0.3)
        max_tokens = llm_params.get("max_tokens", 4096)
        openai_tools = build_openai_tools_schema(self._tools)
        use_tools_api = True
        intent = parse_task_intent(user_message)

        # 用户意图明确时，可先本地执行（不依赖模型输出格式）
        direct = _build_direct_tool_calls(user_message, workspace_path, history=history)
        if direct:
            batch = self._run_tool_batch(direct, user_message, intent)
            try:
                while True:
                    yield next(batch)
            except StopIteration as stop:
                tool_results, auto_finalize = stop.value
            if auto_finalize:
                tn, tc, tr = auto_finalize
                yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                return
            self._append_tool_followup_messages(messages, tool_results, user_message, is_pre_loop=True)

        # 仅在第一轮（工具尚未执行过）允许基于用户原始消息的意图兜底
        fallback_allowed = not bool(direct)
        write_file_called = False  # 追踪是否已实际调用过 write_file 或 edit_file
        is_run_request = _looks_like_run_request(user_message)  # "运行"请求不需要 write_file

        for _ in range(max_steps):
            if stop_event is not None and stop_event.is_set():
                yield {"type": "error", "output": "已停止"}
                return
            yield {"type": "thinking", "output": "正在处理中..."}

            # --- 流式调用 LLM（支持 stream=True + tools，实时输出到 UI）---
            has_streamed_content = False
            full_content = ""
            api_tool_calls = []
            used_fc = use_tools_api

            if use_tools_api and openai_tools:
                try:
                    for chunk in chat_completion_with_tools_stream(
                        messages=messages, tools=openai_tools, model=model_name,
                        temperature=temperature, max_tokens=max_tokens,
                        custom_base_url=custom_base_url, custom_api_key=custom_api_key,
                        stop_event=stop_event,
                    ):
                        if chunk.get("done"):
                            full_content = chunk.get("content", full_content)
                            api_tool_calls = chunk.get("tool_calls", [])
                        else:
                            text = chunk.get("content", "")
                            if text:
                                has_streamed_content = True
                                full_content += text
                                yield {"type": "result_chunk", "output": text}
                except ToolCallingNotSupportedError:
                    use_tools_api = False
                    used_fc = False
                    has_streamed_content = False
                    full_content = ""
                    # 模型不支持 Function Calling，切换到文本 JSON 格式系统提示词
                    # 确保模型知道通过文本输出 JSON 来调用工具
                    text_json_prompt = _build_system_prompt(self._tools, workspace_path, api_mode=False)
                    messages[0] = {"role": "system", "content": text_json_prompt}
                    logger.info(
                        "[TOOL_CALL_DIAG] Function Calling 不支持，已切换到文本 JSON 格式，"
                        "新提示词前60字符=%s",
                        text_json_prompt[:60],
                    )
                except AgentNetworkError as e:
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"网络错误: {e}"}
                    return
                except AgentAPIError as e:
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"API 错误: {e}"}
                    return
                except Exception as e:
                    logger.error("LLM 流式调用失败", exc_info=True)
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                    return

            if not has_streamed_content and not api_tool_calls:
                # 非 FC 模式：使用普通流式调用
                try:
                    for chunk in chat_completion_stream(
                        messages=messages, model=model_name,
                        temperature=temperature, max_tokens=max_tokens,
                        custom_base_url=custom_base_url, custom_api_key=custom_api_key,
                    ):
                        has_streamed_content = True
                        full_content += chunk
                        yield {"type": "result_chunk", "output": chunk}
                except AgentNetworkError as e:
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"网络错误: {e}"}
                    return
                except AgentAPIError as e:
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"API 错误: {e}"}
                    return
                except Exception as e:
                    logger.error("LLM 流式调用失败", exc_info=True)
                    if has_streamed_content:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                    return

            if use_tools_api and not used_fc:
                use_tools_api = False

            content = full_content

            # 提取模型思考内容（thinking/reasoning 标签内的推理过程）
            _thinking = _extract_thinking_content(content)
            if _thinking:
                yield {"type": "thought", "output": _thinking}
            # 过滤掉思考标签，避免泄漏到最终输出
            content = _strip_thinking_tokens(content)

            tool_calls_list = self._collect_tool_calls_from_response(content, api_tool_calls, user_message)

            # 模型用自然语言 + 工具：有 tool_calls 时把说明放在思考区
            if content.strip() and tool_calls_list and not _looks_like_tool_call(content.strip()):
                yield {"type": "thought", "output": content.strip()}

            if tool_calls_list:
                logger.info(
                    f"[TOOL_CALL_DIAG] 准备执行 {len(tool_calls_list)} 个工具调用: "
                    f"{[(tc.get('tool'), type(tc.get('input')).__name__) for tc in tool_calls_list]}"
                )
                # 清除已流式输出的内容（因为要执行工具，不是最终回复）
                if has_streamed_content:
                    yield {"type": "result_clear"}
                fallback_allowed = False  # 模型已经自己调工具了，不再需要意图兜底
                # 检查是否调用了 write_file
                if any(tc.get("tool") in ("write_file", "edit_file") for tc in tool_calls_list):
                    write_file_called = True
                batch = self._run_tool_batch(tool_calls_list, user_message, intent)
                try:
                    while True:
                        yield next(batch)
                except StopIteration as stop:
                    tool_results, auto_finalize = stop.value

                if auto_finalize:
                    tn, tc, tr = auto_finalize
                    yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                    return

                self._append_tool_followup_messages(
                    messages, tool_results, user_message,
                    assistant_content="" if _looks_like_tool_call(content) else content,
                )
                continue

            # 无工具调用：兜底逻辑
            # - _looks_like_tool_call(content)：模型输出看起来像工具 JSON 但解析失败 → 可重试
            # - fallback_allowed：第一轮未执行工具时，若用户原始消息明显需要工具 → 本地兜底
            should_fallback = _looks_like_tool_call(content) or (
                fallback_allowed and _user_requires_tool_first(user_message)
            )
            if should_fallback:
                if has_streamed_content:
                    yield {"type": "result_clear"}
                fallback_allowed = False  # 兜底只执行一次，防止死循环
                fallback = _build_direct_tool_calls(user_message, workspace_path, history=history)
                if fallback:
                    if any(tc.get("tool") in ("write_file", "edit_file") for tc in fallback):
                        write_file_called = True
                    batch = self._run_tool_batch(fallback, user_message, intent)
                    try:
                        while True:
                            yield next(batch)
                    except StopIteration as stop:
                        tool_results, auto_finalize = stop.value
                    if auto_finalize:
                        tn, tc, tr = auto_finalize
                        yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                        return
                    self._append_tool_followup_messages(messages, tool_results, user_message, content)
                    continue
                else:
                    # fallback 也构造不出工具调用（通常因为 write_file 需要模型的代码内容），
                    # 要求模型重新使用 Function Calling 正确调用工具，不要穿透到最终回复
                    if _looks_like_tool_call(content):
                        logger.warning(
                            "[TOOL_CALL_DIAG] 模型输出看起来像工具调用但 JSON 解析失败，"
                            "且 _build_direct_tool_calls 无法构造 write_file，要求模型重试"
                        )
                        yield {"type": "thought", "output": "工具调用格式解析失败，正在要求模型重新调用…"}
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                "你上一条回复中的工具调用 JSON 格式有问题，无法解析。"
                                "请**必须使用 Function Calling / tool_calls** 方式调用 write_file 工具，"
                                "而不要把 JSON 写在文本内容中。如果 content 参数中包含代码，"
                                "确保 JSON 中 content 字段的字符串被正确转义（换行符用 \\n，引号用 \\\"）。"
                                "请立即重新调用 write_file 工具。"
                            ),
                        })
                        continue

            # 【反幻觉】模型声称写入了文件但实际未调用 write_file
            # 跳过条件：用户请求是"运行/执行"（不需要 write_file）
            if not is_run_request and _looks_like_false_file_completion(content, write_file_called):
                if has_streamed_content:
                    yield {"type": "result_clear"}
                logger.warning("检测到模型幻觉：声称写入文件但未调用 write_file，强制重试")
                yield {"type": "thought", "output": "检测到模型未实际调用工具，正在要求模型执行写入…"}
                messages.append({"role": "assistant", "content": content})
                if use_tools_api:
                    retry_prompt = (
                        "你没有实际调用 write_file 工具！你的上一条回复只是描述了代码，"
                        "但文件并未被修改。请立即通过 Function Calling 调用 write_file 工具将代码写入文件。"
                        "必须返回 Function Call，不能只输出代码文本。"
                    )
                else:
                    retry_prompt = (
                        "你没有实际调用 write_file 工具！你的上一条回复只是描述了代码，"
                        "但文件并未被修改。请**立即**输出一行纯 JSON 来调用 write_file 工具：\n\n"
                        '{"tool":"write_file","input":{"file_path":"文件路径","content":"完整代码内容"}}\n\n'
                        "- **不要**用 markdown 代码块包裹\n"
                        "- **不要**在 JSON 前后添加任何解释文字\n"
                        "- JSON 必须在一行内，代码中的换行用 \\n，引号用 \\\"\n"
                        "- content 中必须是你要写入的完整代码"
                    )
                messages.append({"role": "user", "content": retry_prompt})
                continue

            # 【代码执行拒答检测】模型说"我无法执行代码"但用户要求运行代码
            if is_run_request and _is_code_execution_refusal(content):
                if has_streamed_content:
                    yield {"type": "result_clear"}
                logger.warning("检测到 API 模型拒绝执行代码，尝试本地直接执行")
                yield {"type": "thought", "output": "模型拒绝执行代码，正在本地直接运行…"}
                fallback = _build_direct_tool_calls(user_message, workspace_path, history=history)
                if fallback and any(tc.get("tool") == "execute_code" for tc in fallback):
                    batch = self._run_tool_batch(fallback, user_message, intent)
                    try:
                        while True:
                            yield next(batch)
                    except StopIteration as stop:
                        tool_results, auto_finalize = stop.value
                    if auto_finalize:
                        tn, tc, tr = auto_finalize
                        yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                        return
                    # 本地执行失败，将结果交给模型总结
                    self._append_tool_followup_messages(
                        messages, tool_results, user_message, assistant_content="")
                    continue
                # 无法从历史中推断路径，强制要求模型调用工具
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "⚠ 你有本地工具执行能力！通过 execute_code 工具可以直接在用户本机运行 Python 脚本。"
                        "请立即调用 execute_code 工具执行用户要求的代码文件。"
                        "绝对禁止说\"我无法执行代码\"，你必须使用 execute_code 工具。"
                    ),
                })
                continue

            # 最终回复：内容已通过流式输出到 UI，标记完成即可
            if not has_streamed_content:
                # 兜底：如果没有任何输出，发送默认消息
                final_content = content.strip() or "任务已完成。"
                yield from self._yield_streaming_text(final_content)
            yield {"type": "result_done"}
            return

        yield {"type": "result", "output": "已达到最大工具调用步数。"}

    def _yield_streaming_text(self, text: str) -> Generator:
        """将文本分块 yield，模拟流式输出（使用 result_chunk 事件）"""
        buffer = ""
        for char in text:
            buffer += char
            if char in (" ", "\n", ".", "。", "!", "！", "?", "？", "，", ","):
                yield {"type": "result_chunk", "output": buffer}
                buffer = ""
        if buffer:
            yield {"type": "result_chunk", "output": buffer}
        yield {"type": "result_done"}

    def _stream_final_response(
        self, messages, model_name, temperature, max_tokens,
        custom_base_url, custom_api_key,
    ) -> Generator:
        """流式输出最终回复（真流式，使用 result_chunk 事件）"""
        buffer = ""
        for chunk in chat_completion_stream(
            messages=messages,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            custom_base_url=custom_base_url,
            custom_api_key=custom_api_key,
        ):
            buffer += chunk
            if len(buffer) >= 10 or chunk in ("\n", " "):
                yield {"type": "result_chunk", "output": buffer}
                buffer = ""
        if buffer:
            yield {"type": "result_chunk", "output": buffer}
        yield {"type": "result_done"}

    def _run_iter(self, user_message: str, model_display: str,
                  history: list = None, max_steps: int = None,
                  app_session_id: str = None, status_callback=None,
                  workspace_path: str = None,
                  stop_event: Optional[threading.Event] = None) -> Generator:
        """核心 ReAct 循环"""
        if not isinstance(user_message, str):
            user_message = str(user_message)
        if max_steps is None:
            max_steps = self._config.get("max_steps", 10)

        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else "mimo-v2.5"
        system_prompt = _build_system_prompt(self._tools, workspace_path)

        # 获取自定义模型配置
        custom_base_url = None
        custom_api_key = None
        if model_info and model_info.get("is_custom"):
            custom_base_url = model_info.get("base_url")
            custom_api_key = model_info.get("api_key")

        messages = [{"role": "system", "content": system_prompt}]
        # 注入历史对话
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        llm_params = self._config.get("llm_params", {})
        temperature = llm_params.get("temperature", 0.3)
        max_tokens = llm_params.get("max_tokens", 4096)

        if is_api_agent_model(model_display):
            yield from self._run_api_tool_iter(
                user_message, model_display, history, max_steps, app_session_id,
                status_callback=status_callback, workspace_path=workspace_path,
                stop_event=stop_event,
            )
            return

        # 对浏览器模型：让模型先跑一轮，不再预执行工具。
        # 但"运行"请求例外：直接预执行 execute_code，避免模型选择 read_file 而不执行。
        # ReAct 循环中的 fallback 逻辑会处理模型未输出工具调用的情况。
        # 仅在第一轮未执行工具时允许基于用户原始消息的意图兜底，防止死循环。
        is_run_request = _looks_like_run_request(user_message)  # "运行"请求不需要 write_file
        if is_run_request:
            direct = _build_direct_tool_calls(user_message, workspace_path, history=history)
            if direct and any(tc.get("tool") == "execute_code" for tc in direct):
                yield from self._run_preemptive_browser_tools(
                    user_message, model_display, system_prompt, model_name,
                    temperature, max_tokens, direct=direct,
                    custom_base_url=custom_base_url, custom_api_key=custom_api_key,
                    app_session_id=app_session_id, status_callback=status_callback,
                    workspace_path=workspace_path,
                )
                return

        fallback_allowed = True
        write_file_called = False  # 追踪是否已实际调用过 write_file 或 edit_file
        task_intent = parse_task_intent(user_message)
        is_run_request = _looks_like_run_request(user_message)

        # 判断是否为浏览器模型（支持流式输出）
        _is_browser_model = False
        try:
            from services.core.api_service import is_browser_agent_model
            _is_browser_model = is_browser_agent_model(model_display)
        except ImportError:
            pass

        for _ in range(max_steps):
            if stop_event is not None and stop_event.is_set():
                yield {"type": "error", "output": "已停止"}
                return
            yield {"type": "thinking", "output": "正在处理中..."}

            # ── 浏览器模型：使用流式输出，边生成边显示 ──
            if _is_browser_model:
                try:
                    from services.core.api_service import chat_completion_stream
                    response = ''
                    for chunk in chat_completion_stream(
                        messages=messages, model=model_name,
                        temperature=temperature, max_tokens=max_tokens,
                        custom_base_url=custom_base_url,
                        custom_api_key=custom_api_key,
                        app_session_id=app_session_id,
                        status_callback=status_callback,
                    ):
                        if stop_event is not None and stop_event.is_set():
                            if response:
                                yield {"type": "result_clear"}
                            yield {"type": "error", "output": "已停止"}
                            return
                        response += chunk
                        # 流式输出部分内容到 UI
                        yield {"type": "result_chunk", "output": chunk}
                except AgentNetworkError as e:
                    if response:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"网络错误: {e}"}
                    return
                except AgentAPIError as e:
                    if response:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"API 错误: {e}"}
                    return
                except Exception as e:
                    logger.error("LLM 流式调用失败", exc_info=True)
                    if response:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                    return
            else:
                try:
                    response = chat_completion(messages=messages, model=model_name,
                                               temperature=temperature, max_tokens=max_tokens,
                                               custom_base_url=custom_base_url,
                                               custom_api_key=custom_api_key,
                                               app_session_id=app_session_id,
                                               status_callback=status_callback,
                                               stop_event=stop_event)
                except AgentNetworkError as e:
                    yield {"type": "error", "output": f"网络错误: {e}"}
                    return
                except AgentAPIError as e:
                    yield {"type": "error", "output": f"API 错误: {e}"}
                    return
                except Exception as e:
                    logger.error("LLM 调用失败", exc_info=True)
                    yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                    return

            # 提取模型思考内容并发送到 UI，然后过滤掉
            _thinking = _extract_thinking_content(response)
            if _thinking:
                yield {"type": "thought", "output": _thinking}
            # 过滤思考模型的内部分析内容（thinking/reasoning 标签）
            response = _strip_thinking_tokens(response)

            if not response:
                if not _is_browser_model:
                    try:
                        from services.core.api_service import is_browser_agent_model
                        browser_skip_retry = is_browser_agent_model(model_display)
                    except ImportError:
                        browser_skip_retry = False
                    if not browser_skip_retry:
                        try:
                            response = chat_completion(messages=messages, model=model_name,
                                                       temperature=temperature, max_tokens=max_tokens,
                                                       custom_base_url=custom_base_url,
                                                       custom_api_key=custom_api_key,
                                                       app_session_id=app_session_id,
                                                       status_callback=status_callback,
                                                       stop_event=stop_event)
                        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                            pass
                if not response:
                    # 浏览器模型流式已输出部分内容，清除后报错
                    if _is_browser_model:
                        yield {"type": "result_clear"}
                    yield {"type": "error", "output": f"LLM 返回空响应。模型: {model_name}，请检查模型名称是否正确。"}
                    return

            # 重试路径也提取思考内容
            _thinking_retry = _extract_thinking_content(response)
            if _thinking_retry:
                yield {"type": "thought", "output": _thinking_retry}
            # 过滤思考模型的内部分析内容（thinking/reasoning 标签），
            # 重试路径的 response 也要过滤
            response = _strip_thinking_tokens(response)

            # 解析所有工具调用
            all_tool_calls = _parse_tool_calls(response, user_message)
            
            if not all_tool_calls:
                # 单个工具调用解析
                tool_call = _parse_tool_call(response, user_message)
                if tool_call:
                    all_tool_calls = [tool_call]

            if not all_tool_calls and fallback_allowed:
                direct = _try_direct_tool_fallbacks(user_message, response, model_display, workspace_path, history=history)
                if direct:
                    all_tool_calls = direct
                    fallback_allowed = False  # 兜底只执行一次，防止死循环
                    # 浏览器模型：清除流式输出的内容（因为要执行工具）
                    if _is_browser_model:
                        yield {"type": "result_clear"}
                    yield {
                        "type": "thought",
                        "output": "模型未输出工具 JSON，已根据用户指令直接调用本地工具。",
                    }
            
            # 如果解析失败但响应看起来包含工具调用，重试一次
            if not all_tool_calls and _looks_like_tool_call(response):
                wf = _extract_write_file_input(
                    response, _infer_file_path_from_user_message(user_message))
                if wf:
                    wf["input"]["content"] = _fix_python_boilerplate(wf["input"]["content"])
                    all_tool_calls = [wf]
                else:
                    yield {"type": "thought", "output": "工具调用解析失败，正在重试..."}
                    retry_messages = messages + [
                        {"role": "assistant", "content": response},
                        {"role": "user", "content": (
                            "【⚠ 格式错误 — 必须只输出 JSON】"
                            "你的回复不是合法的 JSON 工具调用，已被本地程序忽略。\n"
                            "现在你必须只输出一行纯 JSON，不得有任何其他内容（无 markdown、无解释、无 emoji）：\n"
                            '{"tool":"write_file","input":{"file_path":"D:/path/file.py","content":"完整代码"}}'
                        )},
                    ]
                    try:
                        retry_response = chat_completion(messages=retry_messages, model=model_name,
                                                         temperature=max(temperature - 0.2, 0.0),
                                                         max_tokens=max_tokens,
                                                         custom_base_url=custom_base_url,
                                                         custom_api_key=custom_api_key,
                                                         app_session_id=app_session_id,
                                                         status_callback=status_callback)
                        retry_calls = _parse_tool_calls(retry_response, user_message) if retry_response else []
                        if retry_calls:
                            all_tool_calls = retry_calls
                            response = retry_response
                    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                        logger.warning("重试请求失败，使用原始响应")

            if not all_tool_calls and _looks_like_tool_call(response):
                yield {
                    "type": "error",
                    "output": "工具调用格式有误，未能创建文件。请重试，或切换到 API 模型。",
                }
                return

            if all_tool_calls:
                fallback_allowed = False  # 工具已执行，不再需要意图兜底
                # 浏览器模型：清除流式输出的内容（因为要执行工具，不是最终回复）
                if _is_browser_model:
                    yield {"type": "result_clear"}
                # 提取思考内容
                thought = response.split("{")[0].strip() if "{" in response else ""
                if thought:
                    yield {"type": "thought", "output": thought}

                if any(tc.get("tool") in ("write_file", "edit_file") for tc in all_tool_calls):
                    write_file_called = True

                batch = self._run_tool_batch(all_tool_calls, user_message, task_intent)
                try:
                    while True:
                        yield next(batch)
                except StopIteration as stop:
                    tool_results, auto_finalize = stop.value

                if auto_finalize:
                    tn, tc, tr = auto_finalize
                    yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                    return

                # 「运行已有文件」：模型只 read_file 时，本地追加 execute_code
                if is_run_request:
                    called_tools = {tc.get("tool", "") for tc in all_tool_calls}
                    has_execute_in_results = any("execute_code" in r for r in tool_results)
                    if "execute_code" not in called_tools and not has_execute_in_results and "read_file" in called_tools:
                        file_path = None
                        for tc in all_tool_calls:
                            if tc.get("tool") == "read_file":
                                inp = tc.get("input", {})
                                file_path = inp if isinstance(inp, str) else inp.get("file_path", "")
                                break
                        if file_path:
                            execute_call = {"tool": "execute_code", "input": {"file_path": file_path}}
                            cont_batch = self._run_tool_batch([execute_call], user_message, task_intent)
                            try:
                                while True:
                                    yield next(cont_batch)
                            except StopIteration as stop2:
                                cont_results, cont_finalize = stop2.value
                            tool_results.extend(cont_results)
                            if cont_finalize:
                                tn, tc, tr = cont_finalize
                                yield {"type": "result", "output": _auto_finalize_message(tn, tc, tr)}
                                return

                # 将所有工具结果添加到消息中
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": _build_tool_followup_user_message(tool_results, user_message),
                })
            else:
                stripped = (response or "").strip()
                looks_incomplete = (
                    stripped.endswith("...") or
                    stripped.endswith("…") or
                    "💭" in response
                )
                if looks_incomplete:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "请继续执行任务，调用所需的工具来完成分析。如果任务已完成，请输出最终总结。",
                    })
                    continue
                # fallback_allowed 为 True 说明工具尚未被调用过，此时才强制要求工具调用。
                # 若工具已经执行过（fallback_allowed=False），模型给中文总结是合法的最终回复。
                if fallback_allowed and _user_requires_tool_first(user_message):
                    messages.append({"role": "assistant", "content": response})
                    # 检查是否为拒绝性回复，用更强硬的提示
                    is_refusal = _is_browser_model_refusal(response)
                    retry_prompt = (
                        "【⚠ 强制工具调用 — 你刚才的回复无效】\n"
                        "你没有输出正确的 JSON 工具调用。你的回复会被忽略。\n"
                        "现在你必须只输出一行纯 JSON："
                        '{"tool":"read_file","input":{"file_path":"D:/path/file.py"}} 或 '
                        '{"tool":"scan_project","input":{"dir_path":"D:/path/project"}}\n'
                        "路径使用用户消息中的原始路径。不要输出任何其他文字、emoji、解释或确认。"
                    ) if is_refusal else (
                        "你尚未调用本地工具，不要猜测文件或项目内容。"
                        "请先输出一行 JSON 调用 read_file、list_directory 或 scan_project。"
                    )
                    messages.append({"role": "user", "content": retry_prompt})
                    continue
                if _messages_pending_code_fix(messages):
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【⚠ 代码未修正 — 必须只输出 JSON】"
                            "你尚未调用 write_file 修正代码。请只输出一行 write_file JSON，"
                            "content 为完整修正后的代码。禁止 markdown、禁止解释。"
                        ),
                    })
                    continue
                # 【反幻觉】模型声称写入了文件但实际未调用工具
                # 跳过条件：用户请求是"运行/执行"（不需要 write_file），
                # 或者已经实际调用过 write_file
                if not is_run_request and not write_file_called and _looks_like_false_file_completion(response, write_file_called):
                    logger.warning("检测到浏览器模型幻觉：声称写入文件但未调用工具，强制要求调用")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "⚠ 你没有实际调用 write_file 工具！你的回复只是描述了代码，"
                            "但文件并未被修改。请只输出一行 write_file JSON：\n"
                            '{"tool":"write_file","input":{"file_path":"路径","content":"完整代码"}}'
                        ),
                    })
                    continue
                # 浏览器模型：内容已流式输出，只需标记完成
                if _is_browser_model:
                    yield {"type": "result_done"}
                else:
                    yield {"type": "result", "output": response}
                return

        # 达到最大轮次
        if _is_browser_model and response:
            # 如果最后一步是工具调用（内容已清除），用 result 发送总结
            yield {"type": "result", "output": "达到最大推理轮次"}
        else:
            yield {"type": "result", "output": response if response else "达到最大推理轮次"}

    def get_tools_info(self) -> list:
        return [{"name": t.name, "description": t.description} for t in self._tools]
