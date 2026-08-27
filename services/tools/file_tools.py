"""
文件操作工具集
为 Agent 提供本地文件读写、目录浏览、文件搜索、代码执行能力
"""

import os
import ast
import json
import difflib
import tempfile
import sys
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


def __get_startupinfo():
    """获取 subprocess 启动信息，隐藏 Windows 控制台窗口"""
    if sys.platform == "win32":
        import subprocess
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None

# 回滚管理器引用（由外部注入）
_rollback_mgr = None

# 代码操作回调（由外部注入，用于 UI 反馈）
_code_feedback_callback = None

# Diff 回调（由外部注入，用于 UI 显示内联 diff 视图）
_diff_callback = None


def set_rollback_manager(mgr):
    """注入回滚管理器实例"""
    global _rollback_mgr
    _rollback_mgr = mgr


def set_code_feedback_callback(cb):
    """注入代码操作反馈回调，用于 UI 实时展示语法检查/执行结果"""
    global _code_feedback_callback
    _code_feedback_callback = cb


def set_diff_callback(cb):
    """注入 diff 操作回调，用于 UI 显示内联 diff 视图"""
    global _diff_callback
    _diff_callback = cb


def _notify_diff(file_path: str, old_content: str, new_content: str, applied: bool = True):
    """通知 UI 层显示 diff 视图"""
    if _diff_callback:
        try:
            _diff_callback(file_path, old_content, new_content, applied)
        except Exception:
            pass


def _check_python_syntax(file_path: str, content: str) -> str:
    """对 Python 文件做语法检查，返回空字符串表示通过，否则返回错误描述"""
    try:
        ast.parse(content, filename=file_path)
        return ""
    except SyntaxError as e:
        return f"SyntaxError 第{e.lineno}行: {e.msg}"


def _validate_python_file(file_path: str) -> str:
    """通过 py_compile 验证 Python 文件（比 ast.parse 更严格）"""
    import subprocess
    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", file_path],
            capture_output=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return ""
        # 提取关键错误行
        lines = (result.stderr or "").strip().split("\n")
        # 只保留包含 File/SyntaxError/IndentationError 的行
        key = [l for l in lines if any(
            k in l for k in ("SyntaxError", "IndentationError", "TabError", "File ")
        )]
        return "\n".join(key[:4]) if key else lines[-1] if lines else str(result.stderr)[:300]
    except subprocess.TimeoutExpired:
        return "语法检查超时"
    except FileNotFoundError:
        # Python 不在 PATH 中，回退到 ast.parse
        return ""
    except Exception as e:
        return f"语法检查异常: {e}"


def _notify_code_feedback(event_type: str, data: dict):
    """触达 UI 层的代码操作反馈"""
    if _code_feedback_callback:
        try:
            _code_feedback_callback(event_type, data)
        except Exception:
            pass


@tool
def read_file(file_path: str) -> str:
    """读取本地文件的内容。传入文件的绝对路径，返回文件内容。
    如果文件不存在或无法读取，返回错误信息。

    Args:
        file_path: 文件的绝对路径，例如 "D:/project/main.py"
    """
    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        if not os.path.isfile(file_path):
            return f"错误: 路径不是文件 - {file_path}"
        # 跳过过大的文件
        size = os.path.getsize(file_path)
        if size > 500_000:
            return f"错误: 文件过大 ({size} bytes)，超过 500KB 限制"
        # 跳过二进制文件
        text_exts = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json', '.xml',
                     '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf',
                     '.sh', '.bat', '.ps1', '.cmd', '.sql', '.csv', '.log', '.env',
                     '.c', '.cpp', '.h', '.hpp', '.java', '.go', '.rs', '.rb', '.php',
                     '.swift', '.kt', '.r', '.vue', '.svelte'}
        ext = os.path.splitext(file_path)[1].lower()
        if ext and ext not in text_exts:
            return f"错误: 不支持读取二进制文件类型 {ext}"
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        if len(content) > 300_000:
            head = 200_000
            tail = 100_000
            content = (
                content[:head]
                + f"\n\n... [文件过大，已截断：原 {len(content)} 字符，保留首 200KB + 尾 100KB] ...\n\n"
                + content[-tail:]
            )
        return content
    except PermissionError:
        return f"错误: 没有权限读取 - {file_path}"
    except Exception as e:
        return f"错误: {e}"


@tool
def write_file(file_path: str, content: str) -> str:
    """将内容写入本地文件。如果文件不存在会自动创建，如果目录不存在也会自动创建。
    对于 .py 文件会自动进行语法检查并在返回结果中包含检查结果。

    Args:
        file_path: 文件的绝对路径，例如 "D:/project/output.txt"
        content: 要写入的文本内容
    """
    try:
        # 回滚记录：写入前记录原文件内容
        if _rollback_mgr is not None:
            _rollback_mgr.record_write(file_path, content)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        _invalidate_cache_for_file(file_path)  # 文件变更→缓存失效

        # 增量更新向量数据库索引（后台非阻塞）
        _try_index_file_async(file_path)

        result_parts = [f"成功: 已写入 {len(content)} 字符到 {file_path}"]

        # ── Python 文件语法检查 ──
        if file_path.lower().endswith('.py'):
            # 快速 ast 检查
            ast_error = _check_python_syntax(file_path, content)
            if ast_error:
                result_parts.append(f"⚠ 语法错误: {ast_error}")
                _notify_code_feedback("syntax_error", {
                    "file": file_path, "error": ast_error,
                })
            else:
                # 用 py_compile 做更严格验证
                compile_error = _validate_python_file(file_path)
                if compile_error:
                    result_parts.append(f"⚠ 编译错误: {compile_error}")
                    _notify_code_feedback("compile_error", {
                        "file": file_path, "error": compile_error,
                    })
                else:
                    result_parts.append("✓ 语法检查通过")
                    _notify_code_feedback("syntax_ok", {
                        "file": file_path, "lines": content.count('\n') + 1,
                    })

        return "\n".join(result_parts)
    except PermissionError:
        return f"错误: 没有权限写入 - {file_path}"
    except Exception as e:
        return f"错误: {e}"


class EditFileInput(BaseModel):
    file_path: str = Field(..., description="要修改的文件的绝对路径")
    old_content: str = Field(..., description="要被替换的原始代码片段（必须与文件中的内容完全匹配，包括缩进）")
    new_content: str = Field(..., description="替换后的新代码片段")


@tool(args_schema=EditFileInput)
def edit_file(file_path: str, old_content: str, new_content: str) -> str:
    """对已有文件进行增量编辑（diff-based）。
    只需提供要替换的旧代码片段和新代码片段，工具会自动定位并替换。
    优势：无需重写整个文件，减少 token 消耗，降低出错风险。

    使用步骤：
    1. 先用 read_file 读取文件内容
    2. 找到需要修改的代码片段，将其作为 old_content
    3. 写出修改后的代码片段作为 new_content
    4. 调用 edit_file，工具会自动替换并显示 diff

    注意：old_content 必须与文件中的内容完全匹配（包括缩进和空行），
    否则替换会失败。如果 old_content 在文件中出现多次，只替换第一个匹配。

    Args:
        file_path: 文件的绝对路径，例如 "D:/project/main.py"
        old_content: 要被替换的原始代码片段
        new_content: 替换后的新代码片段
    """
    try:
        file_path = file_path.strip().strip('"').strip("'")
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        if not os.path.isfile(file_path):
            return f"错误: 路径不是文件 - {file_path}"

        # 读取原文件
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            original_content = f.read()

        # 检查 old_content 是否存在于文件中
        if old_content not in original_content:
            # 尝试去除首尾空白后匹配
            stripped_old = old_content.strip()
            if stripped_old and stripped_old in original_content:
                old_content = stripped_old
            else:
                # 提供有用的错误信息
                return (
                    f"错误: old_content 在文件中未找到匹配。\n"
                    f"请确保 old_content 与文件中的内容完全一致（包括缩进）。\n"
                    f"old_content 前 100 字符: {old_content[:100]!r}"
                )

        # 检查 old_content == new_content（无变化）
        if old_content == new_content:
            return "提示: old_content 与 new_content 相同，无需修改。"

        # 执行替换
        new_file_content = original_content.replace(old_content, new_content, 1)

        # 回滚记录
        if _rollback_mgr is not None:
            _rollback_mgr.record_write(file_path, new_file_content)

        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_file_content)
        _invalidate_cache_for_file(file_path)  # 文件变更→缓存失效

        # 增量更新向量数据库索引（后台非阻塞）
        _try_index_file_async(file_path)

        # 计算 diff 统计
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
        added = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
        removed = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))

        # 通知 UI 显示 diff 视图
        _notify_diff(file_path, original_content, new_file_content, applied=True)

        result_parts = [
            f"成功: 已修改 {file_path}",
            f"  +{added} 行 / -{removed} 行",
        ]

        # Python 文件语法检查
        if file_path.lower().endswith('.py'):
            ast_error = _check_python_syntax(file_path, new_file_content)
            if ast_error:
                result_parts.append(f"⚠ 语法错误: {ast_error}")
                _notify_code_feedback("syntax_error", {
                    "file": file_path, "error": ast_error,
                })
            else:
                compile_error = _validate_python_file(file_path)
                if compile_error:
                    result_parts.append(f"⚠ 编译错误: {compile_error}")
                    _notify_code_feedback("compile_error", {
                        "file": file_path, "error": compile_error,
                    })
                else:
                    result_parts.append("✓ 语法检查通过")
                    _notify_code_feedback("syntax_ok", {
                        "file": file_path, "lines": new_file_content.count('\n') + 1,
                    })

        return "\n".join(result_parts)
    except PermissionError:
        return f"错误: 没有权限写入 - {file_path}"
    except Exception as e:
        return f"错误: {e}"


@tool
def delete_file(file_path: str) -> str:
    """删除本地文件。

    Args:
        file_path: 文件的绝对路径，例如 "D:/project/output.txt"
    """
    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        
        # 回滚记录：删除前记录原文件内容
        if _rollback_mgr is not None:
            _rollback_mgr.record_delete(file_path)
        
        os.remove(file_path)
        _invalidate_cache_for_file(file_path)  # 文件删除→缓存失效
        # 从向量数据库中移除该文件的索引
        _try_remove_file_index_async(file_path)
        return f"成功: 已删除文件 {file_path}"
    except PermissionError:
        return f"错误: 没有权限删除 - {file_path}"
    except Exception as e:
        return f"错误: {e}"


@tool
def list_directory(dir_path: str) -> str:
    """列出指定目录下的所有文件和子目录。传入目录的绝对路径。

    Args:
        dir_path: 目录的绝对路径，例如 "D:\\project"
    """
    try:
        # 处理路径中的引号和空格
        dir_path = dir_path.strip().strip('"').strip("'")
        if not os.path.exists(dir_path):
            return f"错误: 目录不存在 - {dir_path}"
        if not os.path.isdir(dir_path):
            return f"错误: 路径不是目录 - {dir_path}"
        entries = []
        for name in sorted(os.listdir(dir_path)):
            full = os.path.join(dir_path, name)
            if os.path.isdir(full):
                entries.append(f"[DIR]  {name}/")
            else:
                size = os.path.getsize(full)
                if size < 1024:
                    entries.append(f"[FILE] {name} ({size}B)")
                elif size < 1024 * 1024:
                    entries.append(f"[FILE] {name} ({size // 1024}KB)")
                else:
                    entries.append(f"[FILE] {name} ({size // (1024*1024)}MB)")
        return "\n".join(entries) if entries else "(空目录)"
    except PermissionError:
        return f"错误: 没有权限访问 - {dir_path}"
    except Exception as e:
        return f"错误: {e}"


@tool
def run_command(command: str, working_dir: str = "", conda_env: str = "") -> str:
    """在 Windows 终端执行命令并返回输出。可指定 Conda 虚拟环境运行 Python。

    Args:
        command: 要执行的命令，例如 "python D:/project/test.py" 或 "dir D:\\project"
        working_dir: 命令的工作目录（可选），例如 "D:\\project"
        conda_env: Conda 虚拟环境名（可选），例如 "base" 或 "py39"。
                   留空则使用设置中配置的默认环境（对 python 命令自动生效）。
    """
    import subprocess
    from services.utils.terminal_config import wrap_command_with_conda, get_config, get_python_executable

    try:
        command = command.strip().strip('"').strip("'")
        working_dir = working_dir.strip().strip('"').strip("'") if working_dir else ""

        final_cmd, used_env = wrap_command_with_conda(command, conda_env)
        cfg = get_config()
        env_note = ""
        if used_env:
            py_exe = get_python_executable(used_env, cfg.get("conda_base", ""))
            env_note = f"[conda:{used_env}]"
            if py_exe:
                env_note += f" python={py_exe}"

        full_cmd = f"chcp 65001 >nul && {final_cmd}"
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            timeout=60,
            cwd=working_dir if working_dir else None,
            encoding="utf-8",
            errors="replace",
            startupinfo=__get_startupinfo(),
        )
        output = ""
        if env_note:
            output += env_note + "\n"
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n[stderr] " + result.stderr
        if result.returncode != 0 and not output.strip():
            output = f"[返回码: {result.returncode}]"
        elif result.returncode != 0 and "[stderr]" not in output:
            output += f"\n[返回码: {result.returncode}]"
        # 记录到终端输出管理器
        try:
            from services.tools.terminal_tools import get_terminal_manager
            get_terminal_manager().append_output(output, command, result.returncode)
        except Exception:
            pass
        return output[:8000] if output else "(无输出)"
    except subprocess.TimeoutExpired:
        return f"错误: 命令执行超时 (60秒)。可能脚本有 input() 等待输入或进入了 GUI 事件循环。"
    except Exception as e:
        return f"错误: {e}"


@tool
def execute_code(file_path: str, working_dir: str = "", conda_env: str = "",
                 script_args: str = "") -> str:
    """运行本地 Python 脚本并返回执行结果（stdout + stderr）。
    **当用户要求运行文件、测试代码、生成结果、查看输出时，必须调用此工具。**
    适用于模型写入脚本后验证其是否能正确运行，或用户明确要求执行某个 .py 文件。
    若运行报错，模型可以看到错误信息并修正代码后重新执行。

    Args:
        file_path: Python 脚本的绝对路径，例如 "D:/project/test.py"
        working_dir: 工作目录（可选），默认使用脚本所在目录
        conda_env: Conda 虚拟环境名（可选），例如 "base" 或 "py39"
        script_args: 传给脚本的命令行参数（可选），例如 "--verbose"
    """
    import subprocess
    from services.utils.terminal_config import wrap_command_with_conda, get_config, get_python_executable

    try:
        file_path = file_path.strip().strip('"').strip("'")
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        if not file_path.lower().endswith('.py'):
            return f"错误: 不是 Python 脚本 - {file_path}"

        working_dir = working_dir.strip().strip('"').strip("'") if working_dir else ""
        if not working_dir:
            working_dir = os.path.dirname(file_path) or "."

        args = script_args.strip().strip('"').strip("'") if script_args else ""
        command = f"python \"{file_path}\" {args}".strip()

        final_cmd, used_env = wrap_command_with_conda(command, conda_env)
        cfg = get_config()
        env_note = ""
        if used_env:
            py_exe = get_python_executable(used_env, cfg.get("conda_base", ""))
            env_note = f"[conda:{used_env}]"
            if py_exe:
                env_note += f" python={py_exe}"

        full_cmd = f"chcp 65001 >nul && {final_cmd}"
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            timeout=30,
            cwd=working_dir,
            encoding="utf-8",
            errors="replace",
            startupinfo=__get_startupinfo(),
        )
        output = ""
        if env_note:
            output += env_note + "\n"
        if result.stdout:
            output += result.stdout.rstrip() + "\n"
        if result.stderr:
            output += "[stderr] " + result.stderr.rstrip() + "\n"

        if result.returncode == 0:
            output += f"\n✅ 执行成功 (返回码 {result.returncode})"
            _notify_code_feedback("execute_ok", {
                "file": file_path, "output": result.stdout[:500],
                "exit_code": result.returncode,
            })
            # 记录到终端输出管理器
            try:
                from services.tools.terminal_tools import get_terminal_manager
                get_terminal_manager().append_output(output, f"python {file_path}", result.returncode)
            except Exception:
                pass
            # 以 "成功:" 前缀返回，以便 auto-finalize 检测
            return f"成功: 执行 {os.path.basename(file_path)} 完成\n{output[:6000]}"
        else:
            output += f"\n❌ 执行失败 (返回码 {result.returncode})"
            _notify_code_feedback("execute_error", {
                "file": file_path, "error": result.stderr[:500] or result.stdout[:500],
                "exit_code": result.returncode,
            })
            # 记录到终端输出管理器
            try:
                from services.tools.terminal_tools import get_terminal_manager
                get_terminal_manager().append_output(output, f"python {file_path}", result.returncode)
            except Exception:
                pass
            # 错误信息以 "错误:" 开头返回给模型，模型可以看到并修正代码后重新执行
            return f"错误: 执行 {os.path.basename(file_path)} 失败\n{output[:6000]}"
    except subprocess.TimeoutExpired:
        _notify_code_feedback("execute_timeout", {"file": file_path})
        return f"⏱ 错误: 脚本执行超时 (30秒)。可能脚本有 input() 等待输入、进入了 GUI 事件循环、或有死循环。请检查代码。"
    except Exception as e:
        _notify_code_feedback("execute_error", {"file": file_path, "error": str(e)})
        return f"错误: {e}"


# 项目扫描：忽略的目录/二进制模式（与 deep_read_directory 保持一致）
_SCAN_IGNORE_DIRS = {
    '__pycache__', '.git', '.svn', '.hg', 'node_modules', '.venv', 'venv',
    '.idea', '.vscode', '.vs', '.gradle', 'build', 'dist', '.next',
    '.nuxt', '.cache', '.tox', '.eggs',
}
_SCAN_IGNORE_FILE_PATTERNS = (
    '*.pyc', '*.pyo', '*.so', '*.dll', '*.exe', '*.pyd', '*.class',
    '*.png', '*.jpg', '*.jpeg', '*.gif', '*.ico', '*.svg', '*.woff',
    '*.mp3', '*.mp4', '*.zip', '*.tar', '*.gz', '*.db', '*.sqlite',
    '*.pdf', '*.docx', '*.xlsx', '*.pptx',
)
_SCAN_TEXT_EXTS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss',
    '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.md', '.txt', '.rst', '.markdown', '.csv', '.log', '.env', '.sh',
    '.bat', '.ps1', '.cmd', '.sql', '.c', '.cpp', '.h', '.hpp', '.java',
    '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.r', '.vue', '.svelte',
    '.gitignore', '.editorconfig',
}
# 小体积关键文件直接读全文
_SCAN_KEY_FILES = frozenset({
    'readme.md', 'main.py', 'requirements.txt', 'pyproject.toml',
    'setup.py', 'package.json', '.gitignore', 'config.json',
})

# ── scan_project 缓存 ─────────────────────────────────────────
import time as _time
import hashlib as _hashlib

_project_cache: dict = {}  # {norm_path: {"fingerprint": str, "result": str, "ts": float}}
_CACHE_TTL_SECONDS = 300  # 5 分钟后自动过期
_CACHE_FINGERPRINT_MAX_DEPTH = 4  # 指纹只扫描前4层目录结构


def _compute_project_fingerprint(dir_path: str) -> str:
    """快速计算项目目录指纹：基于文件路径+大小+mtime，不读内容。仅前4层+120个文件。"""
    import fnmatch
    hasher = _hashlib.sha256()
    file_count = 0

    try:
        norm = os.path.normpath(dir_path).lower()
        # 根目录自身的 mtime
        hasher.update(str(os.path.getmtime(dir_path)).encode())

        for root, dirs, files in os.walk(dir_path):
            depth = root.replace(dir_path, "").count(os.sep)
            if depth > _CACHE_FINGERPRINT_MAX_DEPTH:
                dirs[:] = []
                files = []
            else:
                dirs[:] = sorted(d for d in dirs if d not in _SCAN_IGNORE_DIRS)

            rel_root = os.path.relpath(root, dir_path).replace("\\", "/")
            hasher.update(rel_root.encode())

            for name in sorted(files):
                if file_count >= 120:
                    break
                full = os.path.join(root, name)
                try:
                    is_text = os.path.splitext(name)[1].lower() in _SCAN_TEXT_EXTS or not os.path.splitext(name)[1]
                    if not is_text:
                        continue  # 二进制文件不计入指纹（不参与 scan 输出）
                except Exception:
                    continue
                stat = os.stat(full)
                # 指纹因子：相对路径 + 文件大小 + 修改时间
                rel = os.path.relpath(full, dir_path).replace("\\", "/")
                hasher.update(rel.encode())
                hasher.update(str(stat.st_size).encode())
                hasher.update(str(stat.st_mtime).encode())
                file_count += 1

            if file_count >= 120:
                break
    except PermissionError:
        pass

    return hasher.hexdigest()


def _try_get_cached_scan(dir_path: str) -> Optional[str]:
    """尝试返回缓存的项目扫描结果；None 表示缓存未命中或已过期。"""
    norm = os.path.normpath(dir_path).lower()
    entry = _project_cache.get(norm)
    if not entry:
        return None
    # TTL 过期
    if _time.time() - entry["ts"] > _CACHE_TTL_SECONDS:
        return None
    # 指纹校验：目录是否发生变化
    try:
        current_fp = _compute_project_fingerprint(dir_path)
    except Exception:
        return None
    if current_fp != entry["fingerprint"]:
        return None
    return entry["result"]


def _set_cached_scan(dir_path: str, result: str):
    """缓存项目扫描结果。"""
    try:
        fp = _compute_project_fingerprint(dir_path)
        norm = os.path.normpath(dir_path).lower()
        _project_cache[norm] = {
            "fingerprint": fp,
            "result": result,
            "ts": _time.time(),
        }
    except Exception:
        pass  # 缓存失败不阻塞主流程


def invalidate_scan_cache(dir_path: str = None):
    """外部可调用：清除指定目录的缓存（或全部缓存）。"""
    if dir_path is None:
        _project_cache.clear()
    else:
        norm = os.path.normpath(dir_path).lower()
        _project_cache.pop(norm, None)


def get_cached_project_overview(dir_path: str) -> str:
    """获取缓存中的项目扫描概览（仅目录树+统计，不含文件内容全文）。
    返回空字符串表示缓存未命中或无效。用于向 Agent 系统提示注入项目上下文。"""
    cached = _try_get_cached_scan(dir_path)
    if not cached:
        return ""
    # 只提取目录树和统计部分（"=== 文件摘要" 之前的段落）
    lines = cached.split("\n")
    overview_lines = []
    for line in lines:
        if line.startswith("(⚡"):
            continue
        if line.startswith("=== 文件摘要") or line.startswith("=== 关键文件"):
            break
        overview_lines.append(line)
    return "\n".join(overview_lines).strip()


def warm_scan_cache(dir_path: str, delay_seconds: float = 0.3):
    """在后台预热 scan_project 缓存（非阻塞）。
    打开工作区时调用，让首轮对话就能获取项目概览。

    Args:
        dir_path: 项目目录绝对路径
        delay_seconds: 延迟执行秒数（默认 0.3），留给 UI 渲染时间
    """
    if not dir_path or not os.path.isdir(dir_path):
        return

    def _warm():
        import time as _t
        _t.sleep(delay_seconds)
        try:
            # 直接调用 scan_project，填充缓存；不需要关心返回值
            from services.tools.file_tools import scan_project as _sp
            _sp.invoke({"dir_path": dir_path})
        except Exception:
            pass

    import threading as _th
    t = _th.Thread(target=_warm, daemon=True)
    t.start()


def _invalidate_cache_for_file(file_path: str):
    """文件变更时，使包含该文件的项目缓存指纹失效（不删除缓存条目）。
    下次 _try_get_cached_scan 时指纹校验不匹配会自然丢弃旧缓存。"""
    if not _project_cache:
        return
    try:
        norm_file = os.path.normpath(os.path.abspath(file_path)).lower()
        # 只将包含该文件的目录缓存标记为指纹过期（置空指纹），不删除条目
        # 这样下次 _try_get_cached_scan 时 current_fp != entry["fingerprint"] 自然失效
        for k, v in _project_cache.items():
            if norm_file.startswith(k):
                v["fingerprint"] = "__invalidated__"
    except Exception:
        pass


# ── 向量数据库增量更新（后台非阻塞） ──────────────────────────

def _try_index_file_async(file_path: str):
    """后台非阻塞索引单个文件到向量数据库。
    只索引代码文件，非代码文件跳过。"""
    ext = os.path.splitext(file_path)[1].lower()
    # 与 rag_tools.CODE_EXTENSIONS 保持一致
    code_exts = {
        '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h',
        '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt',
        '.html', '.css', '.scss', '.less', '.json', '.xml', '.yaml', '.yml',
        '.toml', '.ini', '.cfg', '.conf', '.md', '.txt', '.sh', '.bat'
    }
    if ext not in code_exts:
        return

    def _do_index():
        try:
            from services.tools.rag_tools import index_file
            index_file(file_path)
        except Exception:
            pass  # RAG 不可用时静默跳过

    import threading as _th
    t = _th.Thread(target=_do_index, daemon=True)
    t.start()


def _try_remove_file_index_async(file_path: str):
    """后台非阻塞从向量数据库移除文件索引。"""
    def _do_remove():
        try:
            from services.tools.rag_tools import remove_file_from_index
            remove_file_from_index(file_path)
        except Exception:
            pass

    import threading as _th
    t = _th.Thread(target=_do_remove, daemon=True)
    t.start()


def _scan_should_ignore(name: str, is_dir: bool) -> bool:
    import fnmatch
    if is_dir:
        return name in _SCAN_IGNORE_DIRS
    for pattern in _SCAN_IGNORE_FILE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _scan_outline_python(content: str) -> str:
    lines = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return f"  (语法错误: {e.msg})"
    doc = ast.get_docstring(tree)
    if doc:
        first = doc.strip().split('\n')[0][:100]
        lines.append(f'  """{first}"""')
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                lines.append(f"  import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            names = ', '.join(a.name for a in node.names[:8])
            suffix = '...' if len(node.names) > 8 else ''
            lines.append(f"  from {mod} import {names}{suffix}")
        elif isinstance(node, ast.ClassDef):
            bases = ', '.join(
                getattr(b, 'id', getattr(b, 'attr', '')) for b in node.bases
            )
            head = f"  class {node.name}"
            if bases:
                head += f"({bases})"
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ][:6]
            if methods:
                head += f": {', '.join(methods)}..."
            lines.append(head)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args = [a.arg for a in node.args.args[:5]]
            lines.append(f"  {prefix}def {node.name}({', '.join(args)}): ...")
        if len(lines) >= 20:
            lines.append("  ...")
            break
    return '\n'.join(lines) if lines else "  (空模块)"


def _scan_outline_json(content: str) -> str:
    try:
        data = json.loads(content)
    except Exception:
        return _scan_outline_text(content, max_lines=8)
    if isinstance(data, dict):
        keys = list(data.keys())[:20]
        parts = [f"  keys: {', '.join(str(k) for k in keys)}"]
        if len(data) > 20:
            parts.append(f"  ... 共 {len(data)} 个键")
        return '\n'.join(parts)
    if isinstance(data, list):
        return f"  array[{len(data)}]"
    return f"  {type(data).__name__}"


def _scan_outline_text(content: str, max_lines: int = 12) -> str:
    lines = content.split('\n')[:max_lines]
    out = []
    for ln in lines:
        s = ln.rstrip()
        if s:
            out.append(f"  | {s[:120]}")
    total_lines = len(content.split('\n'))
    if total_lines > max_lines:
        out.append(f"  | ... (共 {total_lines} 行)")
    return '\n'.join(out) if out else "  (空文件)"


def _scan_outline_file(rel_path: str, content: str) -> str:
    ext = os.path.splitext(rel_path)[1].lower()
    name = os.path.basename(rel_path).lower()
    if ext == '.py' or name.endswith('.py'):
        return _scan_outline_python(content)
    if ext == '.json':
        return _scan_outline_json(content)
    if ext in ('.md', '.markdown', '.rst'):
        return _scan_outline_text(content, max_lines=8)
    return _scan_outline_text(content, max_lines=10)


@tool
def scan_project(dir_path: str, max_depth: int = 4, max_files: int = 120,
                 max_output_chars: int = 55_000) -> str:
    """轻量扫描项目目录：输出目录树 + 每文件符号/摘要轮廓；关键小文件（README、main.py 等）读全文。
    用于项目整体分析、架构评审；需要修改代码或审查具体实现时，再用 read_file 读取完整文件。

    Args:
        dir_path: 项目根目录绝对路径，例如 "D:/qt_project/LLM_Agent"
        max_depth: 最大递归深度（可选），默认 4
        max_files: 最多扫描文件数（可选），默认 120
        max_output_chars: 输出字符上限（可选），默认 55000
    """
    import fnmatch

    dir_path = dir_path.strip().strip('"').strip("'")
    if not os.path.exists(dir_path):
        return f"错误: 路径不存在 - {dir_path}"
    if not os.path.isdir(dir_path):
        return f"错误: 路径不是目录 - {dir_path}"

    # ── 缓存检查：若目录未变化且未过期，直接返回缓存结果 ──
    cached = _try_get_cached_scan(dir_path)
    if cached is not None:
        return cached + "\n\n(⚡ 缓存命中 — 项目结构与上次扫描一致)"

    root_name = os.path.basename(dir_path.rstrip('/\\')) or dir_path
    tree_lines = [f"📁 {root_name}/"]
    key_full_sections = []
    outline_sections = []
    file_count = 0
    dir_count = 0
    truncated_files = False

    def _read_text(full_path: str, limit: int = 80_000) -> str:
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read(limit)
        except Exception:
            return ""

    def _walk(current: str, depth: int, prefix: str):
        nonlocal file_count, dir_count, truncated_files
        if depth > max_depth or file_count >= max_files:
            return
        try:
            entries = sorted(os.listdir(current))
        except PermissionError:
            tree_lines.append(f"{prefix}[权限不足]")
            return

        dirs, files = [], []
        for name in entries:
            full = os.path.join(current, name)
            if os.path.isdir(full):
                if not _scan_should_ignore(name, True):
                    dirs.append(name)
            elif not _scan_should_ignore(name, False):
                files.append(name)

        all_entries = [(n, True) for n in dirs] + [(n, False) for n in files]
        for i, (name, is_dir) in enumerate(all_entries):
            if file_count >= max_files:
                truncated_files = True
                return
            full = os.path.join(current, name)
            is_last = i == len(all_entries) - 1
            conn = "└── " if is_last else "├── "
            ext_prefix = "    " if is_last else "│   "

            if is_dir:
                dir_count += 1
                tree_lines.append(f"{prefix}{conn}📁 {name}/")
                _walk(full, depth + 1, prefix + ext_prefix)
            else:
                file_count += 1
                rel = os.path.relpath(full, dir_path).replace('\\', '/')
                size = os.path.getsize(full)
                size_str = f"{size}B" if size < 1024 else f"{size // 1024}KB"
                ext = os.path.splitext(name)[1].lower()
                is_text = ext in _SCAN_TEXT_EXTS or not ext
                tree_lines.append(f"{prefix}{conn}📄 {name} ({size_str})")

                if not is_text or size > 500_000:
                    continue

                content = _read_text(full)
                if not content:
                    continue

                name_lower = name.lower()
                if name_lower in _SCAN_KEY_FILES and size <= 12_000:
                    key_full_sections.append(
                        f"### {rel} ###\n{content.rstrip()}\n"
                    )
                elif is_text:
                    outline = _scan_outline_file(rel, content)
                    outline_sections.append(f"--- {rel} ({size_str}) ---\n{outline}")

    _walk(dir_path, 1, "")

    parts = [
        f"=== 项目轻量扫描: {root_name} ===",
        f"路径: {dir_path}",
        f"统计: {dir_count} 个子目录, {file_count} 个文件 (深度≤{max_depth})",
        "",
        "【说明】以下为目录树 + 符号/行摘要；关键配置文件为全文。",
        "需要修改代码或审查具体实现时，请用 read_file 读取该文件完整内容。",
        "",
        "=== 目录树 ===",
        "\n".join(tree_lines),
    ]
    if truncated_files:
        parts.append(f"\n⚠ 已达文件数上限 ({max_files})，部分文件未扫描。")

    if key_full_sections:
        parts.extend(["", "=== 关键文件（全文）===", *key_full_sections])

    if outline_sections:
        parts.extend(["", "=== 文件摘要（符号/前几行）===", *outline_sections])

    result = "\n".join(parts)
    if len(result) > max_output_chars:
        result = (
            result[: max_output_chars - 200]
            + f"\n\n... [扫描输出已截断，原 {len(result)} 字符；"
            "请用 read_file 读取感兴趣的文件全文] ..."
        )
    # ── 缓存结果（仅默认参数时缓存） ──
    if max_depth == 4 and max_files == 120 and max_output_chars == 55_000:
        _set_cached_scan(dir_path, result)
    return result


@tool
def deep_read_directory(dir_path: str, max_depth: int = 3, max_files: int = 80,
                        read_contents: bool = True) -> str:
    """递归读取整个目录的内容，包括完整的文件树结构和所有文本文件的内容。
    用于让模型理解一个项目的整体结构和代码，而不是逐个文件读取。

    Args:
        dir_path: 目录的绝对路径，例如 "D:/qt_project/LLM_Agent"
        max_depth: 最大递归深度（可选），默认 3 层，防止输出过大
        max_files: 最多读取的文件数（可选），默认 80 个
        read_contents: 是否读取文本文件内容（可选），默认 True。设为 False 则只列出文件树
    """
    import fnmatch

    dir_path = dir_path.strip().strip('"').strip("'")
    if not os.path.exists(dir_path):
        return f"错误: 路径不存在 - {dir_path}"
    if not os.path.isdir(dir_path):
        return f"错误: 路径不是目录 - {dir_path}"

    # 忽略的目录/文件模式
    ignore_dirs = {
        '__pycache__', '.git', '.svn', '.hg', 'node_modules', '.venv', 'venv',
        '.idea', '.vscode', '.vs', '.gradle', 'build', 'dist', '.next',
        '.nuxt', '.cache', '.tox', '.eggs', '*.egg-info',
    }
    ignore_files_patterns = ['*.pyc', '*.pyo', '*.so', '*.dll', '*.exe',
                             '*.pyd', '*.class', '*.o', '*.a', '*.lib',
                             '*.obj', '*.bin', '*.dat', '*.db', '*.sqlite',
                             '*.png', '*.jpg', '*.jpeg', '*.gif', '*.ico',
                             '*.svg', '*.woff', '*.woff2', '*.ttf', '*.eot',
                             '*.mp3', '*.mp4', '*.avi', '*.mov', '*.zip',
                             '*.tar', '*.gz', '*.7z', '*.rar', '*.pdf',
                             '*.docx', '*.xlsx', '*.pptx', '*.lock']

    text_exts = {
        '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss', '.less',
        '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
        '.md', '.txt', '.rst', '.markdown', '.csv', '.log', '.env', '.sh',
        '.bat', '.ps1', '.cmd', '.sql', '.c', '.cpp', '.h', '.hpp', '.java',
        '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.r', '.vue', '.svelte',
        '.gradle', '.properties', '.dockerfile', '.gitignore', '.editorconfig',
        '.eslintrc', '.prettierrc', '.babelrc',
    }

    output_lines = []
    file_count = [0]  # 用列表以在闭包内修改
    dir_count = [0]

    def is_ignored(name: str, is_dir: bool) -> bool:
        if is_dir:
            return name in ignore_dirs
        for pattern in ignore_files_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _walk(current_path: str, depth: int, prefix: str):
        if depth > max_depth or file_count[0] >= max_files:
            return
        try:
            entries = sorted(os.listdir(current_path))
        except PermissionError:
            output_lines.append(f"{prefix}[权限不足]")
            return

        dirs = []
        files = []
        for name in entries:
            full = os.path.join(current_path, name)
            if os.path.isdir(full):
                if not is_ignored(name, True):
                    dirs.append(name)
            else:
                if not is_ignored(name, False):
                    files.append(name)

        for i, name in enumerate(dirs):
            if file_count[0] >= max_files:
                break
            full = os.path.join(current_path, name)
            is_last_dir = (i == len(dirs) - 1) and len(files) == 0
            connector = "└── " if is_last_dir else "├── "
            dir_count[0] += 1
            output_lines.append(f"{prefix}{connector}📁 {name}/")
            extension = "    " if is_last_dir else "│   "
            _walk(full, depth + 1, prefix + extension)

        for i, name in enumerate(files):
            if file_count[0] >= max_files:
                break
            full = os.path.join(current_path, name)
            is_last = (i == len(files) - 1)
            connector = "└── " if is_last else "├── "
            size = os.path.getsize(full)
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size // 1024}KB"
            else:
                size_str = f"{size // (1024 * 1024)}MB"
            file_count[0] += 1
            ext = os.path.splitext(name)[1].lower()
            is_text = ext in text_exts
            icon = "📄" if is_text else "📦"
            output_lines.append(f"{prefix}{connector}{icon} {name} ({size_str})")

            # 读取文本文件内容
            if read_contents and is_text and size <= 100_000:
                try:
                    with open(full, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read(80_000)
                    # 缩进内容
                    indent = prefix + ("    " if is_last else "│   ")
                    content_lines = content.split('\n')
                    # 只展示前 200 行
                    show_lines = content_lines[:200]
                    for cl in show_lines:
                        output_lines.append(f"{indent}│ {cl}")
                    if len(content_lines) > 200:
                        output_lines.append(f"{indent}│ ... (省略 {len(content_lines) - 200} 行)")
                    output_lines.append("")  # 空行分隔
                except Exception:
                    pass  # 二进制或无法读取，跳过

    # 根目录
    root_name = os.path.basename(dir_path) or dir_path
    output_lines.append(f"📁 {root_name}/")
    _walk(dir_path, 1, "")

    if file_count[0] >= max_files:
        output_lines.append(f"\n⚠ 已达到文件数上限 ({max_files})，部分文件未列出。可缩小 dir_path 或增大 max_files。")

    summary = (f"\n---\n📊 统计: {dir_count[0]} 个子目录, "
               f"{file_count[0]} 个文件, 最大深度 {max_depth} 层")
    output_lines.append(summary)

    return "\n".join(output_lines)


@tool
def search_files(dir_path: str, keyword: str) -> str:
    """在指定目录中搜索包含关键字的文件名或文件内容。递归搜索子目录。

    Args:
        dir_path: 要搜索的目录绝对路径
        keyword: 搜索关键字（文件名或内容中包含的文本）
    """
    try:
        if not os.path.isdir(dir_path):
            return f"错误: 目录不存在 - {dir_path}"
        results = []
        count = 0
        for root, dirs, files in os.walk(dir_path):
            # 跳过隐藏目录和常见忽略目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       {'node_modules', '__pycache__', '.git', 'venv', '.venv'}]
            for name in files:
                full_path = os.path.join(root, name)
                # 文件名匹配
                if keyword.lower() in name.lower():
                    results.append(f"[文件名] {full_path}")
                    count += 1
                    if count >= 30:
                        break
                # 内容匹配（只搜索文本文件）
                ext = os.path.splitext(name)[1].lower()
                text_exts = {'.py', '.js', '.ts', '.json', '.md', '.txt', '.html', '.css'}
                if ext in text_exts:
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                            for i, line in enumerate(f, 1):
                                if keyword.lower() in line.lower():
                                    results.append(f"[内容] {full_path}:{i} - {line.strip()[:100]}")
                                    count += 1
                                    break
                    except Exception:
                        pass
                if count >= 30:
                    break
            if count >= 30:
                break
        return "\n".join(results) if results else f"未找到包含 '{keyword}' 的文件"
    except Exception as e:
        return f"错误: {e}"
