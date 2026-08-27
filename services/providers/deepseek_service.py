import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from services.providers.chrome_utils import get_chromedriver_service

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DS_UD = os.path.join(os.environ.get("LOCALAPPDATA", ""), "LLM_Agent", "deepseek_chrome")
_driver = None
_ready = False
_active_app_session = None
_storage = None


def _get_storage():
    global _storage
    if _storage is None:
        from services.core.storage_service import StorageService
        _storage = StorageService()
    return _storage


def _clean_locks(ud):
    for lock in ['SingletonLock', 'lockfile', os.path.join('Default', 'LOCK'), os.path.join('Default', 'SingletonLock')]:
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
    _clean_locks(DS_UD)
    if headless is None:
        headless = os.path.exists(os.path.join(DS_UD, 'Default', 'Cookies'))
    service = get_chromedriver_service()
    opts = Options()
    opts.binary_location = CHROME_PATH
    if headless:
        opts.add_argument('--headless=new')
    else:
        opts.add_argument('--start-maximized')
    opts.add_argument(f'--user-data-dir={DS_UD}')
    opts.add_argument('--profile-directory=Default')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-gpu')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    for attempt in range(3):
        try:
            _driver = webdriver.Chrome(service=service, options=opts)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
            _clean_locks(DS_UD)
    _driver.set_script_timeout(300)
    _ready = False
    return _driver


def _ensure_page():
    global _ready
    driver = _get_driver()
    if not _ready:
        driver.get('https://chat.deepseek.com/')
        time.sleep(5)
        for i in range(60):
            time.sleep(5)
            if '/sign_in' not in driver.current_url:
                break
        time.sleep(3)
        if '/sign_in' in driver.current_url:
            raise RuntimeError('DeepSeek login timeout')
        _ready = True
    return driver


def _get_chat_url(driver):
    url = driver.current_url.split('?')[0]
    if '/a/chat/' in url or '/chat/s/' in url:
        return url
    return ''


def _wait_for_textarea(driver, timeout=20):
    for _ in range(timeout * 2):
        ready = driver.execute_script('return !!document.querySelector("textarea");')
        if ready:
            return True
        time.sleep(0.5)
    return False


def _start_new_conversation(driver):
    clicked = driver.execute_script(
        'function clickNewChat(){'
        '  var selectors=["a[href=\\"/\\"]","button[class*=\\"new\\"]","[class*=\\"NewChat\\"]",'
        '"[class*=\\"new-chat\\"]","[data-testid*=\\"new\\"]"];'
        '  for(var i=0;i<selectors.length;i++){'
        '    var el=document.querySelector(selectors[i]);'
        '    if(el){el.click();return true;}'
        '  }'
        '  var nodes=document.querySelectorAll("button,a,span,div");'
        '  for(var j=0;j<nodes.length;j++){'
        '    var t=(nodes[j].innerText||nodes[j].textContent||"").trim();'
        '    if(t==="新对话"||t==="新建对话"||t==="New chat"||t==="New Chat"){nodes[j].click();return true;}'
        '  }'
        '  return false;'
        '}'
        'return clickNewChat();'
    )
    time.sleep(2)
    if not clicked or _get_chat_url(driver):
        driver.get('https://chat.deepseek.com/')
        time.sleep(3)
    _wait_for_textarea(driver)


def _switch_conversation(driver, url):
    if not url:
        _start_new_conversation(driver)
        return
    current = driver.current_url.split('?')[0]
    target = url.split('?')[0]
    if current != target:
        driver.get(target)
        time.sleep(3)
        _wait_for_textarea(driver)


def _ensure_app_conversation(driver, app_session_id):
    global _active_app_session
    if not app_session_id:
        return
    if app_session_id == _active_app_session:
        return
    row = _get_storage().get_provider_session(app_session_id, 'deepseek')
    url = row.get('external_id', '') if row else ''
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
        _get_storage().set_provider_session(app_session_id, 'deepseek', url)


def _send_via_js(driver, message):
    return driver.execute_script(
        'var msg=arguments[0];var ta=document.querySelector("textarea");'
        'if(!ta)return{err:"no textarea"};'
        'var ns=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,"value").set;'
        'ns.call(ta,msg);ta.dispatchEvent(new Event("input",{bubbles:true}));'
        'ta.dispatchEvent(new Event("change",{bubbles:true}));return{ok:true,val:ta.value};', message)


def _get_response_js(driver):
    """获取 AI 助手最新完整回复（从 DOM 还原 Markdown 结构）"""
    from services.providers.browser_markdown_extract import extract_deepseek_markdown
    return extract_deepseek_markdown(driver)


def _is_generating_js(driver):
    return driver.execute_script(
        'var s=document.querySelector("[class*=stop],button[class*=Stop],.ds-stop-btn");'
        'if(s&&s.offsetParent!==null)return true;'
        'var t=document.body.innerText||"";'
        'return t.includes("正在思考")||t.includes("正在生成");')


def _response_looks_incomplete(text):
    if not text:
        return True
    if '"tool"' not in text and '"write_file"' not in text:
        return False
    if text.count('{') > text.count('}'):
        return True
    if '"content"' in text:
        idx = text.find('"content"')
        tail = text[idx:]
        if tail.count('"') % 2 == 0 and tail.rstrip().endswith('}'):
            return False
        return True
    return False


def _wait_response(driver, prev_text='', timeout=180):
    """等待 DeepSeek 响应完成"""
    last_content = ''
    last_len = 0
    stable_len_count = 0
    current = ''
    for _ in range(timeout):
        time.sleep(0.5)
        if _is_generating_js(driver):
            stable_len_count = 0
            continue
        current = _get_response_js(driver)
        if not current or current == prev_text:
            continue
        current_len = len(current)
        if current_len != last_len:
            last_content = current
            last_len = current_len
            stable_len_count = 0
        else:
            stable_len_count += 1
            if stable_len_count >= 6 and current_len >= 20:
                if not _response_looks_incomplete(current):
                    return current
    return last_content if last_content else current


def deepseek_chat(
    messages,
    model='deepseek-chat',
    temperature=0.7,
    max_tokens=2048,
    stream=False,
    app_session_id=None,
    status_callback=None,
):
    driver = _ensure_page()
    _ensure_app_conversation(driver, app_session_id)

    from services.providers.browser_context import compose_browser_prompt_with_context
    prompt = compose_browser_prompt_with_context(
        messages, provider="deepseek", app_session_id=app_session_id,
        include_agent_tool_hint=True,
        status_callback=status_callback,
    )
    prev = _get_response_js(driver)
    result = _send_via_js(driver, prompt)
    if result.get('err'):
        raise RuntimeError(f'Input failed: {result["err"]}')
    time.sleep(0.5)
    try:
        ta = driver.find_element(By.TAG_NAME, 'textarea')
        ta.send_keys(Keys.ENTER)
    except Exception:
        driver.execute_script(
            'var ta=document.querySelector("textarea");'
            'if(ta)ta.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",code:"Enter",keyCode:13,which:13,bubbles:true}));'
        )
    reply = _wait_response(driver, prev_text=prev, timeout=120)
    _persist_conversation_url(driver, app_session_id)
    if stream:
        def _gen():
            yield reply
        return _gen()
    return {
        'id': f'chatcmpl-deepseek-{int(time.time())}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': reply}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': -1, 'completion_tokens': -1, 'total_tokens': -1},
    }


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
