import sys, os, json, time, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from services.providers.chrome_utils import get_chromedriver_service

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
MM_UD = os.path.join(os.environ.get("LOCALAPPDATA", ""), "LLM_Agent", "minimax_chrome")
AGENT_NAME = "413219134567483"
_driver = None
_ready = False
_active_app_session = None
_session_cache = {}
_storage = None


def _get_storage():
    global _storage
    if _storage is None:
        from services.core.storage_service import StorageService
        _storage = StorageService()
    return _storage


def _clean_locks(ud):
    for lock in ["SingletonLock", "lockfile", os.path.join("Default", "LOCK"), os.path.join("Default", "SingletonLock")]:
        try:
            os.remove(os.path.join(ud, lock))
        except Exception:
            pass


def _get_driver(headless=None):
    global _driver, _ready
    if _driver is not None:
        try:
            _driver.current_url
            return _driver
        except Exception:
            _driver = None
            _ready = False
    _clean_locks(MM_UD)
    time.sleep(2)
    if headless is None:
        headless = os.path.exists(os.path.join(MM_UD, "Default", "Cookies"))
    service = get_chromedriver_service()
    opts = Options()
    opts.binary_location = CHROME_PATH
    if headless:
        opts.add_argument("--headless=new")
    else:
        opts.add_argument("--start-maximized")
    opts.add_argument(f"--user-data-dir={MM_UD}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    _driver = webdriver.Chrome(service=service, options=opts)
    _driver.set_script_timeout(300)
    _ready = False
    return _driver


def _ensure_page():
    global _ready
    driver = _get_driver()
    if not _ready:
        driver.get("https://agent.minimaxi.com/")
        time.sleep(10)
        token = driver.execute_script("return localStorage.getItem('_token') || '';")
        if not token:
            print("[MiniMax] Please login in the browser window...")
            for i in range(60):
                time.sleep(5)
                token = driver.execute_script("return localStorage.getItem('_token') || '';")
                if token:
                    break
            if not token:
                raise RuntimeError("MiniMax login timeout")
        driver.execute_script(
            "var r=null;if(window.webpackChunk_N_E){window.webpackChunk_N_E.push([['__ax'],{},function(e){r=e}])}"
            "if(!r)throw new Error('webpack not found');var m=r(24622);if(!m||!m.ZP)throw new Error('axios not found');window.__axios=m.ZP;"
        )
        _ready = True
    return driver


def _create_minimax_session(driver, model_id):
    sess = driver.execute_async_script(
        "var cb=arguments[arguments.length-1];"
        "window.__axios.post('/archon/api/v1/agent/" + AGENT_NAME + "/session',{model:'minimax/" + model_id + "'})"
        ".then(function(r){cb(r.data)}).catch(function(e){cb({err:e.message})});"
    )
    if "err" in sess:
        raise RuntimeError(f"Session failed: {sess['err']}")
    sid = sess.get("session_id", "")
    if not sid:
        raise RuntimeError("Session failed: empty session_id")
    return sid


def _get_or_create_session(driver, app_session_id, model_id):
    global _active_app_session
    if app_session_id:
        if app_session_id != _active_app_session:
            _active_app_session = app_session_id
        cached = _session_cache.get(app_session_id)
        if cached:
            return cached
        row = _get_storage().get_provider_session(app_session_id, "minimax")
        if row and row.get("external_id"):
            sid = row["external_id"]
            _session_cache[app_session_id] = sid
            return sid
    sid = _create_minimax_session(driver, model_id)
    if app_session_id:
        _session_cache[app_session_id] = sid
        _get_storage().set_provider_session(app_session_id, "minimax", sid, model_id)
    return sid


def _invalidate_session(app_session_id):
    if not app_session_id:
        return
    _session_cache.pop(app_session_id, None)
    _get_storage().clear_provider_sessions(app_session_id, "minimax")


def _send_message(driver, sid, payload):
    return driver.execute_async_script(
        "var cb=arguments[arguments.length-1];var sid=arguments[0];var payload=arguments[1];"
        "window.__axios.post('https://agent-stream.minimaxi.com/archon/api/v1/session/'+sid+'/message',"
        "payload,{responseType:'text',timeout:180000})"
        ".then(function(r){cb({ok:true,data:r.data})}).catch(function(e){"
        "var msg=e.message||String(e);"
        "if(e.response){"
        "if(e.response.status)msg+=' (HTTP '+e.response.status+')';"
        "var d=e.response.data;"
        "if(typeof d==='string'&&d.length<400)msg+=' '+d;"
        "else if(d&&typeof d==='object'&&(d.message||d.error))msg+=' '+(d.message||d.error);"
        "}"
        "cb({err:msg});});",
        sid,
        payload,
    )


def minimax_chat(
    messages,
    model="MiniMax-M3",
    temperature=0.7,
    max_tokens=2048,
    stream=False,
    app_session_id=None,
    status_callback=None,
):
    driver = _ensure_page()
    from services.providers.browser_context import compose_browser_prompt_with_context
    user_msg = compose_browser_prompt_with_context(
        messages, provider="minimax", app_session_id=app_session_id,
        include_agent_tool_hint=True,
        status_callback=status_callback,
    )
    model_id = model.replace("minimax/", "")
    variant = "thinking" if "M3" in model_id else ""

    sid = _get_or_create_session(driver, app_session_id, model_id)
    payload = {
        "content": user_msg,
        "model": {"provider_id": "minimax", "model_id": model_id, "variant": variant},
        "turn_id": uuid.uuid4().hex,
        "enable_team": True,
        "worktreeMode": False,
    }
    result = _send_message(driver, sid, payload)
    if "err" in result and app_session_id:
        _invalidate_session(app_session_id)
        sid = _get_or_create_session(driver, app_session_id, model_id)
        result = _send_message(driver, sid, payload)
    if "err" in result:
        raise RuntimeError(f"Message failed: {result['err']}")

    raw = result.get("data", "")
    content = ""
    usage = {}
    for line in raw.split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            evt = json.loads(line[5:].strip())
            if evt.get("type") == 6:
                chunk = evt.get("agent_message_chunk", {})
                cc = chunk.get("msg_content", "")
                if cc:
                    content += cc
                if chunk.get("usage"):
                    usage = chunk["usage"]
            elif evt.get("type") == 2:
                msg = evt.get("agent_message", {})
                if msg.get("usage"):
                    usage = msg["usage"]
        except Exception:
            pass
    if stream:
        def _gen():
            yield content
        return _gen()
    return {
        "id": f"chatcmpl-mm-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", -1),
            "completion_tokens": usage.get("output_tokens", -1),
            "total_tokens": usage.get("total_tokens", -1),
        },
    }


def minimax_list_models():
    driver = _ensure_page()
    result = driver.execute_async_script(
        "var cb=arguments[arguments.length-1];window.__axios.get('/archon/api/v1/config')"
        ".then(function(r){cb(r.data)}).catch(function(e){cb({err:e.message})});"
    )
    return [] if "err" in result else result.get("models", [])


def close_browser():
    global _driver, _ready, _active_app_session
    if _driver:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None
        _ready = False
    _active_app_session = None
