"""
后台 Agent 工具集
为 Agent 提供后台任务管理能力：
  - start_background_task  : 启动后台任务
  - get_task_status        : 查询任务状态
  - list_background_tasks  : 列出所有后台任务
  - cancel_background_task : 取消后台任务

这些工具让 Agent 可以：
  1. 把长时间运行的命令放到后台执行
  2. 继续处理用户的其他请求
  3. 定期检查后台任务状态
  4. 在任务完成后获取结果
"""

import os
import sys
import subprocess
import time
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from services.core.background_agent import get_background_manager, BackgroundTask, TaskStatus
from services.tools.terminal_tools import get_terminal_manager


def _get_startupinfo():
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None


def _run_command_background(task: BackgroundTask, command: str, working_dir: str = "",
                            timeout: int = 300) -> str:
    """后台执行命令的 target 函数"""
    task.log(f"执行命令: {command}")
    task.update_progress(0.1, "命令启动中")

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=working_dir if working_dir else None,
            encoding='utf-8',
            errors='replace',
            startupinfo=_get_startupinfo(),
        )

        # 等待完成，同时检查取消
        start_time = time.time()
        while True:
            if task.is_cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except:
                    process.kill()
                return "任务已取消"

            try:
                ret = process.poll()
                if ret is not None:
                    break
            except Exception:
                break

            if time.time() - start_time > timeout:
                process.terminate()
                return f"任务超时 ({timeout}秒)"

            # 更新进度（基于已运行时间）
            elapsed = time.time() - start_time
            progress = min(0.9, elapsed / timeout * 0.9)
            task.update_progress(progress, f"运行中... ({elapsed:.0f}s)")
            time.sleep(1)

        stdout = process.stdout.read() if process.stdout else ""
        stderr = process.stderr.read() if process.stderr else ""

        task.update_progress(0.95, "收集输出")

        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += "\n[stderr]\n" + stderr
        output += f"\n[退出码: {process.returncode}]"

        # 同时记录到终端管理器
        term_mgr = get_terminal_manager()
        term_mgr.append_output(output, command, process.returncode)

        task.update_progress(1.0, "完成")
        return output[:8000]

    except Exception as e:
        task.log(f"执行失败: {e}")
        raise


def _run_script_background(task: BackgroundTask, file_path: str, args: str = "",
                           working_dir: str = "", timeout: int = 120) -> str:
    """后台执行 Python 脚本的 target 函数"""
    from services.utils.terminal_config import wrap_command_with_conda

    task.log(f"执行脚本: {file_path}")
    task.update_progress(0.1, "启动脚本")

    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    if not working_dir:
        working_dir = os.path.dirname(file_path)

    script_args = args.split() if args else []
    command = f'{sys.executable} "{file_path}" {" ".join(script_args)}'.strip()
    final_cmd, used_env = wrap_command_with_conda(command, "")

    try:
        full_cmd = f"chcp 65001 >nul && {final_cmd}"
        process = subprocess.Popen(
            full_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=working_dir,
            encoding='utf-8',
            errors='replace',
            startupinfo=_get_startupinfo(),
        )

        start_time = time.time()
        while True:
            if task.is_cancelled():
                process.terminate()
                return "脚本执行已取消"

            ret = process.poll()
            if ret is not None:
                break

            if time.time() - start_time > timeout:
                process.terminate()
                return f"脚本执行超时 ({timeout}秒)"

            elapsed = time.time() - start_time
            task.update_progress(min(0.9, elapsed / timeout * 0.9), f"运行中... ({elapsed:.0f}s)")
            time.sleep(1)

        stdout = process.stdout.read() if process.stdout else ""
        stderr = process.stderr.read() if process.stderr else ""

        output = ""
        if stdout:
            output += stdout.rstrip() + "\n"
        if stderr:
            output += "[stderr] " + stderr.rstrip() + "\n"
        output += f"\n{'✅' if process.returncode == 0 else '❌'} 退出码: {process.returncode}"

        # 记录到终端管理器
        term_mgr = get_terminal_manager()
        term_mgr.append_output(output, command, process.returncode)

        task.update_progress(1.0, "脚本执行完成")
        return output[:8000]

    except Exception as e:
        task.log(f"脚本执行失败: {e}")
        raise


# ── 工具定义 ──────────────────────────────────────────────────

class StartBackgroundTaskInput(BaseModel):
    task_name: str = Field(..., description="任务名称（简短描述）")
    task_type: str = Field("command", description="任务类型：command（执行命令）/ script（运行 Python 脚本）")
    command: str = Field("", description="要执行的命令（task_type=command 时必填）")
    script_path: str = Field("", description="Python 脚本路径（task_type=script 时必填）")
    script_args: str = Field("", description="脚本参数（可选）")
    working_dir: str = Field("", description="工作目录（可选）")
    timeout: int = Field(300, description="超时时间（秒），默认 300")


@tool(args_schema=StartBackgroundTaskInput)
def start_background_task(task_name: str, task_type: str = "command", command: str = "",
                          script_path: str = "", script_args: str = "",
                          working_dir: str = "", timeout: int = 300) -> str:
    """在后台启动一个长时间运行的任务。
    任务在独立线程中执行，不会阻塞当前对话。
    适用于：长时间运行的脚本、训练模型、数据处理等。

    Args:
        task_name: 任务名称
        task_type: 任务类型，command 或 script
        command: 要执行的命令（type=command 时）
        script_path: Python 脚本路径（type=script 时）
        script_args: 脚本参数
        working_dir: 工作目录
        timeout: 超时秒数
    """
    mgr = get_background_manager()

    if task_type == "command":
        if not command:
            return "错误: task_type=command 时必须提供 command 参数"
        desc = f"后台命令: {command[:100]}"
        task_id = mgr.submit_task(
            name=task_name,
            description=desc,
            target=_run_command_background,
            args=(command, working_dir, timeout),
        )
    elif task_type == "script":
        if not script_path:
            return "错误: task_type=script 时必须提供 script_path 参数"
        desc = f"后台脚本: {os.path.basename(script_path)}"
        task_id = mgr.submit_task(
            name=task_name,
            description=desc,
            target=_run_script_background,
            args=(script_path, script_args, working_dir, timeout),
        )
    else:
        return f"错误: 不支持的任务类型 {task_type}，可选: command, script"

    return f"✓ 后台任务已启动\n  任务ID: {task_id}\n  名称: {task_name}\n  描述: {desc}\n\n使用 get_task_status 查看进度，list_background_tasks 查看所有任务"


class GetTaskStatusInput(BaseModel):
    task_id: str = Field(..., description="任务 ID")


@tool(args_schema=GetTaskStatusInput)
def get_task_status(task_id: str) -> str:
    """查询后台任务的执行状态和结果。

    Args:
        task_id: 任务 ID
    """
    mgr = get_background_manager()
    task = mgr.get_task(task_id)

    if not task:
        return f"错误: 未找到任务 ID '{task_id}'"

    d = task.to_dict()
    status_icon = {
        "pending": "⏳", "running": "🔄", "completed": "✅",
        "failed": "❌", "cancelled": "🚫"
    }.get(d["status"], "❓")

    lines = [
        f"{status_icon} 任务状态: {d['name']}",
        f"  ID: {d['task_id']}",
        f"  状态: {d['status']}",
        f"  进度: {d['progress']*100:.0f}% {d['progress_message']}",
    ]

    if d["duration"] > 0:
        lines.append(f"  耗时: {d['duration']:.1f}s")

    if d["logs"]:
        lines.append(f"\n  最近日志:")
        for log in d["logs"][-5:]:
            lines.append(f"    [{log['timestamp']}] {log['message']}")

    if d["status"] == "completed":
        lines.append(f"\n  结果:\n{d['result'][:2000]}")
    elif d["status"] == "failed":
        lines.append(f"\n  错误:\n{d['error'][:2000]}")

    return "\n".join(lines)


class ListBackgroundTasksInput(BaseModel):
    status_filter: str = Field("all", description="过滤状态：all/active/completed/failed，默认 all")


@tool(args_schema=ListBackgroundTasksInput)
def list_background_tasks(status_filter: str = "all") -> str:
    """列出所有后台任务及其状态。

    Args:
        status_filter: 过滤状态，all/active/completed/failed
    """
    mgr = get_background_manager()

    if status_filter == "active":
        tasks = mgr.get_active_tasks()
    else:
        tasks = mgr.get_all_tasks()
        if status_filter != "all":
            tasks = [t for t in tasks if t["status"] == status_filter]

    if not tasks:
        return "（无后台任务）"

    lines = [f"后台任务列表 ({len(tasks)} 个):\n"]
    for t in tasks:
        icon = {
            "pending": "⏳", "running": "🔄", "completed": "✅",
            "failed": "❌", "cancelled": "🚫"
        }.get(t["status"], "❓")
        progress = f" [{t['progress']*100:.0f}%]" if t["status"] == "running" else ""
        duration = f" ({t['duration']:.1f}s)" if t["duration"] > 0 else ""
        lines.append(f"  {icon} [{t['task_id']}] {t['name']}{progress}{duration} — {t['status']}")

    return "\n".join(lines)


class CancelBackgroundTaskInput(BaseModel):
    task_id: str = Field(..., description="任务 ID")


@tool(args_schema=CancelBackgroundTaskInput)
def cancel_background_task(task_id: str) -> str:
    """取消正在运行的后台任务。

    Args:
        task_id: 任务 ID
    """
    mgr = get_background_manager()
    if mgr.cancel_task(task_id):
        return f"✓ 任务 {task_id} 已请求取消"
    return f"错误: 无法取消任务 {task_id}（不存在或已完成）"
