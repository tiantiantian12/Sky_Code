"""
终端输出读取工具集
为 Agent 提供读取终端输出的能力：
  - get_terminal_output  : 获取最近的终端输出
  - clear_terminal       : 清除终端输出
  - get_command_history  : 获取命令执行历史

设计理念：
  TerminalWidget 维护一个输出缓冲区，工具通过回调读取。
  这样 Agent 在执行命令后可以主动查看终端输出，
  而不是只能依赖 run_command 工具的返回值。
"""

import os
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ── 终端输出管理器（单例）─────────────────────────────────────

class TerminalOutputManager:
    """终端输出管理器 — 全局单例，连接 UI 终端和 Agent 工具"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._output_buffer = []
            cls._instance._max_buffer = 200  # 最多保留 200 条记录
            cls._instance._command_history = []
            cls._instance._max_history = 100
        return cls._instance

    def append_output(self, output: str, command: str = "", exit_code: int = 0):
        """追加一条终端输出记录"""
        import time
        record = {
            "timestamp": time.strftime("%H:%M:%S"),
            "command": command,
            "output": output,
            "exit_code": exit_code,
        }
        self._output_buffer.append(record)
        if len(self._output_buffer) > self._max_buffer:
            self._output_buffer = self._output_buffer[-self._max_buffer:]

        if command:
            self._command_history.append({
                "timestamp": record["timestamp"],
                "command": command,
                "exit_code": exit_code,
            })
            if len(self._command_history) > self._max_history:
                self._command_history = self._command_history[-self._max_history:]

    def get_recent_output(self, count: int = 5) -> list:
        """获取最近 count 条输出记录"""
        return self._output_buffer[-count:] if self._output_buffer else []

    def get_full_output(self) -> str:
        """获取完整终端输出文本"""
        if not self._output_buffer:
            return "(终端无输出)"
        lines = []
        for record in self._output_buffer:
            if record["command"]:
                lines.append(f"[{record['timestamp']}] $ {record['command']}")
            if record["output"]:
                lines.append(record["output"])
            if record["exit_code"] != 0:
                lines.append(f"[退出码: {record['exit_code']}]")
            lines.append("")
        return "\n".join(lines)

    def get_command_history(self) -> list:
        """获取命令执行历史"""
        return list(self._command_history)

    def clear(self):
        """清空缓冲区"""
        self._output_buffer.clear()
        self._command_history.clear()


def get_terminal_manager() -> TerminalOutputManager:
    """获取终端输出管理器单例"""
    return TerminalOutputManager()


# ── 工具定义 ──────────────────────────────────────────────────

class GetTerminalOutputInput(BaseModel):
    count: int = Field(5, description="获取最近几条输出记录，默认 5。设为 0 获取全部")
    raw: bool = Field(False, description="是否只返回纯文本输出（不含时间戳和命令头），默认 False")


@tool(args_schema=GetTerminalOutputInput)
def get_terminal_output(count: int = 5, raw: bool = False) -> str:
    """获取终端中最近的命令输出。
    当 Agent 需要查看之前 run_command 或 execute_code 的输出时使用此工具，
    尤其是输出被截断或需要重新查看时。

    Args:
        count: 获取最近几条记录，0 表示全部
        raw: 是否只返回纯文本
    """
    mgr = get_terminal_manager()

    if raw:
        records = mgr.get_recent_output(count if count > 0 else 9999)
        if not records:
            return "(终端无输出)"
        return "\n---\n".join(r["output"] for r in records if r["output"])

    if count == 0:
        return mgr.get_full_output()

    records = mgr.get_recent_output(count)
    if not records:
        return "(终端无输出)"

    lines = [f"终端最近 {len(records)} 条输出:\n"]
    for r in records:
        lines.append(f"[{r['timestamp']}]", )
        if r["command"]:
            lines.append(f"  $ {r['command']}")
        if r["output"]:
            # 截断过长的输出
            out = r["output"]
            if len(out) > 2000:
                out = out[:2000] + f"\n... (输出共 {len(r['output'])} 字符，已截断)"
            lines.append(f"  {out}")
        if r["exit_code"] != 0:
            lines.append(f"  [退出码: {r['exit_code']}]")
        lines.append("")

    return "\n".join(lines)


class GetCommandHistoryInput(BaseModel):
    count: int = Field(20, description="获取最近几条命令历史，默认 20")


@tool(args_schema=GetCommandHistoryInput)
def get_command_history(count: int = 20) -> str:
    """获取终端中最近执行的命令历史。
    可用于了解之前执行过哪些命令及其结果。

    Args:
        count: 获取条数，默认 20
    """
    mgr = get_terminal_manager()
    history = mgr.get_command_history()

    if not history:
        return "(无命令历史)"

    recent = history[-count:] if count > 0 else history
    lines = [f"命令历史 (最近 {len(recent)} 条):"]
    for h in recent:
        icon = "✅" if h["exit_code"] == 0 else "❌"
        lines.append(f"  [{h['timestamp']}] {icon} {h['command']}")

    return "\n".join(lines)


@tool
def clear_terminal() -> str:
    """清除终端输出缓冲区和命令历史。
    在开始新任务或需要清理旧输出时使用。
    """
    mgr = get_terminal_manager()
    mgr.clear()
    return "✓ 终端输出已清除"
