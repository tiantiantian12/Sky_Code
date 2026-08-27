"""
Markdown 渲染模块
将 Markdown 转换为带内联样式的 HTML（QLabel RichText 兼容）
代码块：深色背景 + 白色代码 + 可点击的复制链接
"""

import re
import html as html_module
import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter


class RenderResult:
    """渲染结果，包含 HTML 和代码块列表（用于复制功能）"""
    def __init__(self, html: str, code_blocks: list[str]):
        self.html = html
        self.code_blocks = code_blocks


# ── 复用 Markdown 实例（避免每次 render 都重新初始化扩展） ──
_md_instance = None

def _get_md_instance():
    global _md_instance
    if _md_instance is None:
        _md_instance = markdown.Markdown(
            extensions=[
                FencedCodeExtension(),
                TableExtension(),
                "extra",
                "nl2br",
                "sane_lists",  # 更好的列表渲染
                "abbr",        # 缩写支持
                "toc",         # 目录支持
            ],
            output_format="html",
        )
    _md_instance.reset()
    return _md_instance

# ── 流式渲染缓存：避免相同文本重复渲染 ──
_stream_cache = {}  # {(theme, text_hash): (html, code_blocks)}

def _text_hash(text: str) -> int:
    return hash(text)


def _theme_colors(theme: str) -> dict:
    """根据主题返回 markdown 渲染用的颜色，深色主题使用亮色文字"""
    is_dark = theme == 'dark'
    return {
        'inline_code_bg': 'rgba(77,107,254,0.15)' if is_dark else 'rgba(99,102,241,0.08)',
        'inline_code_color': '#6e7fe0' if is_dark else '#6d28d9',
        'blockquote_color': '#9a9ca6' if is_dark else '#555',
        'bold_color': '#d9dae0' if is_dark else '#1d1d1f',
        'link_color': '#6e7fe0' if is_dark else '#6366f1',
        'table_border': '#3a3a3a' if is_dark else '#d1d1d6',
        'th_bg': '#2d2e34' if is_dark else '#f5f5f7',
        'th_color': '#d9dae0' if is_dark else '#1d1d1f',
        # 代码块配色（现代极简风格：浅色浅灰底深字，深色深底亮字）
        'code_bg': '#1f2024' if is_dark else '#f6f7f9',
        'code_header_bg': '#27282e' if is_dark else '#eef0f3',
        'code_text': '#f8f8f2' if is_dark else '#1d1d2b',
        'code_label': '#9a9ca6' if is_dark else '#6b7280',
        'code_copy_bg': '#3a3a3a' if is_dark else '#e2e5ea',
        'code_copy_color': '#ffffff' if is_dark else '#4b5563',
    }


def render_markdown_fast(text: str, theme: str = "light") -> RenderResult:
    """流式阶段快速渲染：跳过 pygments 语法高亮，使用简化样式。
    比 render_markdown 快 5-10 倍，适合流式输出时频繁调用。"""
    h = (theme, _text_hash(text))
    cached = _stream_cache.get(h)
    if cached:
        return RenderResult(cached[0], cached[1])

    text_norm, mermaid_sources = _extract_mermaid_blocks(text)
    text_norm = _normalize_loose_code_fences(text_norm)

    md = _get_md_instance()
    html_body = md.convert(text_norm)

    colors = _theme_colors(theme)
    # 简化代码块样式：不用 pygments，只用纯背景色
    code_blocks = []
    counter = [0]
    def replace_code_block_fast(match):
        pre_attrs = match.group(1) or ""
        code_attrs = match.group(2) or ""
        code_content = match.group(3)
        idx = counter[0]
        counter[0] += 1
        plain_code = re.sub(r'<[^>]+>', '', code_content)
        plain_code = html_module.unescape(plain_code)
        code_blocks.append(plain_code)
        lang_match = re.search(r'class="(?:language-|hljs )?(\w+)"', code_attrs)
        lang = lang_match.group(1) if lang_match else ""
        lang_label = (f'<span style="color:{colors["code_label"]}; '
                      f'font-size:11px;">{lang}</span>') if lang else ""
        return (
            f'<table cellspacing="0" cellpadding="0" width="100%" style="'
            f'background:{colors["code_bg"]}; border-radius:8px; margin:8px 0;">'
            f'<tr><td style="padding:6px 0 6px 12px; background:{colors["code_header_bg"]};">'
            f'{lang_label}</td>'
            f'<td align="right" style="padding:6px 12px 6px 0; background:{colors["code_header_bg"]};">'
            f'<a href="copy_{idx}" style="color:{colors["code_copy_color"]}; '
            f'background:{colors["code_copy_bg"]}; border-radius:4px; '
            f'padding:2px 10px; font-size:11px; text-decoration:none;">复制</a></td></tr>'
            f'<tr><td colspan="2" style="padding:10px 14px;">'
            f'<div style="background:{colors["code_bg"]}; color:{colors["code_text"]}; '
            f'font-family:Consolas,monospace; '
            f'font-size:13px; line-height:1.6; white-space:pre-wrap; word-wrap:break-word;">'
            f'{html_module.escape(plain_code)}'
            f'</div></td></tr></table>'
        )
    pattern = r'<pre(\s[^>]*)?>\s*<code(\s[^>]*)?>(.*?)</code>\s*</pre>'
    html_body = re.sub(pattern, replace_code_block_fast, html_body, flags=re.DOTALL)

    colors = _theme_colors(theme)
    html_body = _style_inline_code(html_body, colors)
    html_body = _style_blockquotes(html_body, colors)
    html_body = _style_paragraphs_and_lists(html_body, colors)
    html_body = _style_tables(html_body, colors)

    # 缓存（限制缓存大小）
    if len(_stream_cache) > 20:
        _stream_cache.clear()
    _stream_cache[h] = (html_body, code_blocks)

    return RenderResult(html_body, code_blocks)


# pygments formatter：内联样式（QLabel 兼容），深色主题
_pygments_formatter = HtmlFormatter(
    noclasses=True,
    nowrap=True,
    style="monokai",
)


def _highlight_code(code: str, lang: str) -> str:
    """用 pygments 高亮代码，返回带内联样式的 HTML"""
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except Exception:
        lexer = TextLexer()
    highlighted = highlight(code, lexer, _pygments_formatter)
    return _pygments_to_qt_html(highlighted)


def _pygments_to_qt_html(html: str) -> str:
    """将 pygments 的 span 样式转为 QLabel RichText 兼容的 font 标签"""
    def span_repl(match):
        style = match.group(1)
        inner = match.group(2)
        color_match = re.search(r'color:\s*(#[0-9a-fA-F]{6})', style)
        if color_match:
            return f'<font color="{color_match.group(1)}">{inner}</font>'
        return inner

    prev = None
    result = html
    while prev != result:
        prev = result
        result = re.sub(
            r'<span style="[^"]*">((?:.(?!</span>))*?)</span>',
            span_repl,
            result,
            flags=re.DOTALL,
        )
    return result


def _looks_like_code_line(line: str) -> bool:
    """判断一行是否像代码（用于合并围栏外的散落代码行）"""
    stripped = line.rstrip()
    if not stripped.strip():
        return False
    patterns = (
        r'^\s*(def|class|import|from|if|elif|else|for|while|return|try|except|'
        r'with|async|await|pass|break|continue|raise|yield)\b',
        r'^\s*#',
        r'^\s+\S',
        r'^\s*\w+\s*=',
        r'^\s*print\(',
        r'^\s*@\w+',
    )
    if any(re.match(p, stripped) for p in patterns):
        return True
    return not re.search(r'[\u4e00-\u9fff]', stripped) and bool(re.search(r'[=\(\)\[\]\{\}]', stripped))


def _normalize_loose_code_fences(text: str) -> str:
    """
    模型常先输出几行裸代码，再写 ```python。
    将围栏前的代码行并入围栏，避免同一段代码被拆成两段显示。
    """
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r'^\s*```\w*\s*$', line):
            prefix: list[str] = []
            while out:
                if not out[-1].strip():
                    out.pop()
                    continue
                if _looks_like_code_line(out[-1]):
                    prefix.insert(0, out.pop())
                else:
                    break
            out.append(line)
            out.extend(prefix)
            i += 1
            while i < len(lines):
                out.append(lines[i])
                if re.match(r'^\s*```\s*$', lines[i]):
                    i += 1
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return '\n'.join(out)


def _style_code_blocks(html_text: str, colors: dict = None) -> tuple[str, list[str]]:
    """给 <pre><code> 块添加主题化背景 + 复制链接，返回 (html, code_blocks)"""
    if colors is None:
        colors = _theme_colors('light')
    code_blocks = []
    counter = [0]

    def replace_code_block(match):
        pre_attrs = match.group(1) or ""
        code_attrs = match.group(2) or ""
        code_content = match.group(3)

        idx = counter[0]
        counter[0] += 1
        # 保存原始代码内容用于复制
        plain_code = re.sub(r'<[^>]+>', '', code_content)
        plain_code = html_module.unescape(plain_code)
        code_blocks.append(plain_code)

        # 提取语言名
        lang_match = re.search(r'class="(?:language-|hljs )?(\w+)"', code_attrs)
        lang = lang_match.group(1) if lang_match else ""

        # 用 pygments 语法高亮
        highlighted = _highlight_code(plain_code, lang)

        lang_label = ""
        if lang:
            lang_label = (
                f'<span style="color:{colors["code_label"]}; font-size:11px; '
                f'font-family:Consolas,monospace;">{lang}</span>'
            )

        return (
            f'<table cellspacing="0" cellpadding="0" width="100%" style="'
            f'background:{colors["code_bg"]}; border-radius:8px; margin:8px 0;">'
            f'<tr>'
            f'<td style="padding:6px 0 6px 12px; background:{colors["code_header_bg"]};">'
            f'{lang_label}'
            f'</td>'
            f'<td align="right" style="padding:6px 12px 6px 0; background:{colors["code_header_bg"]};">'
            f'<a href="copy_{idx}" style="color:{colors["code_copy_color"]}; '
            f'background:{colors["code_copy_bg"]}; border-radius:4px; padding:2px 10px; '
            f'font-size:11px; font-family:sans-serif; text-decoration:none;">'
            f'复制</a>'
            f'</td>'
            f'</tr>'
            f'<tr><td colspan="2" style="padding:10px 14px;">'
            f'<div style="margin:0; padding:0; background:{colors["code_bg"]}; '
            f'color:{colors["code_text"]}; '
            f'font-family:Consolas,&quot;Courier New&quot;,monospace; '
            f'font-size:13px; line-height:1.6; white-space:pre-wrap; '
            f'word-wrap:break-word;">'
            f'{highlighted}'
            f'</div>'
            f'</td></tr></table>'
        )

    pattern = r'<pre(\s[^>]*)?>\s*<code(\s[^>]*)?>(.*?)</code>\s*</pre>'
    result = re.sub(pattern, replace_code_block, html_text, flags=re.DOTALL)
    return result, code_blocks


def _style_inline_code(html_text: str, colors: dict) -> str:
    """给行内 <code> 添加样式（排除代码块 table 区域）"""
    parts = re.split(
        r'(<table cellspacing="0" cellpadding="0" width="100%".*?</table>)',
        html_text,
        flags=re.DOTALL,
    )
    result = []
    for part in parts:
        if part.startswith('<table cellspacing="0"'):
            result.append(part)
        else:
            part = re.sub(
                r'<code>(.*?)</code>',
                fr'<code style="background:{colors["inline_code_bg"]}; '
                fr'color:{colors["inline_code_color"]}; '
                r'padding:1px 5px; border-radius:4px; '
                r'font-family:Consolas,monospace; font-size:13px;">\1</code>',
                part,
            )
            result.append(part)
    return ''.join(result)


def _style_blockquotes(html_text: str, colors: dict) -> str:
    return html_text.replace(
        '<blockquote>',
        f'<blockquote style="border-left:3px solid #6e7fe0; padding:4px 12px; '
        f'margin:8px 0; background:rgba(79,70,229,0.05); color:{colors["blockquote_color"]};">'
    )


def _style_paragraphs_and_lists(html_text: str, colors: dict) -> str:
    """增强段落和列表样式，增加段落间距和列表项间距"""
    # 段落间距：在 p 标签前后增加间距
    html_text = re.sub(
        r'<p>(.*?)</p>',
        r'<p style="margin: 6px 0; line-height: 1.7;">\1</p>',
        html_text,
        flags=re.DOTALL,
    )

    # 列表项：增加间距和缩进
    html_text = re.sub(
        r'<li>(.*?)</li>',
        r'<li style="margin: 4px 0; line-height: 1.6;">\1</li>',
        html_text,
        flags=re.DOTALL,
    )

    # 列表容器：增加左边距
    html_text = re.sub(
        r'<ul>',
        r'<ul style="margin: 6px 0; padding-left: 20px;">',
        html_text,
    )
    html_text = re.sub(
        r'<ol>',
        r'<ol style="margin: 6px 0; padding-left: 20px;">',
        html_text,
    )

    # 标题样式：增加上下间距和加粗
    for level in range(1, 7):
        font_size = [22, 19, 17, 15, 14, 13][level - 1]
        html_text = re.sub(
            rf'<h{level}>(.*?)</h{level}>',
            rf'<h{level} style="margin: {12-level}px 0 {10-level}px 0; font-weight:bold; '
            rf'font-size:{font_size}px; line-height:1.4;">\1</h{level}>',
            html_text,
            flags=re.DOTALL,
        )

    # 粗体强调：增加颜色
    html_text = re.sub(
        r'<strong>(.*?)</strong>',
        fr'<strong style="color:{colors["bold_color"]}; font-weight:700;">\1</strong>',
        html_text,
        flags=re.DOTALL,
    )

    # 链接样式
    html_text = re.sub(
        r'<a\s+href="([^"]*)"(.*?)>(.*?)</a>',
        fr'<a href="\1"\2 style="color:{colors["link_color"]}; text-decoration:none; '
        fr'border-bottom:1px dashed #a5b4fc;">\3</a>',
        html_text,
    )

    return html_text


def _style_tables(html_text: str, colors: dict) -> str:
    html_text = html_text.replace(
        '<table>',
        '<table cellspacing="0" cellpadding="6" style="border-collapse:collapse; margin:8px 0;">'
    )
    html_text = html_text.replace(
        '<th>',
        fr'<th style="border:1px solid {colors["table_border"]}; padding:6px 12px; '
        fr'background:{colors["th_bg"]}; color:{colors["th_color"]}; '
        r'font-weight:bold; text-align:left;">'
    )
    html_text = html_text.replace(
        '<td>',
        fr'<td style="border:1px solid {colors["table_border"]}; padding:6px 12px;">'
    )
    return html_text


def _extract_mermaid_blocks(text: str) -> tuple[str, list[str]]:
    """提取 ```mermaid 代码块，替换为占位符，返回 (处理后文本, mermaid源码列表)"""
    mermaid_blocks = []
    counter = [0]

    def replace_mermaid(match):
        idx = counter[0]
        counter[0] += 1
        source = match.group(1).strip()
        mermaid_blocks.append(source)
        return f"```mermaid_diagram_{idx}\n{source}\n```"

    result = re.sub(
        r'```mermaid\s*\n(.*?)```',
        replace_mermaid,
        text,
        flags=re.DOTALL,
    )
    return result, mermaid_blocks


def _style_mermaid_diagrams(html_text: str, mermaid_sources: list[str]) -> str:
    """将 mermaid 占位代码块替换为样式化的图表占位框"""
    for idx, source in enumerate(mermaid_sources):
        first_line = source.split('\n')[0].strip() if source else ""
        diagram_type = first_line.split()[0] if first_line else "diagram"

        type_labels = {
            'graph': '流程图', 'flowchart': '流程图',
            'sequenceDiagram': '时序图', 'classDiagram': '类图',
            'stateDiagram': '状态图', 'erDiagram': 'ER图',
            'gantt': '甘特图', 'pie': '饼图',
            'journey': '用户旅程', 'gitGraph': 'Git图',
        }
        label = type_labels.get(diagram_type, f'图表 ({diagram_type})')

        preview_lines = source.split('\n')[:8]
        preview_html = '<br>'.join(html_module.escape(l) for l in preview_lines)
        if len(source.split('\n')) > 8:
            preview_html += '<br>...'

        old_pattern = (
            r'<table cellspacing="0" cellpadding="0" width="100%".*?'
            r'mermaid_diagram_' + str(idx) + r'.*?</table>'
        )

        replacement = (
            f'<table cellspacing="0" cellpadding="0" width="100%" style="'
            f'background: #32333a; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; margin: 8px 0;">'
            f'<tr>'
            f'<td style="padding:8px 12px; background: #1e1e32; border-radius: 8px 8px 0 0;">'
            f'<span style="color:#6e7fe0; font-size:13px;">{label}</span>'
            f'<span style="color:#6c7086; font-size:11px; margin-left:8px;">Mermaid</span>'
            f'</td>'
            f'</tr>'
            f'<tr><td style="padding:10px 14px;">'
            f'<div style="background: #11111b; border-radius: 4px; padding: 8px 10px; '
            f'color: #7f849c; font-family: Consolas, monospace; font-size: 11px; '
            f'line-height: 1.5; white-space: pre-wrap;">'
            f'{preview_html}'
            f'</div>'
            f'</td></tr></table>'
        )

        html_text = re.sub(
            old_pattern,
            replacement,
            html_text,
            flags=re.DOTALL,
            count=1,
        )

    return html_text


def render_markdown(text: str, theme: str = "light") -> RenderResult:
    """
    将 Markdown 文本转换为带内联样式的 HTML（完整渲染，含 pygments 高亮）。
    用于最终输出或非流式场景。
    """
    text, mermaid_sources = _extract_mermaid_blocks(text)
    text = _normalize_loose_code_fences(text)

    md = _get_md_instance()
    html_body = md.convert(text)

    colors = _theme_colors(theme)
    html_body, code_blocks = _style_code_blocks(html_body, colors)
    if mermaid_sources:
        html_body = _style_mermaid_diagrams(html_body, mermaid_sources)
    html_body = _style_inline_code(html_body, colors)
    html_body = _style_blockquotes(html_body, colors)
    html_body = _style_paragraphs_and_lists(html_body, colors)
    html_body = _style_tables(html_body, colors)

    return RenderResult(html_body, code_blocks)

