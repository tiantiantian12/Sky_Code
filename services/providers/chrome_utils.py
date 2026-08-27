"""ChromeDriver 路径解析工具，带多层回退。

解决 `webdriver_manager` 在无法访问 Google 服务器时直接崩溃的问题。
策略：webdriver_manager（网络） → 缓存目录 → PATH → 已知固定路径。
"""

import os
import sys
import glob
import logging
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)

# 已知的固定路径（Windows 常见安装位置）
_KNOWN_PATHS = [
    r"D:\Program Files\Python310\Scripts\chromedriver.exe",
    r"C:\Program Files\chromedriver\chromedriver.exe",
    r"C:\chromedriver\chromedriver.exe",
]


def _find_in_wdm_cache():
    """在 webdriver_manager 缓存中查找已有的 chromedriver。"""
    home = os.path.expanduser("~")
    cache_dirs = [
        os.path.join(home, ".wdm", "drivers", "chromedriver"),
        os.path.join(home, ".cache", "selenium", "chromedriver"),
    ]
    for base in cache_dirs:
        if not os.path.isdir(base):
            continue
        # 递归查找 chromedriver.exe
        for root, dirs, files in os.walk(base):
            for f in files:
                if f.lower() in ("chromedriver.exe", "chromedriver"):
                    return os.path.join(root, f)
    return None


def _find_in_path():
    """在系统 PATH 中查找 chromedriver。"""
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, "chromedriver.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_in_known_paths():
    """在已知固定路径中查找 chromedriver。"""
    for p in _KNOWN_PATHS:
        if os.path.isfile(p):
            return p
    return None


def get_chromedriver_service():
    """获取 ChromeDriver Service 对象。

    按优先级尝试获取 chromedriver：
    1. webdriver_manager 自动下载（需要网络访问 Google 服务器）
    2. webdriver_manager 缓存中已有的版本
    3. 系统 PATH 中的 chromedriver
    4. 已知固定路径
    """
    # 策略 1：尝试 webdriver_manager
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        path = ChromeDriverManager().install()
        if path and os.path.exists(path):
            logger.info(f"ChromeDriver (webdriver_manager): {path}")
            return Service(path)
    except Exception as e:
        logger.warning(f"webdriver_manager 获取失败（可能网络不通）: {e}")

    # 策略 2：检查 webdriver_manager 缓存
    cached = _find_in_wdm_cache()
    if cached:
        logger.info(f"ChromeDriver (wdm cache): {cached}")
        return Service(cached)

    # 策略 3：尝试直接使用 Service 不带参数（依赖 PATH）
    try:
        s = Service()
        logger.info("ChromeDriver: 使用 PATH 中的 chromedriver")
        return s
    except Exception:
        pass

    # 策略 4：PATH 中搜索
    path_chrome = _find_in_path()
    if path_chrome:
        logger.info(f"ChromeDriver (PATH): {path_chrome}")
        return Service(path_chrome)

    # 策略 5：已知固定路径
    known = _find_in_known_paths()
    if known:
        logger.info(f"ChromeDriver (known path): {known}")
        return Service(known)

    raise RuntimeError(
        "未找到 ChromeDriver。请确保满足以下条件之一：\n"
        "1. 可以访问 Google 服务器（webdriver_manager 自动下载）\n"
        "2. 将 chromedriver.exe 所在目录添加到 PATH 环境变量\n"
        "3. 将 chromedriver.exe 放在以下位置之一：\n" +
        "\n".join(f"   - {p}" for p in _KNOWN_PATHS)
    )
