"""
Agent 任务结束策略 — 控制 auto_finalize 与 write→execute 自动衔接。

避免「生成脚本并运行验证」类复合任务在 write_file 成功后过早结束循环。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

TOOL_SUCCESS_PREFIX = "成功:"
CODE_ERROR_MARKERS = ("⚠ 语法错误:", "⚠ 编译错误:", "❌ 执行失败")

_WRITE_KW = ("生成", "创建", "写入", "实现", "编写", "写一个", "写个", "制作", "新建")
_SUMMARY_KW = ("给出结果", "输出结果", "返回结果", "总结", "告诉我结果", "展示结果", "验证并")


def tool_result_success(tool_result: str) -> bool:
    res = str(tool_result or "")
    if not res.startswith(TOOL_SUCCESS_PREFIX):
        return False
    return not any(m in res for m in CODE_ERROR_MARKERS)


def _extract_file_path(tool_input) -> str:
    if isinstance(tool_input, str):
        return tool_input.strip()
    if isinstance(tool_input, dict):
        return str(
            tool_input.get("file_path")
            or tool_input.get("path")
            or ""
        ).strip()
    return ""


def looks_like_run_request(user_message: str) -> bool:
    if not user_message:
        return False
    msg = user_message.lower()
    run_kw = (
        "运行", "执行", "跑一下", "跑一遍", "跑起来", "测试一下", "测试用例",
        "输出结果", "生成结果", "看看结果", "run", "execute", "test it",
        "运行看看", "执行一下", "跑个结果", "直接运行出结果", "验证",
    )
    if not any(k in msg for k in run_kw):
        return False
    if re.search(r"[A-Za-z]:[/\\].+\.py", user_message, re.I) or ".py" in msg:
        return True
    if any(k in msg for k in ("代码", "这段", "这个脚本", "这个程序", "脚本", "the code", "this code", "this script")):
        return True
    return False


def looks_like_needs_write(user_message: str) -> bool:
    if not user_message:
        return False
    return any(k in user_message for k in _WRITE_KW)


def looks_like_needs_summary(user_message: str) -> bool:
    if not user_message:
        return False
    msg = user_message.lower()
    return any(k in msg for k in _SUMMARY_KW)


def looks_like_multiple_tasks(user_message: str) -> bool:
    """检测用户消息是否包含多个独立步骤。"""
    if not user_message:
        return False
    msg = user_message.strip()
    if re.search(r"(?:^|\n)\s*\d+\s*[\.．、\)]\s*.{4,}", msg):
        return True
    if re.search(r"[第一二三四五六七八九十][一二三四五六七八九十\d]?[、．\.:：]", msg):
        return True
    if re.search(r"[,，;；]\s*(?:并且|同时|另外|此外|然后|接着|之后|还有|以及)\s*.{4,}", msg):
        return True
    parts = [p for p in re.split(r"[;；]", msg) if p.strip() and len(p.strip()) > 8]
    if len(parts) >= 3:
        return True
    compound_patterns = [
        r"(?:生成|创建|写入|实现|编写|写一个|写段|制作)\s*.{2,}?\s*并\s*(?:运行|执行|测试|验证)",
        r"(?:修改|更新|修复)\s*.{2,}?\s*并\s*(?:运行|执行|测试|验证)",
        r"(?:写|创建|生成)\s*.{2,}?\s*[，,然后接着]\s*(?:运行|执行|跑|测试)",
        r"(?:运行|执行|测试)\s*.{2,}?\s*并\s*(?:给出|输出|返回|查看)\s*(?:结果|输出)",
    ]
    for pat in compound_patterns:
        if re.search(pat, msg):
            return True
    return False


def looks_like_generate_and_run(user_message: str) -> bool:
    return looks_like_needs_write(user_message) and looks_like_run_request(user_message)


@dataclass
class TaskIntent:
    """单次 Agent 回合内的任务意图与工具执行状态。"""

    needs_write: bool = False
    needs_run: bool = False
    needs_summary: bool = False
    is_multi_step: bool = False
    written_files: set = field(default_factory=set)
    executed_files: set = field(default_factory=set)

    def record_tool(self, tool_name: str, tool_input, tool_result: str) -> None:
        if not tool_result_success(tool_result):
            return
        path = _extract_file_path(tool_input)
        if tool_name == "write_file" and path:
            self.written_files.add(path.replace("\\", "/"))
        elif tool_name == "execute_code" and path:
            self.executed_files.add(path.replace("\\", "/"))

    def pending_execute_paths(self) -> list[str]:
        pending = self.written_files - self.executed_files
        return sorted(
            p for p in pending
            if p.lower().endswith(".py")
        )


def parse_task_intent(user_message: str) -> TaskIntent:
    msg = user_message or ""
    needs_write = looks_like_needs_write(msg)
    needs_run = looks_like_run_request(msg)
    needs_summary = looks_like_needs_summary(msg)
    is_multi_step = (
        looks_like_multiple_tasks(msg)
        or looks_like_generate_and_run(msg)
        or (needs_write and needs_run)
    )
    return TaskIntent(
        needs_write=needs_write,
        needs_run=needs_run,
        needs_summary=needs_summary,
        is_multi_step=is_multi_step,
    )


def should_auto_finalize(
    tool_name: str,
    tool_result: str,
    intent: TaskIntent,
    tool_calls_in_batch: int,
) -> bool:
    """
    判断是否应在当前工具成功后直接结束 Agent 循环。

    - delete_file 单步成功 → 可结束
    - write_file 成功 → 仅当不需要运行且非多步任务
    - execute_code 成功 → 仅当纯运行任务，或不需要文字总结
    """
    if tool_calls_in_batch != 1:
        return False
    if not tool_result_success(tool_result):
        return False

    if tool_name == "delete_file":
        return not intent.is_multi_step

    if tool_name == "write_file":
        if intent.needs_run or intent.is_multi_step:
            return False
        return True

    if tool_name == "execute_code":
        if intent.needs_summary:
            return False
        if intent.needs_write and intent.is_multi_step:
            return False
        return intent.needs_run

    return False


def build_auto_continue_calls(intent: TaskIntent) -> list[dict]:
    """write_file 成功后，若用户要求运行，本地自动追加 execute_code。"""
    if not intent.needs_run:
        return []
    calls = []
    for path in intent.pending_execute_paths():
        calls.append({"tool": "execute_code", "input": {"file_path": path}})
    return calls


def followup_after_execute(intent: TaskIntent, has_execute_result: bool) -> Optional[str]:
    """execute 完成后若仍需总结，返回额外 follow-up 提示。"""
    if has_execute_result and intent.needs_summary:
        return (
            "代码已运行完毕。请根据以上 execute_code 的输出，用中文向用户总结运行结果。"
            "不要再调用工具。"
        )
    return None
