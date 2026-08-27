"""从浏览器聊天页 DOM 还原 Markdown 文本（ChatGPT / DeepSeek 等共用）"""

import re

EXTRACT_MARKDOWN_FROM_ASSISTANT_JS = """
function stripCodeHeaderNoise(text) {
  return (text || '')
    .replace(/^(Python|JavaScript|JSON|Bash|Shell|TypeScript|Java|Go|Rust|C\\+\\+|C#|Ruby|PHP|SQL|HTML|CSS|YAML|Markdown)\\s*(运行|Run|Copy|复制)?\\s*/i, '')
    .replace(/\\u00a0/g, ' ');
}

function reflowFlattenedCode(text) {
  if (!text || text.indexOf('\\n') >= 0) return text;
  var t = stripCodeHeaderNoise(text);
  t = t.replace(/\\s+(def\\s+\\w+\\s*\\()/g, '\\n$1');
  t = t.replace(/\\s+(class\\s+\\w+\\s*[:(])/g, '\\n$1');
  t = t.replace(/\\s+(if\\s+__name__\\s*==)/g, '\\n$1');
  t = t.replace(/(\\S)(if\\s+__name__\\s*==)/g, '$1\\n$2');
  t = t.replace(/\\s{4,}/g, '\\n    ');
  return t.trim();
}

function extractFromBlockNodes(codeEl) {
  var parts = [];
  var current = '';
  function walk(node) {
    if (node.nodeType === 3) {
      current += node.textContent.replace(/\\u00a0/g, ' ');
      return;
    }
    if (node.nodeName === 'BR') {
      if (current) parts.push(current.replace(/\\s+$/, ''));
      current = '';
      return;
    }
    if (node !== codeEl) {
      try {
        var style = window.getComputedStyle(node);
        if ((style.display === 'block' || style.display === 'list-item') && current.trim()) {
          parts.push(current.replace(/\\s+$/, ''));
          current = '';
        }
      } catch (e) {}
    }
    for (var i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i]);
  }
  walk(codeEl);
  if (current.trim()) parts.push(current.replace(/\\s+$/, ''));
  if (parts.length > 1) {
    return parts.map(function(p) { return stripCodeHeaderNoise(p); }).join('\\n');
  }
  return null;
}

function extractCodeFromPre(pre) {
  var codeEl = pre.querySelector('code') || pre;
  var clone = codeEl.cloneNode(true);
  clone.querySelectorAll('button, [role="button"]').forEach(function(el) { el.remove(); });

  var trs = clone.querySelectorAll('table tr');
  if (trs.length > 0) {
    return Array.from(trs).map(function(tr) {
      return stripCodeHeaderNoise((tr.textContent || '').replace(/^\\s*\\d+\\s?/, ''));
    }).join('\\n');
  }

  var lineDivs = clone.querySelectorAll(':scope > div');
  if (lineDivs.length > 1) {
    return Array.from(lineDivs).map(function(d) {
      return stripCodeHeaderNoise((d.textContent || '').replace(/\\s+$/, ''));
    }).join('\\n');
  }

  var lineNodes = clone.querySelectorAll(
    '[class*="Line"], [data-line], .react-syntax-highlighter-line, [class*="code-line"]'
  );
  if (lineNodes.length > 1) {
    return Array.from(lineNodes).map(function(n) {
      return stripCodeHeaderNoise((n.textContent || '').replace(/\\s+$/, ''));
    }).join('\\n');
  }

  var blockText = extractFromBlockNodes(clone);
  if (blockText) return blockText;

  var html = clone.innerHTML || '';
  if (/<br\\s*\\/?>/i.test(html)) {
    return html.split(/<br\\s*\\/?>/i).map(function(part) {
      var el = document.createElement('div');
      el.innerHTML = part;
      return stripCodeHeaderNoise(el.textContent || '');
    }).join('\\n');
  }

  var raw = stripCodeHeaderNoise(clone.textContent || clone.innerText || '');
  return reflowFlattenedCode(raw);
}

function extractMarkdownFromAssistant(node) {
  if (!node) return '';
  var el = node.querySelector('.markdown,[class*="markdown"]') || node;
  var clone = el.cloneNode(true);
  // 移除复制按钮、工具栏等噪声元素
  clone.querySelectorAll('button, [role="button"], [class*="toolbar"], [class*="header"], [class*="copy"], [class*="Copy"]').forEach(function(el) {
    el.remove();
  });
  clone.querySelectorAll('pre').forEach(function(pre) {
    var wrapper = pre.closest('[class*="code"], [class*="Code"], [data-testid*="code"]') || pre.parentElement;
    if (wrapper && wrapper !== pre) {
      wrapper.querySelectorAll('button, [role="button"], [class*="toolbar"], [class*="header"]').forEach(function(el) {
        if (!pre.contains(el)) el.remove();
      });
    }
    var code = extractCodeFromPre(pre);
    var lang = '';
    var codeEl = pre.querySelector('code');
    if (codeEl) {
      var m = (codeEl.className || '').match(/language-([\\w+-]+)/);
      if (m) lang = m[1];
    }
    pre.replaceWith(document.createTextNode('\\n```' + lang + '\\n' + code + '\\n```\\n'));
  });
  // 表格转 Markdown
  clone.querySelectorAll('table').forEach(function(table) {
    var rows = table.querySelectorAll('tr');
    if (rows.length === 0) return;
    var lines = [];
    var isFirstRow = true;
    var colCount = 0;
    rows.forEach(function(tr) {
      var cells = tr.querySelectorAll('th, td');
      if (isFirstRow) colCount = cells.length;
      var cellTexts = Array.from(cells).map(function(c) {
        return (c.innerText || c.textContent || '').trim().replace(/\\|/g, '\\\\|');
      });
      lines.push('| ' + cellTexts.join(' | ') + ' |');
      if (isFirstRow) {
        var sep = Array.from({length: colCount}, function() { return '---'; }).join(' | ');
        lines.push('| ' + sep + ' |');
        isFirstRow = false;
      }
    });
    table.replaceWith(document.createTextNode('\\n' + lines.join('\\n') + '\\n'));
  });
  // 引用块
  clone.querySelectorAll('blockquote').forEach(function(bq) {
    var t = (bq.innerText || '').trim();
    var bqLines = t.split('\\n').map(function(l) { return '> ' + l; });
    bq.replaceWith(document.createTextNode('\\n' + bqLines.join('\\n') + '\\n'));
  });
  // 水平分割线
  clone.querySelectorAll('hr').forEach(function(hr) {
    hr.replaceWith(document.createTextNode('\\n---\\n'));
  });
  // 图片
  clone.querySelectorAll('img').forEach(function(img) {
    var src = img.getAttribute('src') || '';
    var alt = img.getAttribute('alt') || '';
    if (src) img.replaceWith(document.createTextNode('![' + alt + '](' + src + ')'));
  });
  clone.querySelectorAll('code').forEach(function(c) {
    if (c.closest('pre')) return;
    var t = (c.innerText || c.textContent || '').trim();
    c.replaceWith(document.createTextNode('`' + t + '`'));
  });
  for (var lv = 6; lv >= 1; lv--) {
    clone.querySelectorAll('h' + lv).forEach(function(h) {
      var marks = '';
      for (var i = 0; i < lv; i++) marks += '#';
      h.replaceWith(document.createTextNode('\\n' + marks + ' ' + (h.innerText || '').trim() + '\\n'));
    });
  }
  clone.querySelectorAll('li').forEach(function(li) {
    var parent = li.parentElement;
    var prefix = (parent && parent.nodeName === 'OL') ? '1. ' : '- ';
    var t = (li.innerText || li.textContent || '').trim();
    li.replaceWith(document.createTextNode('\\n' + prefix + t));
  });
  clone.querySelectorAll('strong,b').forEach(function(s) {
    var t = (s.innerText || s.textContent || '').trim();
    s.replaceWith(document.createTextNode('**' + t + '**'));
  });
  clone.querySelectorAll('em,i').forEach(function(s) {
    var t = (s.innerText || s.textContent || '').trim();
    s.replaceWith(document.createTextNode('*' + t + '*'));
  });
  // 删除线
  clone.querySelectorAll('del,s,strike').forEach(function(s) {
    var t = (s.innerText || s.textContent || '').trim();
    s.replaceWith(document.createTextNode('~~' + t + '~~'));
  });
  clone.querySelectorAll('a[href]').forEach(function(a) {
    var t = (a.innerText || a.textContent || '').trim();
    var href = a.getAttribute('href') || '';
    a.replaceWith(document.createTextNode('[' + t + '](' + href + ')'));
  });
  var text = (clone.innerText || clone.textContent || '').trim();
  return text.replace(/\\n{3,}/g, '\\n\\n');
}
"""

DEEPSEEK_LAST_ASSISTANT_JS = """
function getLastDeepSeekAssistantNode(){
  var sel=[
    '[class*="assistant"]:not([class*="user"])',
    '[data-role="assistant"]',
    '[class*="Assistant"]:not([class*="User"])'
  ];
  for(var i=0;i<sel.length;i++){
    try{
      var nodes=document.querySelectorAll(sel[i]);
      if(nodes.length) return nodes[nodes.length-1];
    }catch(e){}
  }
  var wrappers=document.querySelectorAll('[class*="message"]');
  for(var j=wrappers.length-1;j>=0;j--){
    var el=wrappers[j];
    var cls=(el.className||'')+'';
    if(/assistant/i.test(cls)&&!/user/i.test(cls)) return el;
  }
  if(wrappers.length) return wrappers[wrappers.length-1];
  var md=document.querySelectorAll('[class*="markdown"]');
  return md.length ? md[md.length-1] : null;
}
var node=getLastDeepSeekAssistantNode();
return node ? extractMarkdownFromAssistant(node) : '';
"""


def reflow_flat_code(code: str) -> str:
    """DOM 抓取时可能把多行代码压成一行，尝试恢复换行。"""
    if not code or "\n" in code.strip():
        return code
    text = re.sub(
        r"^(Python|JavaScript|JSON|Bash|Shell|TypeScript|Java|Go|Rust|"
        r"C\+\+|C#|Ruby|PHP|SQL|HTML|CSS|YAML|Markdown)\s*(运行|Run|Copy|复制)?\s*",
        "",
        code,
        flags=re.I,
    )
    text = re.sub(r"\s+(def\s+\w+\s*\()", r"\n\1", text)
    text = re.sub(r"\s+(class\s+\w+\s*[:\(])", r"\n\1", text)
    text = re.sub(r"\s+(if\s+__name__\s*==)", r"\n\1", text)
    text = re.sub(r"(\S)(if\s+__name__\s*==)", r"\1\n\2", text)
    text = re.sub(r"\s{4,}", "\n    ", text)
    return text.strip()


def normalize_browser_markdown(text: str) -> str:
    """修正网页抓取后代码块换行/标题噪声，并过滤思考内容。"""
    if not text:
        return text

    # ── 清理 Kimi 代码块头部噪声 ──
    # Kimi 的代码块提取后经常出现 "Python\n复制\ndef ..." 或 "JavaScript\n复制\nconst ..."
    # 这些是代码块 header（语言名 + 复制按钮文本）泄漏到正文
    # 需要移除噪声并补全 ``` 围栏
    _lang_pattern = (
        r"Python|JavaScript|TypeScript|JSON|Bash|Shell|Java|Go|Rust|"
        r"C\+\+|C#|Ruby|PHP|SQL|HTML|CSS|YAML|Markdown|XML|Vue|React|"
        r"python|javascript|typescript|java|golang|rust|cpp|csharp|"
        r"shell|bash|sql|html|css|yaml|json|xml|markdown"
    )
    # 模式1: "Language\n复制\n" 或 "Language\nCopy\n" 后跟代码 → 替换为 ```language\n
    text = re.sub(
        rf"(?:^|\n)({_lang_pattern})\s*\n(?:复制|Copy|copy)\s*\n",
        lambda m: f"\n```{m.group(1).lower()}\n",
        text,
    )
    # 模式2: "Language\n复制" (无尾换行) → 移除噪声
    text = re.sub(
        rf"(?:^|\n)({_lang_pattern})\s*\n(?:复制|Copy|copy)\s*",
        lambda m: f"\n```{m.group(1).lower()}\n",
        text,
    )
    # 模式3: 行首 "Language 复制" (同行) → 移除
    text = re.sub(
        rf"(?:^|\n)({_lang_pattern})\s+(?:复制|Copy|copy)\s*\n",
        lambda m: f"\n```{m.group(1).lower()}\n",
        text,
    )

    # ── 补全未闭合的 ``` 围栏 ──
    # 如果文本中有 ``` 开头但没有对应的 ``` 结尾，在末尾补上
    fence_count = text.count("```")
    if fence_count % 2 == 1:
        text = text.rstrip() + "\n```"

    def _fix_fence(match):
        lang = match.group(1) or ""
        code = reflow_flat_code(match.group(2))
        return f"```{lang}\n{code}\n```"

    # 修正代码块：确保 ``` 后有换行（DOM 提取可能丢失换行）
    text = re.sub(r"```(\w*)\n(.*?)```", _fix_fence, text, flags=re.DOTALL)
    # 处理缺少换行的代码块：```python code``` → ```python\ncode\n```
    text = re.sub(r"```(\w+)\s+(.+?)```", lambda m: f"```{m.group(1)}\n{m.group(2)}\n```", text, flags=re.DOTALL)

    # 过滤 Kimi 思考内容（文本级别兜底）
    # 移除 Kimi 特有的 Unicode 标记及其内容
    text = re.sub(
        r"<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 如果只有结束标记，移除它及其之前的所有内容
    if "<｜end▁of▁thinking｜>" in text:
        idx = text.rfind("<｜end▁of▁thinking｜>")
        after = text[idx + len("<｜end▁of▁thinking｜>"):]
        # 只有当后面有实质内容时才截断
        if after.strip():
            text = after
    text = text.replace("<｜begin▁of▁thinking｜>", "").replace("<｜end▁of▁thinking｜>", "")

    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_deepseek_markdown(driver) -> str:
    """从 DeepSeek 聊天页提取最新 assistant 回复的 Markdown。"""
    try:
        raw = driver.execute_script(
            EXTRACT_MARKDOWN_FROM_ASSISTANT_JS + DEEPSEEK_LAST_ASSISTANT_JS
        )
    except Exception:
        return ""
    return normalize_browser_markdown(raw or "")


KIMI_LAST_ASSISTANT_JS = """
function getLastKimiAssistantNode(){
  // Kimi 消息通常包裹在特定 class 中，assistant 消息和 user 消息在不同容器
  var sel=[
    // Kimi 专有选择器
    '[class*="kimi-answer"]',
    '[class*="KimiAnswer"]',
    '[class*="chat-bubble"]:not([class*="user"])',
    '[class*="bubble"]:not([class*="user"])',
    '[class*="reply"]',
    // 通用选择器
    '[class*="assistant"]:not([class*="user"])',
    '[class*="Assistant"]:not([class*="User"])',
    '[data-role="assistant"]',
    '[class*="answer"]',
    '[class*="response"]',
  ];
  for(var i=0;i<sel.length;i++){
    try{
      var nodes=document.querySelectorAll(sel[i]);
      if(nodes.length) return nodes[nodes.length-1];
    }catch(e){}
  }
  // 回退：查找所有消息容器，取最后一个非 user 的
  var wrappers=document.querySelectorAll(
    '[class*="message"],[class*="Message"],[class*="item"],[class*="Item"],'
    +'[class*="chat"],[class*="turn"]'
  );
  var assistantWrappers=[];
  for(var j=0;j<wrappers.length;j++){
    var el=wrappers[j];
    var cls=(el.className||'')+'';
    if(!/user|User|question|Question/.test(cls)){
      assistantWrappers.push(el);
    }
  }
  if(assistantWrappers.length) return assistantWrappers[assistantWrappers.length-1];
  if(wrappers.length) return wrappers[wrappers.length-1];
  // 更广泛的回退：取任何包含实质文本的容器
  var textBlocks = document.querySelectorAll('p, [class*="text"], [class*="Text"], [class*="content"], [class*="Content"]');
  for (var k = textBlocks.length - 1; k >= 0; k--) {
    var tt = (textBlocks[k].innerText || textBlocks[k].textContent || '').trim();
    if (tt.length > 30) return textBlocks[k];
  }
  // 最终回退：取全部 markdown 节点
  var md=document.querySelectorAll('[class*="markdown"]');
  return md.length ? md[md.length-1] : null;
}
var node=getLastKimiAssistantNode();
if(node){
  // 克隆节点后移除思考/推理区域，避免修改原始 DOM
  var clone=node.cloneNode(true);
  // 移除 Kimi 思考过程区域（class 含 think/reasoning/analysis/thought）
  var thinkingSelectors=[
    '[class*="think"]',
    '[class*="Think"]',
    '[class*="reasoning"]',
    '[class*="Reasoning"]',
    '[class*="analysis"]',
    '[class*="Analysis"]',
    '[class*="thought"]',
    '[class*="Thought"]',
    '[class*="collapse-think"]',
    '[class*="thinking-process"]',
    '[class*="reasoning-process"]'
  ];
  for(var si=0;si<thinkingSelectors.length;si++){
    try{
      clone.querySelectorAll(thinkingSelectors[si]).forEach(function(el){
        // 不移除包含 markdown 主体内容的节点
        if(!el.querySelector('.markdown,[class*="markdown"]')){
          el.remove();
        }
      });
    }catch(e){}
  }
  // 用清理后的克隆节点提取 Markdown
  return extractMarkdownFromAssistant(clone);
}
return '';
"""
