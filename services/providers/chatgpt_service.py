import sys, os, time, subprocess, urllib.request, shutil, socket, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException
from services.providers.chrome_utils import get_chromedriver_service

from services.providers.browser_markdown_extract import (
    EXTRACT_MARKDOWN_FROM_ASSISTANT_JS as _EXTRACT_MARKDOWN_FROM_ASSISTANT_JS,
    normalize_browser_markdown as _normalize_chatgpt_markdown,
)

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_CHROME_USER_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
)
ISOLATED_USER_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "LLM_Agent", "chatgpt_chrome"
)
CHATGPT_URL = "https://chatgpt.com/"
DEFAULT_DEBUG_PORT = 9222
DEFAULT_LOGIN_WAIT_SECONDS = 60
NOT_LOGGED_IN_MSG = (
    "未检测到 ChatGPT 登录。请先点击「启动 ChatGPT Chrome」，"
    "在打开的专用窗口中登录 ChatGPT，并保持窗口打开后再发送消息。"
)
PORT_NOT_READY_MSG = (
    "ChatGPT Chrome 调试端口未能开启。"
    "请重试「启动 ChatGPT Chrome」；若仍失败，可在 config/ui_config.json 修改 chatgpt_debug_port（如 9333）。"
)

_driver = None
_ready = False
_active_app_session = None
_storage = None
_using_debug_attach = False


def _get_ui_config():
    try:
        import json
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
            "ui_config.json",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _get_debug_port():
    port = _get_ui_config().get("chatgpt_debug_port", DEFAULT_DEBUG_PORT)
    try:
        return int(port or DEFAULT_DEBUG_PORT)
    except (TypeError, ValueError):
        return DEFAULT_DEBUG_PORT


def _find_chrome():
    if os.path.isfile(CHROME_PATH):
        return CHROME_PATH
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _get_automation_user_data_dir():
    """自动化专用 Chrome 配置（独立目录，调试端口才能稳定开启）"""
    return ISOLATED_USER_DATA


def _get_chrome_user_data_dir():
    """兼容旧接口，自动化始终使用独立配置"""
    return _get_automation_user_data_dir()


def _get_desktop_profile_directory():
    """桌面 Chrome 当前使用的 Profile（用于同步登录 Cookie）"""
    manual = str(_get_ui_config().get("chatgpt_profile_directory", "") or "").strip()
    if manual:
        return manual
    local_state = os.path.join(DEFAULT_CHROME_USER_DATA, "Local State")
    if not os.path.isfile(local_state):
        return "Default"
    try:
        import json
        with open(local_state, "r", encoding="utf-8") as f:
            data = json.load(f)
        last_used = (data.get("profile") or {}).get("last_used")
        if last_used:
            return last_used
    except Exception:
        pass
    return "Default"


def _get_chrome_profile_directory():
    """自动化 Chrome 固定使用 Default 子配置"""
    return "Default"


def _chrome_profile_locked(user_data_dir):
    return os.path.exists(os.path.join(user_data_dir, "SingletonLock"))


def _count_chrome_processes():
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode("gbk", errors="ignore")
            return out.lower().count("chrome.exe")
        out = subprocess.check_output(["pgrep", "-c", "chrome"], stderr=subprocess.DEVNULL)
        return int(out.strip() or 0)
    except Exception:
        return -1


def _is_port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except OSError:
        return False


def _diagnose_debug_port_failure(port, just_launched=False):
    port_open = _debug_port_open(port)
    if port_open:
        return ""
    chrome_count = _count_chrome_processes()
    if just_launched and chrome_count > 0:
        in_use = _is_port_in_use(port)
        return (
            f"Chrome 已启动（{chrome_count} 个进程属正常现象），但调试端口 {port} 仍未就绪。"
            + (f"端口 {port} 已被占用但无法连接，请修改 chatgpt_debug_port。" if in_use else "")
            + " 请重试启动；若持续失败，尝试在 ui_config.json 将 chatgpt_debug_port 改为 9333。"
        )
    if _chrome_profile_locked(DEFAULT_CHROME_USER_DATA) and not _debug_port_open(port):
        return (
            f"桌面 Chrome 正在运行（无调试端口 {port}）。"
            "软件使用独立 Chrome 配置，可与桌面 Chrome 同时运行。"
            "请直接点击「启动 ChatGPT Chrome」，无需关闭桌面 Chrome。"
        )
    return (
        f"调试端口 {port} 未就绪。"
        "请先点击「启动 ChatGPT Chrome」，看到「调试端口已就绪」后再发送消息。"
    )


def _get_login_wait_seconds():
    wait = _get_ui_config().get("chatgpt_login_wait_seconds", DEFAULT_LOGIN_WAIT_SECONDS)
    try:
        wait = int(wait or DEFAULT_LOGIN_WAIT_SECONDS)
    except (TypeError, ValueError):
        wait = DEFAULT_LOGIN_WAIT_SECONDS
    return max(10, min(wait, 300))


def _debug_port_open(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _wait_for_debug_port(port, status_callback=None, timeout=None, just_launched=False):
    """等待 Chrome 调试端口就绪（启动 Chrome 后可能需要几秒）"""
    if timeout is None:
        timeout = _get_login_wait_seconds()
    if _debug_port_open(port):
        return True

    if not just_launched:
        hint = _diagnose_debug_port_failure(port, just_launched=False)
        if hint:
            _notify(status_callback, hint)

    _notify(status_callback, f"等待 ChatGPT Chrome 调试端口 {port} 就绪（最多 {timeout}s）...")
    for i in range(timeout):
        time.sleep(1)
        if _debug_port_open(port):
            _notify(status_callback, f"调试端口 {port} 已就绪。")
            return True
        if i in (0, 14, 29, 44, 59) or i == timeout - 1:
            _notify(status_callback, f"仍在等待调试端口... ({i + 1}/{timeout}s)")
    return False


def _get_storage():
    global _storage
    if _storage is None:
        from services.core.storage_service import StorageService
        _storage = StorageService()
    return _storage


def _clean_locks(ud):
    for lock in [
        "SingletonLock",
        "lockfile",
        os.path.join("Default", "LOCK"),
        os.path.join("Default", "SingletonLock"),
    ]:
        try:
            os.remove(os.path.join(ud, lock))
        except Exception:
            pass


def _copy_profile_auth_files(src_dir, dst_dir):
    copied = []
    if not os.path.isdir(src_dir):
        return copied
    os.makedirs(dst_dir, exist_ok=True)
    names = [
        "Cookies", "Login Data", "Web Data", "Preferences", "Secure Preferences",
    ]
    for name in names:
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        if not os.path.isfile(src):
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(name)
        except Exception:
            pass
    for folder in ("Local Storage", "Session Storage"):
        src = os.path.join(src_dir, folder)
        dst = os.path.join(dst_dir, folder)
        if os.path.isdir(src):
            try:
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
                copied.append(folder)
            except Exception:
                pass
    return copied


def _sync_desktop_login_to_automation(status_callback=None):
    """从桌面 Chrome 复制登录 Cookie（需所有 Chrome 已关闭）"""
    if not _get_ui_config().get("chatgpt_sync_desktop_login", True):
        return False
    if _count_chrome_processes() > 0:
        _notify(
            status_callback,
            "检测到 Chrome 正在运行，暂无法同步桌面登录；"
            "请在本窗口手动登录一次，或关闭所有 Chrome 后重试启动。",
        )
        return False
    src_profile = _get_desktop_profile_directory()
    src_dir = os.path.join(DEFAULT_CHROME_USER_DATA, src_profile)
    dst_dir = os.path.join(_get_automation_user_data_dir(), "Default")
    if not os.path.isdir(src_dir):
        return False
    copied = _copy_profile_auth_files(src_dir, dst_dir)
    if copied:
        _notify(
            status_callback,
            f"已从桌面 Chrome（{src_profile}）同步登录数据: {', '.join(copied)}",
        )
        return True
    return False


def _build_chrome_args(chrome, port, user_data, profile_dir, open_url=True):
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data}",
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-features=VizDisplayCompositor",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-extensions",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-dev-shm-usage",
        "--disable-ipc-flooding-protection",
        "--disable-blink-features=AutomationControlled",
    ]
    if open_url:
        args.append(CHATGPT_URL)
    return args


def _start_chrome_process(args):
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=(sys.platform != "win32"),
    )


def _reset_driver():
    global _driver, _ready, _active_app_session, _using_debug_attach
    if _driver and not _using_debug_attach:
        try:
            _driver.quit()
        except Exception:
            pass
    _driver = None
    _ready = False
    _active_app_session = None
    _using_debug_attach = False


def _attach_debug_driver():
    global _driver, _using_debug_attach
    port = _get_debug_port()
    if not _debug_port_open(port):
        raise RuntimeError(
            f"Chrome 调试端口 {port} 未就绪。请先点击「启动 ChatGPT Chrome」并在其中手动登录。"
        )
    service = get_chromedriver_service()
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    opts.add_argument("--remote-allow-origins=*")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(service=service, options=opts)
    _driver = driver
    _using_debug_attach = True
    driver.set_script_timeout(60)
    driver.set_page_load_timeout(90)
    _switch_to_chatgpt_tab(driver)
    return driver


def _get_driver(create=True):
    """仅通过 Chrome 调试端口接入（不在 Selenium 中启动浏览器，避免人机验证）"""
    global _driver
    if _driver is not None and _is_session_alive(_driver):
        return _driver
    if not create:
        return None
    return _attach_debug_driver()


def launch_chrome_for_login(status_callback=None):
    """启动带调试端口的 ChatGPT 专用 Chrome（独立配置，可与桌面 Chrome 并存）"""
    global _ready
    port = _get_debug_port()
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Google Chrome，请先安装 Chrome 浏览器。")

    user_data = _get_automation_user_data_dir()
    profile_dir = _get_chrome_profile_directory()

    if _debug_port_open(port):
        _notify(status_callback, f"ChatGPT Chrome 调试端口 {port} 已开启，可直接发送消息。")
        return None

    os.makedirs(user_data, exist_ok=True)
    if not _chrome_profile_locked(user_data):
        _sync_desktop_login_to_automation(status_callback)
    _clean_locks(user_data)

    args = _build_chrome_args(chrome, port, user_data, profile_dir)
    _notify(status_callback, f"正在启动 ChatGPT 专用 Chrome（端口 {port}）...")
    _start_chrome_process(args)
    _ready = False

    if not _wait_for_debug_port(
        port, status_callback=status_callback, timeout=45, just_launched=True
    ):
        detail = _diagnose_debug_port_failure(port, just_launched=True)
        raise RuntimeError(detail or PORT_NOT_READY_MSG)

    _notify(
        status_callback,
        f"ChatGPT Chrome 已就绪（调试端口 {port}）。"
        "若未登录请在打开的窗口登录一次（之后会自动保留）；"
        "可与桌面 Chrome 同时使用。请保持该窗口打开后再发送消息。",
    )
    return None


def _is_session_alive(driver):
    if driver is None:
        return False
    try:
        _ = driver.current_url
        driver.execute_script("return 1")
        return True
    except Exception:
        return False


def _notify(status_callback, message):
    if status_callback and message:
        try:
            status_callback(message)
        except Exception:
            pass


def _list_debug_pages(port=None):
    if port is None:
        port = _get_debug_port()
    try:
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as resp:
            items = json.loads(resp.read().decode())
        return [t for t in items if t.get("type") == "page"]
    except Exception:
        return []


def _is_logged_in_cookies(driver):
    """HttpOnly 登录 Cookie 无法被页面 JS 读取，需用 CDP / Selenium Cookie API"""
    try:
        for c in driver.get_cookies():
            name = c.get("name", "")
            if "session-token" in name and c.get("value"):
                return True
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        for url in ("https://chatgpt.com", "https://www.chatgpt.com"):
            result = driver.execute_cdp_cmd("Network.getCookies", {"urls": [url]})
            for c in result.get("cookies", []):
                name = c.get("name", "")
                if "session-token" in name and c.get("value"):
                    return True
    except Exception:
        pass
    return False


def _is_logged_in_js(driver):
    try:
        return driver.execute_script(
            "(function(){"
            "var href=(location.href||'').toLowerCase();"
            "if(href.indexOf('/auth')>=0||/\\/login\\b/.test(href))return false;"
            "if(/chatgpt\\.com\\/c\\/[0-9a-f-]+/.test(href))return true;"
            "try{"
            "  var ck=document.cookie||'';"
            "  if(ck.indexOf('__Secure-next-auth.session-token')>=0)return true;"
            "}catch(e){}"
            "var sels=["
            "'#prompt-textarea',"
            "'textarea#prompt-textarea',"
            "'[data-testid=\"prompt-textarea\"]',"
            "'[data-testid=\"composer-textarea\"]',"
            "'div[contenteditable=\"true\"][role=\"textbox\"]',"
            "'div[contenteditable=\"true\"][data-placeholder]',"
            "'div.ProseMirror[contenteditable=\"true\"]',"
            "'div.ProseMirror',"
            "'button[data-testid=\"send-button\"]',"
            "'button[data-testid=\"composer-send-button\"]',"
            "'[data-testid=\"profile-button\"]',"
            "'button[aria-label*=\"Profile\"]',"
            "'button[aria-label*=\"Open Profile\"]'"
            "];"
            "for(var i=0;i<sels.length;i++){"
            "  if(document.querySelector(sels[i]))return true;"
            "}"
            "var nodes=document.querySelectorAll('button,a');"
            "for(var j=0;j<nodes.length;j++){"
            "  var t=(nodes[j].innerText||nodes[j].textContent||'').trim();"
            "  if(/^log in$/i.test(t)||t==='登录'||/^sign up$/i.test(t)){"
            "    var r=nodes[j].getBoundingClientRect();"
            "    if(r.width>0&&r.height>0&&r.top<200)return false;"
            "  }"
            "}"
            "if(href.indexOf('chatgpt.com')>=0&&document.body){"
            "  var body=(document.body.innerText||'').slice(0,4000);"
            "  if(body.indexOf('What can I help')>=0||body.indexOf('有什么可以帮忙')>=0"
            "     ||body.indexOf('Ask anything')>=0||body.indexOf('Message ChatGPT')>=0"
            "     ||body.indexOf('New chat')>=0||body.indexOf('新对话')>=0)return true;"
            "}"
            "if(href.indexOf('chatgpt.com')>=0&&href.indexOf('/auth')<0){"
            "  var main=document.querySelector('main');"
            "  if(main)return true;"
            "}"
            "return false;"
            "})();"
        )
    except WebDriverException:
        return False


def _is_logged_in(driver):
    if _is_logged_in_cookies(driver):
        return True
    return _is_logged_in_js(driver)


def _switch_to_chatgpt_tab(driver, status_callback=None):
    """切换到 chatgpt.com 标签页；登录等待由 _check_driver_logged_in 负责"""
    chatgpt_pages = [
        p for p in _list_debug_pages()
        if "chatgpt.com" in (p.get("url") or "").lower()
        and "/auth" not in (p.get("url") or "").lower()
    ]

    try:
        handles = list(driver.window_handles)
    except WebDriverException:
        handles = []

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = (driver.current_url or "").lower()
            if "chatgpt.com" not in url or "/auth" in url:
                continue
            if _is_logged_in(driver):
                _notify(status_callback, "已连接到 ChatGPT 标签页。")
                return True
        except WebDriverException:
            continue

    for page in chatgpt_pages:
        target = (page.get("url") or "").split("#")[0].strip()
        if not target:
            continue
        try:
            driver.get(target)
            time.sleep(2)
            if _is_logged_in(driver):
                _notify(status_callback, "已切换到 ChatGPT 标签页。")
                return True
        except WebDriverException:
            continue

    _notify(status_callback, "正在打开 chatgpt.com ...")
    try:
        driver.get(CHATGPT_URL)
    except WebDriverException:
        pass
    time.sleep(3)
    return _is_logged_in(driver)


def _navigate_chatgpt(driver, status_callback=None):
    _switch_to_chatgpt_tab(driver, status_callback=status_callback)


def check_logged_in():
    """检查当前 ChatGPT 浏览器是否已登录（不自动打开新窗口）"""
    port = _get_debug_port()
    if not _wait_for_debug_port(port):
        return False
    driver = _get_driver(create=True)
    if not driver:
        return False
    try:
        wait_seconds = _get_login_wait_seconds()
        _navigate_chatgpt(driver)
        for i in range(wait_seconds):
            if _is_logged_in(driver):
                return True
            url = driver.current_url or ""
            if "__cf_chl" not in url and "challenge" not in url.lower() and i >= 10:
                break
            time.sleep(1)
        return _is_logged_in(driver)
    except Exception:
        return False


def open_browser_for_manual_login(status_callback=None):
    """启动真实 Chrome 供用户手动登录（兼容旧接口名）"""
    return launch_chrome_for_login(status_callback=status_callback)


def _check_driver_logged_in(driver, status_callback=None, wait_seconds=None):
    if wait_seconds is None:
        wait_seconds = _get_login_wait_seconds()
    _switch_to_chatgpt_tab(driver, status_callback=status_callback)
    if _is_logged_in(driver):
        return True
    for i in range(wait_seconds):
        if _is_logged_in(driver):
            return True
        url = driver.current_url or ""
        if "__cf_chl" in url or "challenge" in url.lower():
            if i in (0, 14, 29, 44, 59):
                _notify(status_callback, f"等待 Cloudflare 验证... ({i + 1}/{wait_seconds}s)")
            time.sleep(1)
            continue
        if i > 0 and i % 15 == 0:
            _switch_to_chatgpt_tab(driver)
        if i in (0, 14, 29, 44, 59) or i == wait_seconds - 1:
            _notify(status_callback, f"等待 ChatGPT 登录状态... ({i + 1}/{wait_seconds}s)")
        time.sleep(1)
    ok = _is_logged_in(driver)
    if not ok:
        try:
            details = driver.execute_script(
                "return {url:location.href||'', title:document.title||'',"
                "hasPrompt:!!document.querySelector('#prompt-textarea,[data-testid=\"prompt-textarea\"],div.ProseMirror'),"
                "hasLogin:!!Array.from(document.querySelectorAll('button,a')).find(function(n){"
                "var t=(n.innerText||'').trim();return /^log in$/i.test(t)||t==='登录';})};"
            ) or {}
            url = str(details.get("url", ""))[:100]
            title = str(details.get("title", ""))[:60]
            _notify(
                status_callback,
                f"登录检测失败: url={url} title={title} "
                f"prompt={details.get('hasPrompt')} loginBtn={details.get('hasLogin')} "
                f"sessionCookie={_is_logged_in_cookies(driver)}",
            )
        except Exception:
            try:
                url = (driver.current_url or "")[:100]
                _notify(status_callback, f"登录检测失败，当前页面: {url}")
            except Exception:
                pass
    return ok


def _ensure_page(status_callback=None):
    """通过调试端口接入已登录的 Chrome，不在 Selenium 中重新打开浏览器"""
    global _ready
    _notify(status_callback, "正在连接 ChatGPT Chrome...")

    port = _get_debug_port()
    if not _wait_for_debug_port(port, status_callback=status_callback):
        detail = _diagnose_debug_port_failure(port)
        msg = detail or PORT_NOT_READY_MSG
        _notify(status_callback, msg)
        raise RuntimeError(msg)

    try:
        if _driver is not None and _is_session_alive(_driver):
            driver = _driver
        else:
            driver = _get_driver(create=True)
    except Exception as e:
        _notify(status_callback, str(e))
        raise RuntimeError(str(e)) from e

    if _check_driver_logged_in(driver, status_callback):
        _ready = True
        _notify(status_callback, "ChatGPT 已登录，开始对话...")
        return driver

    _notify(status_callback, NOT_LOGGED_IN_MSG)
    raise RuntimeError(NOT_LOGGED_IN_MSG)


def _get_chat_url(driver):
    url = driver.current_url.split("?")[0]
    if "/c/" in url:
        return url
    return ""


def _wait_for_input(driver, timeout=20):
    for _ in range(timeout * 2):
        if _is_logged_in(driver):
            return True
        time.sleep(0.5)
    return False


def _start_new_conversation(driver):
    clicked = driver.execute_script(
        'function clickNewChat(){'
        '  var selectors=['
        '"a[href=\\"/\\"]",'
        '"button[data-testid=\\"create-new-chat-button\\"]",'
        '"[data-testid=\\"create-new-chat-button\\"]",'
        '"button[aria-label*=\\"New chat\\"]"'
        '];'
        '  for(var i=0;i<selectors.length;i++){'
        '    var el=document.querySelector(selectors[i]);'
        '    if(el){el.click();return true;}'
        '  }'
        '  var nodes=document.querySelectorAll("button,a,span,div");'
        '  for(var j=0;j<nodes.length;j++){'
        '    var t=(nodes[j].innerText||nodes[j].textContent||"").trim();'
        '    if(t==="New chat"||t==="新对话"||t==="新建对话"){nodes[j].click();return true;}'
        '  }'
        '  return false;'
        '}'
        'return clickNewChat();'
    )
    time.sleep(2)
    if not clicked or _get_chat_url(driver):
        driver.get(CHATGPT_URL)
        time.sleep(3)
    _wait_for_input(driver)


def _switch_conversation(driver, url):
    if not url:
        _start_new_conversation(driver)
        return
    current = driver.current_url.split("?")[0]
    target = url.split("?")[0]
    if current != target:
        driver.get(target)
        time.sleep(3)
        _wait_for_input(driver)


def _ensure_app_conversation(driver, app_session_id):
    global _active_app_session
    if not app_session_id:
        return
    if app_session_id == _active_app_session:
        return
    row = _get_storage().get_provider_session(app_session_id, "chatgpt")
    url = row.get("external_id", "") if row else ""
    if url:
        _switch_conversation(driver, url)
    else:
        _start_new_conversation(driver)
    _active_app_session = app_session_id


def _persist_conversation_url(driver, app_session_id):
    if not app_session_id:
        return
    url = _get_chat_url(driver)
    if url:
        _get_storage().set_provider_session(app_session_id, "chatgpt", url)


def _find_input(driver):
    """查找 ChatGPT 输入框，优先匹配 contenteditable/ProseMirror（当前 UI 使用），
    避免匹配到隐藏的 fallback textarea。"""
    selectors = [
        'div.ProseMirror[contenteditable="true"]',
        'div[contenteditable="true"]',
        'div.ProseMirror',
        '[data-testid="composer-textarea"]',
        '#prompt-textarea:not([style*="display: none"])',
        '[data-testid="prompt-textarea"]',
    ]
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed():
                    return el
        except Exception:
            pass
    return None


def _fill_prompt_js(driver, message):
    """ChatGPT 输入框多为 ProseMirror/contenteditable，send_keys 遇换行会截断"""
    try:
        return driver.execute_script(
            """
            var msg = arguments[0] || '';
            var selectors = [
                'div.ProseMirror[contenteditable="true"]',
                'div[contenteditable="true"]',
                'div.ProseMirror',
                '#prompt-textarea',
                '[data-testid="prompt-textarea"]',
                'textarea'
            ];
            var el = null;
            for (var i = 0; i < selectors.length; i++) {
                var cand = document.querySelector(selectors[i]);
                if (cand) { el = cand; break; }
            }
            if (!el) return {err: 'no input'};

            el.focus();

            if (el.tagName === 'TEXTAREA') {
                var desc = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value');
                if (desc && desc.set) desc.set.call(el, msg);
                else el.value = msg;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return {ok: true, len: (el.value || '').length};
            }

            el.textContent = '';
            if (document.execCommand) {
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, msg);
            } else {
                el.textContent = msg;
            }
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true, inputType: 'insertText', data: msg
            }));
            var got = (el.innerText || el.textContent || '').trim();
            return {ok: true, len: got.length};
            """,
            message,
        )
    except Exception as e:
        return {"err": str(e)}


def _send_message(driver, message):
    el = _find_input(driver)
    if not el:
        return {"err": "no input"}
    try:
        # 使用 JS click 避免 Selenium click 被 placeholder/overlay 拦截
        driver.execute_script("arguments[0].focus();", el)
        time.sleep(0.2)
        result = _fill_prompt_js(driver, message)
        if result.get("err"):
            return result
        got_len = int(result.get("len") or 0)
        expect_len = len(message.strip())
        if expect_len > 0 and got_len < max(20, int(expect_len * 0.5)):
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.BACKSPACE)
            time.sleep(0.1)
            for line in message.split("\n"):
                if line:
                    el.send_keys(line)
                el.send_keys(Keys.SHIFT, Keys.ENTER)
            result = _fill_prompt_js(driver, message)
        time.sleep(0.5)
        return result if result.get("ok") else {"err": result.get("err", "fill failed")}
    except Exception as e:
        return {"err": str(e)}


def _click_send(driver):
    try:
        btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="send-button"]'))
        )
        btn.click()
        return True
    except Exception:
        pass
    for _ in range(8):
        sent = driver.execute_script(
            'var btn=document.querySelector("button[data-testid=\\"send-button\\"]");'
            'if(btn&&!btn.disabled){btn.click();return "clicked";}'
            'return btn ? "disabled" : "missing";'
        )
        if sent == "clicked":
            return True
        time.sleep(0.3)
    el = _find_input(driver)
    if el:
        el.send_keys(Keys.ENTER)
        return True
    return False


def _poll_state_light(driver):
    """轻量轮询：只检测生成状态和文本长度，不执行昂贵的 Markdown 提取。
    避免在等待响应期间反复运行 extractMarkdownFromAssistant（clone + DOM 操作）
    导致 ChromeDriver 原生层崩溃（0xC0000409）。"""
    try:
        return driver.execute_script(
            'var stop=document.querySelector("button[data-testid=\\"stop-button\\"]");'
            'var loading=document.querySelector("[data-testid=\\"conversation-turn-response-loading\\"]");'
            'var msgs=document.querySelectorAll("[data-message-author-role=\\"assistant\\"]");'
            'var textLen=0;'
            'if(msgs.length){'
            '  var last=msgs[msgs.length-1];'
            '  textLen=(last.innerText||last.textContent||"").length;'
            '}'
            'return {'
            '  generating:!!(stop&&stop.offsetParent)||!!(loading&&loading.offsetParent),'
            '  users:document.querySelectorAll("[data-message-author-role=\\"user\\"]").length,'
            '  assistants:msgs.length,'
            '  textLen:textLen'
            '};'
        )
    except Exception:
        return {"generating": False, "users": 0, "assistants": 0, "textLen": 0}


def _extract_full_markdown(driver):
    """仅在响应稳定后调用一次，执行完整的 Markdown 提取。"""
    try:
        raw = driver.execute_script(
            _EXTRACT_MARKDOWN_FROM_ASSISTANT_JS
            + 'var msgs=document.querySelectorAll("[data-message-author-role=\\"assistant\\"]");'
            'if(msgs.length){'
            '  return extractMarkdownFromAssistant(msgs[msgs.length-1]);'
            '}'
            'return "";'
        )
        return _normalize_chatgpt_markdown(raw or "")
    except Exception as e:
        return ""


def _poll_state(driver):
    """完整轮询（含 Markdown 提取），仅供 _get_response_js 复用。"""
    try:
        return driver.execute_script(
            _EXTRACT_MARKDOWN_FROM_ASSISTANT_JS
            + 'var stop=document.querySelector("button[data-testid=\\"stop-button\\"]");'
            'var loading=document.querySelector("[data-testid=\\"conversation-turn-response-loading\\"]");'
            'var msgs=document.querySelectorAll("[data-message-author-role=\\"assistant\\"]");'
            'var last="";'
            'if(msgs.length){'
            '  last=extractMarkdownFromAssistant(msgs[msgs.length-1]);'
            '}'
            'return {'
            '  generating:!!(stop&&stop.offsetParent)||!!(loading&&loading.offsetParent),'
            '  users:document.querySelectorAll("[data-message-author-role=\\"user\\"]").length,'
            '  assistants:msgs.length,'
            '  reply:last'
            '};'
        )
    except Exception:
        return {"generating": False, "users": 0, "assistants": 0, "reply": ""}


def _get_response_js(driver):
    """获取上次回复文本（执行完整 Markdown 提取）。"""
    return _poll_state(driver).get("reply", "")


def _get_state_before_send(driver):
    """发送消息前获取状态快照（轻量轮询，不提取 Markdown）。"""
    return _poll_state_light(driver)


def _response_looks_incomplete(text):
    if not text:
        return True
    if '"tool"' not in text and '"write_file"' not in text:
        return False
    if text.count("{") > text.count("}"):
        return True
    if '"content"' in text:
        idx = text.find('"content"')
        tail = text[idx:]
        if tail.count('"') % 2 == 0 and tail.rstrip().endswith("}"):
            return False
        return True
    return False


def _wait_response(driver, prev_text="", user_count_before=0, assistant_count_before=0, timeout=180):
    """轮询等待 ChatGPT 响应完成，使用轻量轮询避免 ChromeDriver 崩溃。"""
    last_len = 0
    stable_len_count = 0
    saw_generating = False
    for _ in range(timeout):
        time.sleep(0.5)
        if not _is_session_alive(driver):
            raise RuntimeError("ChatGPT 浏览器已关闭，请重新登录后再试")

        state = _poll_state_light(driver)
        if state.get("generating"):
            saw_generating = True
            stable_len_count = 0
            continue

        current_len = state.get("textLen", 0)
        user_count = state.get("users", 0)
        assistant_count = state.get("assistants", 0)
        sent_ok = user_count > user_count_before or assistant_count > assistant_count_before
        if not sent_ok and not saw_generating:
            continue

        if current_len == 0 or (current_len == last_len and not saw_generating):
            continue

        if current_len != last_len:
            last_len = current_len
            stable_len_count = 0
        else:
            stable_len_count += 1
            min_len = 1 if saw_generating else 3
            if stable_len_count >= 4 and current_len >= min_len:
                # 仅在确认稳定后执行一次完整的 Markdown 提取
                result = _extract_full_markdown(driver)
                if result and not _response_looks_incomplete(result):
                    return result
                # 如果提取失败或内容不完整，重置计数继续等
                stable_len_count = 0

    # 超时：最后尝试一次提取
    result = _extract_full_markdown(driver)
    return result


def chatgpt_chat(
    messages,
    model="gpt-4o",
    temperature=0.7,
    max_tokens=2048,
    stream=False,
    app_session_id=None,
    status_callback=None,
):
    last_error = None
    for attempt in range(2):
        try:
            driver = _ensure_page(status_callback=status_callback)
            _ensure_app_conversation(driver, app_session_id)

            from services.providers.browser_context import compose_browser_prompt_with_context
            prompt = compose_browser_prompt_with_context(
                messages, provider="chatgpt", app_session_id=app_session_id,
                include_agent_tool_hint=True,
                status_callback=status_callback,
            )
            state_before = _get_state_before_send(driver)
            user_count_before = state_before.get("users", 0)
            assistant_count_before = state_before.get("assistants", 0)
            result = _send_message(driver, prompt)
            if result.get("err"):
                raise RuntimeError(f"Input failed: {result['err']}")
            time.sleep(0.5)
            if not _click_send(driver):
                raise RuntimeError("Send failed: could not click send button")
            reply = _wait_response(
                driver,
                user_count_before=user_count_before,
                assistant_count_before=assistant_count_before,
                timeout=180,
            )
            _persist_conversation_url(driver, app_session_id)
            if stream:
                def _gen():
                    yield reply
                return _gen()
            return {
                "id": f"chatcmpl-chatgpt-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
            }
        except WebDriverException as e:
            last_error = e
            _reset_driver()
            if attempt == 0:
                _notify(status_callback, "ChatGPT 浏览器连接断开，正在重新连接...")
                continue
            raise RuntimeError(f"ChatGPT 浏览器异常: {e}") from e
        except RuntimeError:
            raise
    raise RuntimeError(f"ChatGPT 调用失败: {last_error}")


def close_browser():
    _reset_driver()
