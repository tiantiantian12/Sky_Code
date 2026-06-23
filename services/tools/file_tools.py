"""
文件操作工具集
为 Agent 提供本地文件读写、目录浏览、文件搜索能力
"""

import os
from langchain_core.tools import tool

# 回滚管理器引用（由外部注入）
_rollback_mgr = None


def set_rollback_manager(mgr):
    """注入回滚管理器实例"""
    global _rollback_mgr
    _rollback_mgr = mgr


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
            content = f.read(100_000)
        return content
    except PermissionError:
        return f"错误: 没有权限读取 - {file_path}"
    except Exception as e:
        return f"错误: {e}"


@tool
def write_file(file_path: str, content: str) -> str:
    """将内容写入本地文件。如果文件不存在会自动创建，如果目录不存在也会自动创建。

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
        return f"成功: 已写入 {len(content)} 字符到 {file_path}"
    except PermissionError:
        return f"错误: 没有权限写入 - {file_path}"
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
def run_command(command: str, working_dir: str = "") -> str:
    """在 Windows 终端执行命令并返回输出。可以运行 Windows CMD 命令。

    Args:
        command: 要执行的命令，例如 "dir D:\\project" 或 "tree /F /A D:\\project"
        working_dir: 命令的工作目录（可选），例如 "D:\\project"
    """
    import subprocess
    try:
        # Windows 用 chcp 65001 强制 UTF-8 输出
        full_cmd = f"chcp 65001 >nul && {command}"
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            timeout=30,
            cwd=working_dir if working_dir else None,
            encoding="utf-8",
            errors="replace",
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n[stderr] " + result.stderr
        if result.returncode != 0 and not output:
            output = f"[返回码: {result.returncode}]"
        return output[:5000] if output else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时 (30秒)"
    except Exception as e:
        return f"错误: {e}"


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
