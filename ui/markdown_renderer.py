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
    return highlight(code, lexer, _pygments_formatter)


def _style_code_blocks(html_text: str) -> tuple[str, list[str]]:
    """给 <pre><code> 块添加深色背景 + 复制链接，返回 (html, code_blocks)"""
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
                f'<span style="color:#a6adc8; font-size:11px; '
                f'font-family:Consolas,monospace;">{lang}</span>'
            )

        return (
            f'<table cellspacing="0" cellpadding="0" width="100%" style="'
            f'background:#1e1e2e; border-radius:8px; margin:8px 0;">'
            f'<tr>'
            f'<td style="padding:6px 0 6px 12px; background:#1e1e32;">'
            f'{lang_label}'
            f'</td>'
            f'<td align="right" style="padding:6px 12px 6px 0; background:#1e1e32;">'
            f'<a href="copy_{idx}" style="color:#ffffff; '
            f'background:#4a4a6a; border-radius:4px; padding:2px 10px; '
            f'font-size:11px; font-family:sans-serif; text-decoration:none;">'
            f'复制</a>'
            f'</td>'
            f'</tr>'
            f'<tr><td colspan="2" style="padding:10px 14px;">'
            f'<pre style="margin:0; padding:0; background:#1e1e2e; color:#f8f8f2; '
            f'font-family:Consolas,&quot;Courier New&quot;,monospace; '
            f'font-size:13px; line-height:1.6; white-space:pre-wrap; '
            f'word-wrap:break-word;">'
            f'{highlighted}'
            f'</pre>'
            f'</td></tr></table>'
        )

    pattern = r'<pre(\s[^>]*)?>\s*<code(\s[^>]*)?>(.*?)</code>\s*</pre>'
    result = re.sub(pattern, replace_code_block, html_text, flags=re.DOTALL)
    return result, code_blocks


def _style_inline_code(html_text: str) -> str:
    """给行内 <code> 添加样式（排除已在 pre 中的）"""
    parts = re.split(r'(<pre.*?</pre>)', html_text, flags=re.DOTALL)
    result = []
    for part in parts:
        if part.startswith('<pre'):
            result.append(part)
        else:
            part = re.sub(
                r'<code>(.*?)</code>',
                r'<code style="background:rgba(99,102,241,0.08); color:#6d28d9; '
                r'padding:1px 5px; border-radius:4px; '
                r'font-family:Consolas,monospace; font-size:13px;">\1</code>',
                part,
            )
            result.append(part)
    return ''.join(result)


def _style_blockquotes(html_text: str) -> str:
    return html_text.replace(
        '<blockquote>',
        '<blockquote style="border-left:3px solid #4f46e5; padding:4px 12px; '
        'margin:8px 0; background:rgba(79,70,229,0.05); color:#555;">'
    )


def _style_tables(html_text: str) -> str:
    html_text = html_text.replace(
        '<table>',
        '<table cellspacing="0" cellpadding="6" style="border-collapse:collapse; margin:8px 0;">'
    )
    html_text = html_text.replace(
        '<th>',
        '<th style="border:1px solid #d1d1d6; padding:6px 12px; background:#f5f5f7; '
        'font-weight:bold; text-align:left;">'
    )
    html_text = html_text.replace(
        '<td>',
        '<td style="border:1px solid #d1d1d6; padding:6px 12px;">'
    )
    return html_text


def render_markdown(text: str) -> RenderResult:
    """
    将 Markdown 文本转换为带内联样式的 HTML

    Returns:
        RenderResult，包含 .html 和 .code_blocks
    """
    md = markdown.Markdown(
        extensions=[
            FencedCodeExtension(),
            TableExtension(),
            "extra",
            "nl2br",
        ],
        output_format="html",
    )
    html_body = md.convert(text)

    # 代码块处理（同时收集原始代码用于复制）
    html_body, code_blocks = _style_code_blocks(html_body)
    html_body = _style_inline_code(html_body)
    html_body = _style_blockquotes(html_body)
    html_body = _style_tables(html_body)

    return RenderResult(html_body, code_blocks)

