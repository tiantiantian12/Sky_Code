"""
Web 预览工具集
为 Agent 提供启动本地服务器、预览 Web 应用的能力
"""

import subprocess
import time
import os
import sys
from typing import Optional
from langchain_core.tools import tool

# 全局变量：存储已启动的服务器进程
_running_servers = {}

# 获取主窗口的回调（用于触发 UI 预览）
_preview_url_callback = None


def set_preview_url_callback(callback):
    """设置预览 URL 回调函数（由 MainWindow 调用）"""
    global _preview_url_callback
    _preview_url_callback = callback


def _trigger_preview_ui(url: str):
    """触发 UI 预览 URL"""
    global _preview_url_callback
    if _preview_url_callback:
        try:
            _preview_url_callback(url)
        except Exception as e:
            print(f"[Preview Tools] 触发预览回调失败: {e}")


@tool
def start_preview_server(port: int = 8000, directory: str = ".", server_type: str = "http") -> str:
    """启动本地 HTTP 服务器用于预览 Web 应用。
    支持的 server_type: http (Python http.server), flask, fastapi, streamlit

    Args:
        port: 服务器端口（默认 8000）
        directory: 服务器根目录（默认当前目录）
        server_type: 服务器类型，支持: http, flask, fastapi, streamlit（默认 http）

    Returns:
        服务器启动状态和访问 URL
    """
    global _running_servers

    # 检查端口是否已被占用
    server_key = f"{port}:{directory}"
    if server_key in _running_servers:
        process = _running_servers[server_key]
        if process.poll() is None:  # 进程还在运行
            return f"端口 {port} 上已有服务器在运行。访问: http://localhost:{port}"

    # 如果目录不存在，创建它
    os.makedirs(directory, exist_ok=True)

    # 切换到目标目录
    original_dir = os.getcwd()
    os.chdir(directory)

    try:
        process = None

        if server_type == "http":
            # 使用 Python 内置 http.server
            # Windows 和 Unix 兼容
            cmd = [sys.executable, "-m", "http.server", str(port)]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

        elif server_type == "flask":
            # 检查是否有 app.py 或 main.py
            app_file = None
            for f in ["app.py", "main.py", "wsgi.py"]:
                if os.path.exists(f):
                    app_file = f
                    break

            if not app_file:
                return f"错误: 目录中没有找到 Flask 应用文件 (app.py, main.py, wsgi.py)"

            cmd = [sys.executable, app_file]
            # 设置环境变量来指定端口
            env = os.environ.copy()
            env["FLASK_RUN_PORT"] = str(port)
            env["FLASK_APP"] = app_file

            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

        elif server_type == "fastapi":
            # 检查是否有 main.py
            if not os.path.exists("main.py"):
                return f"错误: 目录中没有找到 main.py (FastAPI 应用入口)"

            cmd = [sys.executable, "-m", "uvicorn", "main:app", f"--port={port}", "--host=0.0.0.0"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

        elif server_type == "streamlit":
            # 检查是否有 app.py
            if not os.path.exists("app.py"):
                return f"错误: 目录中没有找到 app.py (Streamlit 应用入口)"

            cmd = [sys.executable, "-m", "streamlit", "run", "app.py", f"--server.port={port}"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

        else:
            return f"错误: 不支持的 server_type: {server_type}。支持: http, flask, fastapi, streamlit"

        # 等待一小段时间确保服务器启动
        time.sleep(0.5)

        # 检查进程是否启动成功
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            return f"启动服务器失败: {stderr.decode('utf-8', errors='ignore')}"

        # 存储进程引用
        _running_servers[server_key] = process

        url = f"http://localhost:{port}"
        message = f"""✅ 服务器已启动成功！
类型: {server_type}
端口: {port}
目录: {os.path.abspath(directory)}
访问 URL: {url}

可以使用 open_in_browser 工具打开浏览器预览。"""

        return message

    except Exception as e:
        return f"启动服务器时出错: {e}"
    finally:
        os.chdir(original_dir)


@tool
def stop_preview_server(port: int, directory: str = ".") -> str:
    """停止指定端口和目录的预览服务器。

    Args:
        port: 服务器端口
        directory: 服务器根目录

    Returns:
        停止结果
    """
    global _running_servers

    server_key = f"{port}:{directory}"
    if server_key not in _running_servers:
        return f"端口 {port} 上没有运行的服务器"

    process = _running_servers[server_key]

    if os.name == 'nt':  # Windows
        # 强制终止进程组
        process.kill()
    else:  # Unix
        # 发送 SIGTERM
        process.terminate()

    # 等待进程结束
    process.wait(timeout=5)

    del _running_servers[server_key]
    return f"✅ 端口 {port} 上的服务器已停止"


@tool
def list_preview_servers() -> str:
    """列出所有正在运行的预览服务器。

    Returns:
        服务器列表
    """
    global _running_servers

    if not _running_servers:
        return "当前没有运行的服务器"

    lines = ["正在运行的服务器:"]
    for server_key, process in _running_servers.items():
        port, directory = server_key.split(":", 1)
        status = "运行中" if process.poll() is None else "已停止"
        lines.append(f"  - 端口 {port}, 目录 {directory}, 状态: {status}")

    return "\n".join(lines)


@tool
def preview_in_browser(url: str, auto_open: bool = True) -> str:
    """在内置预览面板或外部浏览器中打开指定 URL。

    Args:
        url: 要预览的 URL，例如 "http://localhost:8000" 或文件路径 "file:///path/to/index.html"
        auto_open: 是否自动在 UI 中打开预览面板（默认 True）

    Returns:
        预览状态信息
    """
    # 触发 UI 预览回调
    if auto_open:
        _trigger_preview_ui(url)

    return f"✅ 预览请求已发送: {url}\n预览面板将在代码编辑器区域显示。"


@tool
def open_in_external_browser(url: str) -> str:
    """在系统默认浏览器中打开 URL。

    Args:
        url: 要打开的 URL

    Returns:
        操作结果
    """
    import webbrowser

    try:
        webbrowser.open(url)
        return f"✅ 已在系统浏览器中打开: {url}"
    except Exception as e:
        return f"打开浏览器失败: {e}"