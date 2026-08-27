"""
终端 / Conda 环境配置
支持在指定 Anaconda 虚拟环境中执行 run_command（尤其是 Python 脚本）
"""

import json
import os
import re
import subprocess
from typing import List, Optional, Tuple

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
CONFIG_PATH = os.path.join(_CONFIG_DIR, "terminal_config.json")

DEFAULT_CONFIG = {
    "conda_base": "",
    "conda_env": "",
    "auto_use_conda_for_python": True,
}

_config = dict(DEFAULT_CONFIG)


def _ensure_config_dir():
    os.makedirs(_CONFIG_DIR, exist_ok=True)


def detect_conda_base() -> str:
    """自动检测 Conda/Anaconda 安装路径"""
    conda_exe = os.environ.get("CONDA_EXE", "")
    if conda_exe and os.path.isfile(conda_exe):
        return os.path.dirname(os.path.dirname(os.path.normpath(conda_exe)))

    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        base = conda_prefix
        if os.path.basename(base).lower() == "envs":
            base = os.path.dirname(base)
        elif os.path.basename(base) not in ("", "base") and os.path.isdir(os.path.join(os.path.dirname(base), "envs")):
            pass
        else:
            parent = os.path.dirname(base)
            if os.path.isfile(os.path.join(parent, "Scripts", "conda.exe")):
                base = parent
        if os.path.isfile(os.path.join(base, "Scripts", "conda.exe")):
            return base

    candidates = [
        r"D:\Anaconda3",
        r"D:\ProgramData\anaconda3",
        os.path.expanduser(r"~\anaconda3"),
        os.path.expanduser(r"~\miniconda3"),
        os.path.expanduser(r"~\AppData\Local\anaconda3"),
        os.path.expanduser(r"~\AppData\Local\miniconda3"),
    ]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "Scripts", "conda.exe")):
            return path

    try:
        result = subprocess.run(
            ["where", "conda"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            conda_path = result.stdout.strip().splitlines()[0].strip()
            if os.path.isfile(conda_path):
                return os.path.dirname(os.path.dirname(os.path.normpath(conda_path)))
    except Exception:
        pass
    return ""


def get_conda_exe(conda_base: str) -> str:
    if not conda_base:
        return ""
    for parts in (("Scripts", "conda.exe"), ("condabin", "conda.bat")):
        path = os.path.join(conda_base, *parts)
        if os.path.isfile(path):
            return path
    return ""


def list_conda_envs(conda_base: str = "") -> List[str]:
    """列出 conda 环境名称"""
    base = conda_base or _config.get("conda_base") or detect_conda_base()
    envs = {"base"}
    if not base:
        return sorted(envs)

    envs_dir = os.path.join(base, "envs")
    if os.path.isdir(envs_dir):
        for name in os.listdir(envs_dir):
            if os.path.isdir(os.path.join(envs_dir, name)):
                envs.add(name)

    conda_exe = get_conda_exe(base)
    if conda_exe:
        try:
            result = subprocess.run(
                [conda_exe, "env", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                norm_base = os.path.normpath(base).lower()
                for item in data.get("envs", []):
                    item_norm = os.path.normpath(str(item).rstrip("/\\"))
                    if item_norm.lower() == norm_base:
                        envs.add("base")
                    else:
                        name = os.path.basename(item_norm)
                        if name:
                            envs.add(name)
        except Exception:
            pass
    return sorted(envs)


def list_conda_envs_fast(conda_base: str = "") -> List[str]:
    """快速列出 conda 环境（仅扫描目录，不调用 conda CLI，适合 UI 初始化）"""
    base = conda_base or _config.get("conda_base") or ""
    envs = {"base"}
    if not base or not os.path.isdir(base):
        return sorted(envs)
    envs_dir = os.path.join(base, "envs")
    if os.path.isdir(envs_dir):
        for name in os.listdir(envs_dir):
            if os.path.isdir(os.path.join(envs_dir, name)):
                envs.add(name)
    return sorted(envs)

def get_python_executable(conda_env: str = "", conda_base: str = "") -> str:
    """获取指定 conda 环境的 python.exe 路径"""
    base = conda_base or _config.get("conda_base") or detect_conda_base()
    env = conda_env or _config.get("conda_env") or "base"
    if not base:
        return ""
    if env == "base":
        path = os.path.join(base, "python.exe")
        return path if os.path.isfile(path) else ""
    path = os.path.join(base, "envs", env, "python.exe")
    return path if os.path.isfile(path) else ""


def load_config() -> dict:
    global _config
    _ensure_config_dir()
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _config = {**DEFAULT_CONFIG, **saved}
        except Exception:
            _config = dict(DEFAULT_CONFIG)
    else:
        _config = dict(DEFAULT_CONFIG)

    if not _config.get("conda_base"):
        pass  # 不在启动时自动检测，避免阻塞；由设置页「自动检测」触发
    return dict(_config)


def save_config(config: dict):
    global _config
    _ensure_config_dir()
    _config = {**DEFAULT_CONFIG, **config}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(_config, f, ensure_ascii=False, indent=4)


def get_config() -> dict:
    return dict(_config)


def _should_use_conda(command: str, explicit_env: str = "") -> bool:
    if explicit_env:
        return True
    if not _config.get("auto_use_conda_for_python", True):
        return False
    if not (_config.get("conda_env") or explicit_env):
        return False
    cmd = command.strip().lower()
    if cmd.startswith("python ") or cmd.startswith("py ") or cmd.startswith("python.exe"):
        return True
    if re.search(r"[\s\"']\.py(?:\s|$|\"|')", f" {command} ", re.I):
        return True
    if re.match(r"^[A-Za-z]:[\\/].+\.py$", command.strip()):
        return True
    return False


def _normalize_python_command(command: str) -> str:
    cmd = command.strip()
    if re.match(r"^[A-Za-z]:[\\/].+\.py$", cmd):
        return f'python "{cmd}"'
    if cmd.lower().endswith(".py") and not cmd.lower().startswith("python"):
        return f"python {cmd}"
    return command


def wrap_command_with_conda(command: str, conda_env: str = "") -> Tuple[str, str]:
    """
    若需要，将命令包装为 conda run 执行。
    返回 (final_command, env_label)
    """
    command = _normalize_python_command(command)
    env = (conda_env or _config.get("conda_env") or "").strip()
    if not _should_use_conda(command, conda_env):
        return command, ""

    conda_base = _config.get("conda_base") or detect_conda_base()
    if not conda_base:
        return command, ""

    conda_exe = get_conda_exe(conda_base)
    if not conda_exe:
        return command, ""

    if not env:
        env = "base"

    wrapped = f'"{conda_exe}" run -n {env} --no-capture-output cmd /c "chcp 65001 >nul && {command}"'
    return wrapped, env


# 启动时加载
load_config()
