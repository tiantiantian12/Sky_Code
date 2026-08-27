import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from services.providers.chrome_utils import get_chromedriver_service

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
KIMI_UD = os.path.join(os.environ.get("LOCALAPPDATA", ""), "LLM_Agent", "kimi_chrome")
KIMI_URL = "https://kimi.moonshot.cn/"
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
    _clean_locks(KIMI_UD)
    if headless is None:
        headless = os.path.exists(os.path.join(KIMI_UD, 'Default', 'Cookies'))
    service = get_chromedriver_service()
    opts = Options()
    opts.binary_location = CHROME_PATH
    if headless:
        opts.add_argument('--headless=new')
    else:
        opts.add_argument('--start-maximized')
    opts.add_argument(f'--user-data-dir={KIMI_UD}')
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
            _clean_locks(KIMI_UD)
    _driver.set_script_timeout(300)
    _ready = False
    return _driver


LOGIN_CHECK_JS = "return !!document.querySelector('textarea,[contenteditable=\"true\"],div.chat-input-editor');"


def _ensure_page():
    global _ready
    driver = _get_driver()
    if not _ready:
        driver.get(KIMI_URL)
        time.sleep(5)
        for i in range(60):
            time.sleep(5)
            # Kimi 登录页通常包含 /login 或 /signin
            url_lower = driver.current_url.lower()
            if '/login' not in url_lower and '/signin' not in url_lower and 'chat/' in url_lower:
                break
            # 也有可能在首页未登录状态显示登录按钮
            is_logged = driver.execute_script(LOGIN_CHECK_JS)
            if is_logged:
                break
        time.sleep(3)
        # 最终检查
        url_lower = driver.current_url.lower()
        is_logged = driver.execute_script(LOGIN_CHECK_JS)
        if not is_logged and ('/login' in url_lower or '/signin' in url_lower):
            print("[Kimi] Please login in the browser window...")
            for i in range(60):
                time.sleep(5)
                url_lower = driver.current_url.lower()
                if '/login' not in url_lower and '/signin' not in url_lower:
                    break
            time.sleep(3)
            is_logged = driver.execute_script(LOGIN_CHECK_JS)
            if not is_logged:
                raise RuntimeError('Kimi login timeout')
        _ready = True
    return driver


def _get_chat_url(driver):
    url = driver.current_url.split('?')[0]
    if '/chat/' in url or '/c/' in url:
        return url
    return ''


def _wait_for_textarea(driver, timeout=20):
    for _ in range(timeout * 2):
        ready = driver.execute_script(LOGIN_CHECK_JS)
        if ready:
            return True
        time.sleep(0.5)
    return False


SAFE_CLICK_JS = """\
function safeClick(el){
  if(!el)return false;
  if(typeof el.click==="function"){el.click();return true;}
  try{el.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true}));return true;}
  catch(e){}
  return false;
}
"""

NEW_CHAT_JS = SAFE_CLICK_JS + """\
function clickNewChat(){
  var selectors=[
    '[data-testid*="new"]',
    'button[class*="new"]',
    '[class*="NewChat"]',
    '[class*="new-chat"]',
    '[class*="newChat"]',
    'a[href*="/chat"]',
  ];
  for(var i=0;i<selectors.length;i++){
    var el=document.querySelector(selectors[i]);
    if(el&&safeClick(el))return true;
  }
  var nodes=document.querySelectorAll('button,a,span,div');
  for(var j=0;j<nodes.length;j++){
    var t=(nodes[j].innerText||nodes[j].textContent||'').trim();
    if(t==='新对话'||t==='新建对话'||t==='新会话'||t==='New chat'||t==='新任务'){
      if(safeClick(nodes[j]))return true;
    }
  }
  return false;
}
return clickNewChat();
"""

THINKING_JS = SAFE_CLICK_JS + """\
function enableThinking(){
  // Kimi K2.6 思考和快速模式的切换按钮可能在多个位置：
  // 1) 输入框上方的模式切换按钮
  // 2) 顶部模型选择器中的思考/快速选项
  
  // 策略1: 搜索包含"长思考"/"深度思考"/"思考模式"的按钮或标签
  var thinkKeywords = [
    '长思考', '深度思考', '思考模式', '深度探索',
    'K2思考', 'K2.6思考', '深度推理', '慢速思考',
    'thinking', 'reasoning', 'deep think'
  ];
  var nodes = document.querySelectorAll('button,a,span,div,label,[role="button"],[role="switch"]');
  
  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i];
    if (n.offsetParent === null || n.getBoundingClientRect().width === 0) continue;
    var t = (n.innerText || n.textContent || '').trim();
    var aria = (n.getAttribute('aria-label') || '').trim();
    var full = (t + ' ' + aria).toLowerCase();
    
    for (var k = 0; k < thinkKeywords.length; k++) {
      var kw = thinkKeywords[k];
      if (t === kw || t.indexOf(kw) >= 0 || full.indexOf(kw.toLowerCase()) >= 0) {
        // 如果当前已经是思考模式（可能有 active/selected 状态），跳过
        var cls = (n.className || n.getAttribute('class') || '').toString().toLowerCase();
        if (cls.indexOf('active') >= 0 || cls.indexOf('selected') >= 0 || cls.indexOf('checked') >= 0) {
          return true; // 已经启用
        }
        if (safeClick(n)) return true;
      }
    }
  }
  
  // 策略2: 找 model selector 下拉框（可能包含 K2.6 或 "快速思考" 字样）
  var modelSelector = document.querySelector('[class*="model-selector"], [class*="ModelSelector"], [class*="model-switch"]');
  if (!modelSelector) {
    // 搜索包含"快速思考"或"快速"的可点击元素（说明当前是快速模式，需要切换）
    for (var j = 0; j < nodes.length; j++) {
      var mn = nodes[j];
      if (mn.offsetParent === null) continue;
      var mt = (mn.innerText || mn.textContent || '').trim();
      if (mt === '快速思考' || mt === '快速' || mt === '即时回答' || mt === '即时') {
        // 点击它打开下拉菜单
        if (safeClick(mn)) {
          // 等待下拉菜单出现，然后找"长思考"选项
          var _findThink = function() {
            var all = document.querySelectorAll('*');
            for (var a = 0; a < all.length; a++) {
              var at = (all[a].innerText || all[a].textContent || '').trim();
              if (at === '长思考' || at === '深度思考') {
                if (all[a].offsetParent !== null && all[a].getBoundingClientRect().width > 0) {
                  return safeClick(all[a]);
                }
              }
            }
            return false;
          };
          // 立即尝试
          if (_findThink()) return true;
          // 短暂延迟后再试（等待下拉动画）
          for (var d = 0; d < 20; d++) {
            // 用 setTimeout 做不到同步等待，改为直接重复尝试
            if (_findThink()) return true;
          }
        }
        break;
      }
    }
  }
  
  // 策略3: 查找 Swipe/Switch 类型的切换开关（Kimi 可能用开关切换思考模式）
  var toggles = document.querySelectorAll('[role="switch"], [class*="toggle"], [class*="switch"]');
  for (var s = 0; s < toggles.length; s++) {
    var tg = toggles[s];
    var parent = tg.parentElement;
    // 检查周围 3 级父元素内是否有"思考"相关文字
    for (var level = 0; level < 4; level++) {
      if (!parent) break;
      var pt = (parent.innerText || parent.textContent || '').trim();
      if (pt.indexOf('长思考') >= 0 || pt.indexOf('深度思考') >= 0 || pt.indexOf('深度推理') >= 0) {
        if (safeClick(tg)) return true;
      }
      parent = parent.parentElement;
    }
  }
  
  // 策略4: 搜索"K2.6"并查看附近是否有思考/快速切换
  var k26Els = document.querySelectorAll('[class*="k2"], [class*="K2"]');
  for (var x = 0; x < k26Els.length; x++) {
    var k26 = k26Els[x];
    var tt = (k26.innerText || k26.textContent || '').trim();
    if (tt.indexOf('K2') >= 0 && (tt.indexOf('思考') >= 0 || tt.indexOf('快速') >= 0)) {
      if (tt.indexOf('思考') >= 0 && tt.indexOf('快速') < 0) {
        // 已经是思考模式
        return true;
      }
      // 点击切换
      if (safeClick(k26)) return true;
    }
  }
  
  return false;
}
return enableThinking();
"""

SEND_BTN_JS = SAFE_CLICK_JS + """\
// 找到 Kimi 实际的发送按钮: div.send-button-container
// 注：Kimi 不使用 <button> 标签，发送按钮是 div.send-button-container
var sendDiv=document.querySelector('div.send-button-container');
if(sendDiv){
  // 确保移除 disabled 状态
  sendDiv.classList.remove('disabled');
  // 点击发送
  return safeClick(sendDiv);
}
// 回退：找输入框(可能是 contenteditable 或 textarea)
var input=document.querySelector('div.chat-input-editor[contenteditable]') || document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
if(input){
  // 完整模拟 Enter 键序列
  input.focus();
  ['keydown','keypress','keyup'].forEach(function(type){
    input.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}));
  });
  return true;
}
// 最后的回退：找任何 svg.send-icon 的父级可点击元素
var svg=document.querySelector('svg.send-icon,svg[name="Send"]');
if(svg){
  var p=svg;
  for(var i=0;i<5;i++){
    p=p.parentElement;
    if(!p)break;
    p.classList.remove('disabled');
    if(safeClick(p))return true;
  }
}
return false;
"""


def _start_new_conversation(driver):
    """在 Kimi 中开始新对话"""
    clicked = driver.execute_script(NEW_CHAT_JS)
    time.sleep(2)
    if not clicked or _get_chat_url(driver):
        driver.get(KIMI_URL)
        time.sleep(3)
    _wait_for_textarea(driver)


def _enable_deep_thinking(driver):
    """启用 Kimi K2.6 深度思考模式"""
    result = driver.execute_script(THINKING_JS)
    if result:
        print("[Kimi] 已启用深度思考/长思考模式")
        # 等待 UI 更新（切换模式可能需要加载新界面）
        time.sleep(2.0)
        _wait_for_textarea(driver, timeout=10)
    else:
        print("[Kimi] 未找到思考模式切换按钮，使用默认模式")
    return result


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
    row = _get_storage().get_provider_session(app_session_id, 'kimi')
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
        _get_storage().set_provider_session(app_session_id, 'kimi', url)


FILL_INPUT_JS = """\
var msg=arguments[0];
// Kimi 使用 contenteditable div.chat-input-editor，不是 textarea
var el=document.querySelector('div.chat-input-editor[contenteditable]') || document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
if(!el)return{err:'no input element'};
el.focus();
if(el.tagName==='TEXTAREA'){
  var ns=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
  ns.call(el,msg);
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
}else{
  // contenteditable div - 使用 innerText 触发 Vue 响应式 setter
  el.innerHTML='';
  // 尝试方式1: innerText (触发浏览器原生 setter)
  try{el.innerText=msg;}catch(e){el.textContent=msg;}
  // 方式2: 用 <p> 包裹（Kimi 可能期望这种格式）
  if(!el.innerText||el.innerText.trim()===el.textContent.trim()){
    el.innerHTML='<p>'+msg.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'</p><p>')+'</p>';
  }
  // 移动光标到末尾
  var sel=window.getSelection();
  var range=document.createRange();
  if(el.lastChild){range.setStartAfter(el.lastChild);range.collapse(true);}
  sel.removeAllRanges();
  sel.addRange(range);
  // 触发多种事件确保 Vue 感知到变化
  el.dispatchEvent(new InputEvent('input',{bubbles:true,cancelable:true,inputType:'insertText',data:msg}));
  el.dispatchEvent(new CompositionEvent('compositionend',{data:msg,bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  // 移除发送按钮的 disabled 状态
  var sendDiv=document.querySelector('div.send-button-container');
  if(sendDiv)sendDiv.classList.remove('disabled');
}
return{ok:true};
"""


def _fill_input_js(driver, message):
    """通过 JS 填充 Kimi 输入框（支持 textarea 和 contenteditable）"""
    return driver.execute_script(FILL_INPUT_JS, message)


def _has_markdown_syntax(text):
    """检查文本是否包含 Markdown 语法标记"""
    if not text:
        return False
    import re
    markers = [
        r'```',
        r'^#{1,6}\s',
        r'\*\*[^*]+\*\*',
        r'^[*-]\s',
        r'^\d+\.\s',
        r'\|.*\|.*\|',
        r'^>\s',
        r'`[^`]+`',
        r'!\[.*\]\(.*\)',
        r'\[.*\]\(.*\)',
        r'^---',
    ]
    for m in markers:
        if re.search(m, text, re.MULTILINE):
            return True
    return False


# ─── 策略1: 通过剪贴板获取原始 Markdown ───
# 注入 JS hook 拦截 clipboard 写入，然后点击复制按钮
KIMI_HOOK_CLIPBOARD_JS = """
window.__copiedMarkdown = '';
if (!window.__clipboardHooked) {
  window.__clipboardHooked = true;
  // hook navigator.clipboard.writeText
  var origWrite = navigator.clipboard.writeText.bind(navigator.clipboard);
  navigator.clipboard.writeText = function(text) {
    window.__copiedMarkdown = text;
    return origWrite(text);
  };
  // hook document.execCommand('copy')
  var origExec = document.execCommand.bind(document);
  document.execCommand = function(cmd) {
    if (cmd === 'copy') {
      var sel = window.getSelection();
      if (sel && sel.toString()) {
        window.__copiedMarkdown = sel.toString();
      }
    }
    return origExec.apply(document, arguments);
  };
}
window.__copiedMarkdown = '';
"""

KIMI_CLICK_COPY_JS = """
// 查找最新 assistant 消息的复制按钮并点击
function findAndClickCopyBtn(){
  // 策略1: 查找所有复制按钮，取最后一个（通常属于最新回复）
  var copyBtns = document.querySelectorAll(
    '[class*="copy"]:not([class*="code"]):not(input):not(textarea),'
    +' [class*="Copy"]:not([class*="code"]),'
    +' button[aria-label*="copy" i], button[aria-label*="复制"],'
    +' [class*="action"] [class*="copy"],'
    +' [class*="toolbar"] [class*="copy"]'
  );
  // 过滤：只取可见的按钮
  var visible = [];
  copyBtns.forEach(function(btn) {
    if (btn.offsetParent !== null && btn.getBoundingClientRect().width > 0) {
      visible.push(btn);
    }
  });
  if (visible.length > 0) {
    var btn = visible[visible.length - 1];
    btn.click();
    return true;
  }
  // 策略2: 在最后一个 assistant 消息容器内查找复制按钮
  var sel = [
    '[class*="assistant"]:not([class*="user"])',
    '[class*="answer"]',
    '[class*="response"]',
    '[class*="markdown"]'
  ];
  for (var i = 0; i < sel.length; i++) {
    var nodes = document.querySelectorAll(sel[i]);
    if (nodes.length) {
      var last = nodes[nodes.length - 1];
      var btn = last.querySelector('[class*="copy"], [class*="Copy"], button[aria-label*="copy" i]');
      if (btn) { btn.click(); return true; }
      // 查找父级中的复制按钮
      var parent = last.parentElement;
      for (var p = 0; p < 5 && parent; p++) {
        btn = parent.querySelector('[class*="copy"], [class*="Copy"]');
        if (btn && btn.offsetParent !== null) { btn.click(); return true; }
        parent = parent.parentElement;
      }
    }
  }
  return false;
}
return findAndClickCopyBtn();
"""

KIMI_READ_CLIPBOARD_JS = "return window.__copiedMarkdown || '';"


def _get_markdown_via_clipboard(driver):
    """通过点击复制按钮获取原始 Markdown"""
    try:
        # 1. 注入 clipboard hook
        driver.execute_script(KIMI_HOOK_CLIPBOARD_JS)
        # 2. 点击复制按钮
        clicked = driver.execute_script(KIMI_CLICK_COPY_JS)
        if not clicked:
            return ""
        # 3. 等待复制完成
        time.sleep(0.3)
        # 4. 读取捕获的 Markdown
        md = driver.execute_script(KIMI_READ_CLIPBOARD_JS)
        return md or ""
    except Exception:
        return ""


# ─── 策略2: 获取 innerHTML 并在 Python 中转 Markdown ───
KIMI_GET_INNERHTML_JS = """
function getLastKimiAssistantForHTML(){
  var sel=[
    '[class*="kimi-answer"]',
    '[class*="chat-bubble"]:not([class*="user"])',
    '[class*="bubble"]:not([class*="user"])',
    '[class*="reply"]',
    '[class*="assistant"]:not([class*="user"])',
    '[class*="answer"]',
    '[class*="response"]',
    '[class*="markdown"]'
  ];
  for(var i=0;i<sel.length;i++){
    try{
      var nodes=document.querySelectorAll(sel[i]);
      if(nodes.length) return nodes[nodes.length-1];
    }catch(e){}
  }
  return null;
}
var node = getLastKimiAssistantForHTML();
if (!node) return '';
// 找到 markdown 渲染容器
var el = node.querySelector('.markdown,[class*="markdown"]') || node;
var clone = el.cloneNode(true);
// 移除思考区域
var thinkingSelectors=['[class*="think"]','[class*="Think"]','[class*="reasoning"]','[class*="Reasoning"]','[class*="thought"]','[class*="Thought"]','[class*="collapse-think"]','[class*="thinking-process"]'];
for(var si=0;si<thinkingSelectors.length;si++){
  try{
    clone.querySelectorAll(thinkingSelectors[si]).forEach(function(e){
      if(!e.querySelector('[class*="markdown"]')) e.remove();
    });
  }catch(e){}
}
// 移除按钮等噪声
clone.querySelectorAll('button, [role="button"], [class*="toolbar"], [class*="copy"]').forEach(function(e){ e.remove(); });
return clone.innerHTML || '';
"""


def _html_to_markdown(html_str):
    """将 HTML 字符串转换为 Markdown（Python 端处理，更灵活）"""
    import re as _re

    if not html_str or not html_str.strip():
        return ""

    text = html_str

    # 移除 script/style
    text = _re.sub(r'<script[^>]*>.*?</script>', '', text, flags=_DOTALL | _IGNORECASE)
    text = _re.sub(r'<style[^>]*>.*?</style>', '', text, flags=_DOTALL | _IGNORECASE)

    # 处理代码块 <pre>...</pre>
    def _pre_repl(m):
        inner = m.group(1)
        # 提取语言
        lang = ''
        lang_m = _re.search(r'class="[^"]*language-(\w+)', inner)
        if lang_m:
            lang = lang_m.group(1)
        # 移除内部 HTML 标签
        code = _re.sub(r'<[^>]+>', '', inner)
        code = _html_unescape(code)
        # 清理行号
        code = _re.sub(r'^\s*\d+\s', '', code, flags=_MULTILINE)
        return f'\n```{lang}\n{code.strip()}\n```\n'

    text = _re.sub(r'<pre[^>]*>(.*?)</pre>', _pre_repl, text, flags=_DOTALL | _IGNORECASE)

    # 处理表格
    def _table_repl(m):
        table_html = m.group(0)
        rows = _re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, _DOTALL)
        if not rows:
            return ''
        lines = []
        col_count = 0
        for idx, row in enumerate(rows):
            cells = _re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, _DOTALL)
            cells = [_re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            cells = [_html_unescape(c) for c in cells]
            if idx == 0:
                col_count = len(cells)
            lines.append('| ' + ' | '.join(cells) + ' |')
            if idx == 0:
                lines.append('| ' + ' | '.join(['---'] * col_count) + ' |')
        return '\n' + '\n'.join(lines) + '\n'

    text = _re.sub(r'<table[^>]*>.*?</table>', _table_repl, text, flags=_DOTALL | _IGNORECASE)

    # 引用块
    def _bq_repl(m):
        inner = _re.sub(r'<[^>]+>', '', m.group(1)).strip()
        lines = inner.split('\n')
        return '\n' + '\n'.join('> ' + l for l in lines) + '\n'
    text = _re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', _bq_repl, text, flags=_DOTALL | _IGNORECASE)

    # 标题
    for lv in range(6, 0, -1):
        text = _re.sub(
            rf'<h{lv}[^>]*>(.*?)</h{lv}>',
            lambda m, l=lv: '\n' + '#' * l + ' ' + _re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n',
            text, flags=_DOTALL | _IGNORECASE
        )

    # 水平线
    text = _re.sub(r'<hr[^>]*/?>', '\n---\n', text, flags=_IGNORECASE)

    # 列表
    def _ul_repl(m):
        items = _re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), _DOTALL)
        return '\n' + '\n'.join('- ' + _re.sub(r'<[^>]+>', '', item).strip() for item in items) + '\n'
    text = _re.sub(r'<ul[^>]*>(.*?)</ul>', _ul_repl, text, flags=_DOTALL | _IGNORECASE)

    def _ol_repl(m):
        items = _re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), _DOTALL)
        return '\n' + '\n'.join(f'{i+1}. ' + _re.sub(r'<[^>]+>', '', item).strip() for i, item in enumerate(items)) + '\n'
    text = _re.sub(r'<ol[^>]*>(.*?)</ol>', _ol_repl, text, flags=_DOTALL | _IGNORECASE)

    # 图片
    text = _re.sub(r'<img[^>]*alt="([^"]*)"[^>]*src="([^"]*)"[^>]*/?>', r'![\1](\2)', text, flags=_IGNORECASE)
    text = _re.sub(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?>', r'![\2](\1)', text, flags=_IGNORECASE)
    text = _re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?>', r'![](\1)', text, flags=_IGNORECASE)

    # 链接
    text = _re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', lambda m: f'[{_re.sub(r"<[^>]+>", "", m.group(2)).strip()}]({m.group(1)})', text, flags=_DOTALL | _IGNORECASE)

    # 行内代码
    text = _re.sub(r'<code[^>]*>(.*?)</code>', lambda m: '`' + _re.sub(r'<[^>]+>', '', m.group(1)).strip() + '`', text, flags=_DOTALL | _IGNORECASE)

    # 粗体
    text = _re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', lambda m: '**' + _re.sub(r'<[^>]+>', '', m.group(1)).strip() + '**', text, flags=_DOTALL | _IGNORECASE)

    # 斜体
    text = _re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', lambda m: '*' + _re.sub(r'<[^>]+>', '', m.group(1)).strip() + '*', text, flags=_DOTALL | _IGNORECASE)

    # 删除线
    text = _re.sub(r'<(?:del|s|strike)[^>]*>(.*?)</(?:del|s|strike)>', lambda m: '~~' + _re.sub(r'<[^>]+>', '', m.group(1)).strip() + '~~', text, flags=_DOTALL | _IGNORECASE)

    # <br> → 换行
    text = _re.sub(r'<br\s*/?>', '\n', text, flags=_IGNORECASE)

    # <p> → 换行
    text = _re.sub(r'<p[^>]*>', '\n', text, flags=_IGNORECASE)
    text = _re.sub(r'</p>', '\n', text, flags=_IGNORECASE)

    # <div> → 换行
    text = _re.sub(r'<div[^>]*>', '\n', text, flags=_IGNORECASE)
    text = _re.sub(r'</div>', '', text, flags=_IGNORECASE)

    # 移除剩余标签
    text = _re.sub(r'<[^>]+>', '', text)

    # HTML 反转义
    text = _html_unescape(text)

    # 清理多余空行
    text = _re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


import re as _re_mod
import html as _html_mod
_DOTALL = _re_mod.DOTALL
_IGNORECASE = _re_mod.IGNORECASE
_MULTILINE = _re_mod.MULTILINE
_html_unescape = _html_mod.unescape


def _get_markdown_via_innerhtml(driver):
    """获取 assistant 节点的 innerHTML 并在 Python 中转为 Markdown"""
    try:
        html_str = driver.execute_script(KIMI_GET_INNERHTML_JS)
        if not html_str or not html_str.strip():
            return ""
        md = _html_to_markdown(html_str)
        return md
    except Exception:
        return ""


# ─── 策略3: Vue 组件状态提取 ───
KIMI_TRY_RAW_MARKDOWN_JS = """
function tryGetRawMarkdown(){
  // 策略1: 查找 Vue 应用根节点
  var app = document.querySelector('#app');
  if (app && app.__vue_app__) {
    // Vue 3: 遍历组件树
    var root = app.__vue_app__._instance;
    if (root) {
      var found = '';
      function walk(instance) {
        if (found) return;
        var state = instance.setupState || {};
        var props = instance.props || {};
        // 检查各种属性名
        var keys = ['source', 'content', 'markdown', 'rawContent', 'text', 'value', 'raw', 'rawText', 'msg', 'message', 'answer', 'reply'];
        for (var i = 0; i < keys.length; i++) {
          var v = state[keys[i]] || props[keys[i]];
          if (typeof v === 'string' && v.length > 20) {
            if (v.indexOf('**') >= 0 || v.indexOf('```') >= 0 || v.indexOf('# ') >= 0 || v.indexOf('\\n#') >= 0) {
              found = v;
              return;
            }
          }
        }
        // 递归子组件
        var children = instance.subTree && instance.subTree.children;
        if (children) {
          for (var j = 0; j < children.length; j++) {
            if (children[j].component) walk(children[j].component);
            if (found) return;
          }
        }
      }
      walk(root);
      if (found) return found;
    }
  }
  // 策略2: 查找带 __vueParentComponent 的元素
  var mdContainers = document.querySelectorAll(
    '[class*="markdown"], [class*="answer"], [class*="response"], [class*="content"]'
  );
  for (var i = mdContainers.length - 1; i >= 0; i--) {
    var el = mdContainers[i];
    var comp = el.__vueParentComponent;
    if (comp) {
      var state = comp.setupState || {};
      var props = comp.props || {};
      var keys = ['source', 'content', 'markdown', 'rawContent', 'text', 'value'];
      for (var k = 0; k < keys.length; k++) {
        var v = state[keys[k]] || props[keys[k]];
        if (typeof v === 'string' && v.length > 20) {
          if (v.indexOf('**') >= 0 || v.indexOf('```') >= 0 || v.indexOf('# ') >= 0) {
            return v;
          }
        }
      }
    }
  }
  // 策略3: 查找 data 属性
  var dataEls = document.querySelectorAll('[data-source], [data-raw], [data-markdown], [data-content]');
  for (var j = dataEls.length - 1; j >= 0; j--) {
    var raw = dataEls[j].getAttribute('data-source')
      || dataEls[j].getAttribute('data-raw')
      || dataEls[j].getAttribute('data-markdown')
      || dataEls[j].getAttribute('data-content');
    if (raw && raw.length > 20) return raw;
  }
  return '';
}
return tryGetRawMarkdown();
"""


def _get_last_assistant_js(driver, fast=False):
    """获取 Kimi 最新 assistant 回复节点的 Markdown — 多策略提取

    fast=True 时跳过剪贴板和 Vue 策略（轮询时使用，避免频繁点击复制按钮）。
    """
    from services.providers.browser_markdown_extract import (
        EXTRACT_MARKDOWN_FROM_ASSISTANT_JS, KIMI_LAST_ASSISTANT_JS,
        normalize_browser_markdown,
    )

    # ── 策略0: 标准 DOM 提取（JS 端 HTML→Markdown） ──
    try:
        raw = driver.execute_script(
            EXTRACT_MARKDOWN_FROM_ASSISTANT_JS + KIMI_LAST_ASSISTANT_JS
        )
    except Exception:
        raw = ""

    if _has_markdown_syntax(raw):
        return normalize_browser_markdown(raw)

    if not fast:
        print(f"[Kimi] 策略0(DOM提取) 无 Markdown 语法, raw_len={len(raw or '')}")

    # ── 策略1: 剪贴板提取（点击复制按钮）— fast 模式跳过 ──
    if not fast:
        try:
            clip_md = _get_markdown_via_clipboard(driver)
            if clip_md and _has_markdown_syntax(clip_md):
                print(f"[Kimi] 策略1(剪贴板) 成功, len={len(clip_md)}")
                return normalize_browser_markdown(clip_md)
        except Exception:
            pass

    # ── 策略2: innerHTML 提取（Python 端 HTML→Markdown） ──
    try:
        html_md = _get_markdown_via_innerhtml(driver)
        if html_md and _has_markdown_syntax(html_md):
            if not fast:
                print(f"[Kimi] 策略2(innerHTML) 成功, len={len(html_md)}")
            return normalize_browser_markdown(html_md)
    except Exception:
        pass

    # ── 策略3: Vue 组件状态提取 — fast 模式跳过 ──
    if not fast:
        try:
            vue_raw = driver.execute_script(KIMI_TRY_RAW_MARKDOWN_JS)
            if vue_raw and _has_markdown_syntax(vue_raw):
                print(f"[Kimi] 策略3(Vue状态) 成功, len={len(vue_raw)}")
                return normalize_browser_markdown(vue_raw)
        except Exception:
            pass

    # ── 兜底: 使用已有结果（即使没有 Markdown 语法） ──
    if raw and raw.strip():
        return normalize_browser_markdown(raw)

    # ── 最终回退: 原始 innerText ──
    try:
        raw = driver.execute_script("""
            var areas = document.querySelectorAll(
                '[class*="answer"],[class*="response"],'
                +'[class*="assistant"]:not([class*="user"]),'
                +'[class*="content"] [class*="text"]'
            );
            for (var i = areas.length-1; i >= 0; i--) {
                var t = (areas[i].innerText || areas[i].textContent || '').trim();
                if (t && t.length > 10) return t;
            }
            var body = document.body.innerText || '';
            var inputArea = document.querySelector(
                'div.chat-input-editor, [contenteditable="true"], textarea'
            );
            if (inputArea) {
                var inputText = inputArea.innerText || inputArea.textContent || inputArea.value || '';
                if (inputText && body.endsWith(inputText)) {
                    body = body.substring(0, body.length - inputText.length).trim();
                }
            }
            return body;
        """)
    except Exception:
        pass

    return normalize_browser_markdown(raw or "")


IS_GENERATING_JS = """\
// 精准判断 Kimi 是否仍在生成回复（避免静态 UI 文本误判）
// 策略1: 找生成中的停止按钮（仅在聊天响应容器内）
var respContainers = document.querySelectorAll(
  '[class*="chat"] [class*="stop"],'
  +'[class*="message"] [class*="stop"],'
  +'[class*="response"] [class*="stop"],'
  +'[class*="answer"] [class*="stop"]'
);
for (var i = 0; i < respContainers.length; i++) {
  var s = respContainers[i];
  if (s.offsetParent !== null && s.getBoundingClientRect().width > 0) return true;
}

// 策略2: 找可见的 loading spinner（限于响应区域）
var spinners = document.querySelectorAll(
  '[class*="chat"] [class*="spinner"],'
  +'[class*="message"] [class*="spinner"],'
  +'[class*="response"] [class*="spinner"],'
  +'[class*="chat"] [class*="loading"],'
  +'[class*="message"] [class*="loading"]'
);
for (var j = 0; j < spinners.length; j++) {
  var sp = spinners[j];
  if (sp.offsetParent !== null && sp.getBoundingClientRect().width > 0) return true;
}

// 策略3: 检测页面底部是否出现"正在思考"/"正在生成"（限最新回复附近）
var bubbles = document.querySelectorAll(
  '[class*="assistant"]:not([class*="user"]),'
  +'[class*="Assistant"]:not([class*="User"]),'
  +'[class*="answer"],'
  +'[class*="response"],'
  +'[class*="markdown"]'
);
for (var k = bubbles.length - 1; k >= Math.max(0, bubbles.length - 3); k--) {
  var t = (bubbles[k].innerText || bubbles[k].textContent || '');
  if (t.includes('正在思考') || t.includes('正在生成') || t.includes('思考中') || t.includes('搜索中'))
    return true;
}

// 策略4: 检查是否有"停止生成"按钮（非静态 UI stop 按钮）
var allButtons = document.querySelectorAll('button, [role="button"]');
for (var m = 0; m < allButtons.length; m++) {
  var btn = allButtons[m];
  if (btn.offsetParent === null || btn.getBoundingClientRect().width === 0) continue;
  var label = (btn.getAttribute('aria-label') || '').toLowerCase();
  var text = (btn.innerText || btn.textContent || '').trim();
  if (label.indexOf('stop') >= 0 || label.indexOf('停止') >= 0 || text === '停止生成' || text === '停止') {
    return true;
  }
}

// 未检测到生成中
return false;
"""


def _is_generating_js(driver):
    """判断 Kimi 是否还在生成回复"""
    return driver.execute_script(IS_GENERATING_JS)


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


def _wait_response_stream(driver, prev_text='', timeout=240):
    """等待 Kimi 响应完成 — 生成器版本，轮询时 yield 部分内容实现真流式。

    yield (kind, text) 元组：
      kind='partial' — 生成中的部分内容（全量文本）
      kind='final'   — 完成后的最终文本
    """
    last_content = ''
    last_len = 0
    stable_len_count = 0
    generating_streak = 0
    current = ''
    for i in range(timeout):
        time.sleep(0.5)
        is_gen = _is_generating_js(driver)
        if is_gen:
            generating_streak += 1
            stable_len_count = 0
            # 生成中：每 2 秒尝试提取一次部分内容
            if generating_streak % 4 == 0:
                current = _get_last_assistant_js(driver, fast=True)
                if current and current != prev_text and len(current) > last_len:
                    last_content = current
                    last_len = len(current)
                    yield ('partial', current)
            continue
        # 不在生成中：尝试提取完整回复
        generating_streak = 0
        current = _get_last_assistant_js(driver)
        if not current or current == prev_text:
            if i > 30 and last_content:
                yield ('final', last_content)
                return
            continue
        current_len = len(current)
        if current_len != last_len:
            last_content = current
            last_len = current_len
            stable_len_count = 0
            yield ('partial', current)
        else:
            stable_len_count += 1
            if stable_len_count >= 6 and current_len >= 20:
                if not _response_looks_incomplete(current):
                    yield ('final', current)
                    return
    # 超时回退
    if not last_content and not current:
        try:
            raw = driver.execute_script("""
                var areas = document.querySelectorAll('[class*="answer"],[class*="response"],[class*="assistant"],[class*="markdown"]');
                for (var i = areas.length-1; i >= 0; i--) {
                    var t = (areas[i].innerText || areas[i].textContent || '').trim();
                    if (t && t.length > 20) return t;
                }
                return '';
            """)
            if raw:
                yield ('final', raw)
                return
        except Exception:
            pass
    yield ('final', last_content if last_content else current)


def kimi_chat(
    messages,
    model='kimi-k2-thinking',
    temperature=0.7,
    max_tokens=2048,
    stream=False,
    app_session_id=None,
    status_callback=None,
):
    driver = _ensure_page()
    _ensure_app_conversation(driver, app_session_id)

    # 启用深度思考 K2.6 模式
    _enable_deep_thinking(driver)

    from services.providers.browser_context import compose_browser_prompt_with_context
    prompt = compose_browser_prompt_with_context(
        messages, provider="kimi", app_session_id=app_session_id,
        include_agent_tool_hint=True,
        status_callback=status_callback,
    )
    prev = _get_last_assistant_js(driver)
    print(f"[Kimi] 填充前 prev_text 长度: {len(prev)}")
    result = _fill_input_js(driver, prompt)
    if result.get('err'):
        raise RuntimeError(f'Input failed: {result["err"]}')
    # 等 Vue 处理完 fill 事件后再发送
    time.sleep(1.0)
    sent = driver.execute_script(SEND_BTN_JS)
    if not sent:
        # 回退：通过 contenteditable div 模拟 Enter 发送
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            inp = driver.find_element(By.CSS_SELECTOR, 'div.chat-input-editor[contenteditable]')
            if inp:
                ActionChains(driver).move_to_element(inp).click().send_keys(Keys.ENTER).perform()
        except Exception:
            try:
                inp = driver.find_element(By.CSS_SELECTOR, '[contenteditable="true"]')
                ActionChains(driver).move_to_element(inp).click().send_keys(Keys.ENTER).perform()
            except Exception:
                pass
    print(f"[Kimi] 消息已发送，等待响应... (sent={sent})")
    _persist_conversation_url(driver, app_session_id)

    if stream:
        # ── 真流式：轮询时 yield 部分内容 ──
        def _stream_gen():
            final_reply = ''
            for kind, text in _wait_response_stream(driver, prev_text=prev, timeout=240):
                if kind == 'partial' and text:
                    yield text  # 全量文本，调用方自行计算增量
                elif kind == 'final':
                    final_reply = text or ''
                    yield final_reply
                    break
            if not final_reply:
                final_reply = _get_last_assistant_js(driver) or ''
                yield final_reply
            print(f"[Kimi] 流式完成, 长度: {len(final_reply)}")
            has_md = _has_markdown_syntax(final_reply)
            print(f"[Kimi] 响应包含 Markdown 语法: {has_md}")
            if not has_md and final_reply:
                print(f"[Kimi] 响应前 200 字符预览: {final_reply[:200]!r}")
        return _stream_gen()

    # ── 非流式：等待完整回复 ──
    final_reply = ''
    for kind, text in _wait_response_stream(driver, prev_text=prev, timeout=240):
        if kind == 'final':
            final_reply = text or ''
            break
        elif kind == 'partial':
            final_reply = text or ''
    if not final_reply:
        final_reply = _get_last_assistant_js(driver) or ''
    reply = final_reply
    print(f"[Kimi] 收到响应，长度: {len(reply)}")
    has_md = _has_markdown_syntax(reply)
    print(f"[Kimi] 响应包含 Markdown 语法: {has_md}")
    if not has_md and reply:
        print(f"[Kimi] 响应前 200 字符预览: {reply[:200]!r}")
    return {
        'id': f'chatcmpl-kimi-{int(time.time())}',
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
