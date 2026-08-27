"""
LSP（语言服务协议）集成工具集
为 Agent 提供类似 IDE 的代码分析能力：
  - diagnose_file        : 诊断文件（错误、警告、提示）
  - go_to_definition     : 跳转到定义
  - get_hover_info       : 悬停提示（函数/类文档）
  - find_references      : 查找引用
  - get_document_symbols : 获取文件符号列表
  - get_completion_items: 代码补全建议

设计理念：
  不依赖外部 LSP server 进程，而是直接使用 Python ast / inspect 模块
  以及子进程调用 linter（flake8/pylint）实现轻量级 LSP 功能。
  对于 JS/TS 等语言，降级为基础正则匹配 + 语法检查。
"""

import os
import ast
import re
import json
import subprocess
import tempfile
import inspect
import importlib
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ── 辅助函数 ──────────────────────────────────────────────────

def _get_startupinfo():
    """获取 subprocess 启动信息，隐藏 Windows 控制台窗口"""
    import sys
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None


def _read_file_safe(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception:
        return ""


def _line_col_from_offset(offset: int, text: str):
    """将字符偏移转换为 (line, col)"""
    line = 1
    col = 1
    for i, ch in enumerate(text):
        if i >= offset:
            break
        if ch == '\n':
            line += 1
            col = 1
        else:
            col += 1
    return line, col


# ── 1. diagnose_file — 诊断文件 ─────────────────────────────

class DiagnoseFileInput(BaseModel):
    file_path: str = Field(..., description="要诊断的文件绝对路径")
    severity_filter: str = Field("all", description="过滤级别：all/error/warning/info，默认 all")


@tool(args_schema=DiagnoseFileInput)
def diagnose_file(file_path: str, severity_filter: str = "all") -> str:
    """诊断文件中的错误、警告和提示信息。
    等同于 IDE 中打开文件后看到的红色/黄色波浪线。
    支持 Python（ast + py_compile + flake8）、JavaScript/TypeScript（基础语法检查）。

    Args:
        file_path: 文件绝对路径，例如 "D:/project/main.py"
        severity_filter: 过滤级别，可选 "all"/"error"/"warning"/"info"
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    diagnostics = []

    if ext == '.py':
        diagnostics = _diagnose_python(file_path, content)
    elif ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs'):
        diagnostics = _diagnose_javascript(file_path, content)
    elif ext in ('.json',):
        diagnostics = _diagnose_json(file_path, content)
    elif ext in ('.html', '.htm', '.xml'):
        diagnostics = _diagnose_markup(file_path, content)
    else:
        return f"提示: 文件类型 {ext} 暂不支持诊断"

    # 过滤
    if severity_filter != "all":
        diagnostics = [d for d in diagnostics if d["severity"] == severity_filter]

    if not diagnostics:
        return f"✓ {os.path.basename(file_path)} 无诊断问题"

    # 格式化输出
    lines = [f"诊断结果: {os.path.basename(file_path)} ({len(diagnostics)} 个问题)\n"]
    for d in diagnostics:
        icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(d["severity"], "•")
        lines.append(f"  {icon} 行 {d['line']}:{d.get('col', 0)} [{d['severity']}] {d['message']}")
        if d.get("source"):
            lines.append(f"     来源: {d['source']}")

    return "\n".join(lines)


def _diagnose_python(file_path: str, content: str) -> list:
    """Python 诊断：ast + py_compile + flake8"""
    diagnostics = []

    # 1. ast 语法检查
    try:
        ast.parse(content, filename=file_path)
    except SyntaxError as e:
        diagnostics.append({
            "line": e.lineno or 1,
            "col": e.offset or 0,
            "message": e.msg or "语法错误",
            "severity": "error",
            "source": "ast",
        })
        return diagnostics  # 语法错误时不再继续

    # 2. py_compile 编译检查
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = subprocess.run(
            ["python", "-m", "py_compile", tmp_path],
            capture_output=True, timeout=10,
            encoding="utf-8", errors="replace",
            startupinfo=_get_startupinfo(),
        )
        os.unlink(tmp_path)
        if result.returncode != 0:
            stderr = result.stderr or ""
            for line in stderr.strip().split("\n"):
                if "SyntaxError" in line or "IndentationError" in line or "TabError" in line:
                    m = re.search(r'line\s+(\d+)', line)
                    ln = int(m.group(1)) if m else 1
                    msg = line.split(":", 1)[-1].strip() if ":" in line else line
                    diagnostics.append({
                        "line": ln, "col": 0,
                        "message": msg, "severity": "error", "source": "py_compile"
                    })
    except Exception:
        pass

    # 3. 尝试 flake8（如果已安装）
    try:
        result = subprocess.run(
            ["python", "-m", "flake8", "--max-line-length=120", "--format=%(row)d:%(col)d:%(code)s:%(text)s", file_path],
            capture_output=True, timeout=15,
            encoding="utf-8", errors="replace",
            startupinfo=_get_startupinfo(),
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    row = int(parts[1])
                    col = int(parts[2])
                    text = parts[3].strip()
                    code = text.split(":", 1)[0].strip() if ":" in text else ""
                    # flake8 的 code: E/W=warning, F=error(pyflakes)
                    severity = "error" if code.startswith("F") else "warning"
                    diagnostics.append({
                        "line": row, "col": col,
                        "message": text, "severity": severity, "source": "flake8"
                    })
    except FileNotFoundError:
        pass  # flake8 未安装，跳过
    except Exception:
        pass

    return diagnostics


def _diagnose_javascript(file_path: str, content: str) -> list:
    """JavaScript/TypeScript 基础诊断"""
    diagnostics = []
    # 括号匹配检查
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    opens = set(pairs.values())
    for i, ch in enumerate(content):
        if ch in opens:
            stack.append((ch, i))
        elif ch in pairs:
            if not stack or stack[-1][0] != pairs[ch]:
                line, col = _line_col_from_offset(i, content)
                diagnostics.append({
                    "line": line, "col": col,
                    "message": f"括号不匹配: '{ch}'",
                    "severity": "error", "source": "syntax"
                })
            else:
                stack.pop()
    for ch, offset in stack:
        line, col = _line_col_from_offset(offset, content)
        diagnostics.append({
            "line": line, "col": col,
            "message": f"未闭合的 '{ch}'",
            "severity": "warning", "source": "syntax"
        })

    # 尝试 node --check
    try:
        result = subprocess.run(
            ["node", "--check", file_path],
            capture_output=True, timeout=10,
            encoding="utf-8", errors="replace",
            startupinfo=_get_startupinfo(),
        )
        if result.returncode != 0 and result.stderr:
            for line in result.stderr.strip().split("\n"):
                m = re.search(r':(\d+):(\d+)', line)
                if m:
                    diagnostics.append({
                        "line": int(m.group(1)), "col": int(m.group(2)),
                        "message": line.strip(), "severity": "error", "source": "node"
                    })
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return diagnostics


def _diagnose_json(file_path: str, content: str) -> list:
    """JSON 诊断"""
    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return [{
            "line": e.lineno, "col": e.colno,
            "message": e.msg, "severity": "error", "source": "json"
        }]
    return []


def _diagnose_markup(file_path: str, content: str) -> list:
    """HTML/XML 基础标签匹配检查"""
    diagnostics = []
    tag_pattern = re.compile(r'<(/?)(\w+)[^>]*?(/?)>')
    stack = []
    for m in tag_pattern.finditer(content):
        is_closing, tag_name, self_closing = m.group(1), m.group(2), m.group(3)
        if self_closing or tag_name in ('br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base', 'col', 'embed', 'source', 'track', 'wbr'):
            continue
        if is_closing:
            if not stack or stack[-1][0] != tag_name:
                line, col = _line_col_from_offset(m.start(), content)
                diagnostics.append({
                    "line": line, "col": col,
                    "message": f"未匹配的闭合标签 </{tag_name}>",
                    "severity": "warning", "source": "markup"
                })
            else:
                stack.pop()
        else:
            stack.append((tag_name, m.start()))
    for tag_name, offset in stack:
        line, col = _line_col_from_offset(offset, content)
        diagnostics.append({
            "line": line, "col": col,
            "message": f"未闭合的标签 <{tag_name}>",
            "severity": "warning", "source": "markup"
        })
    return diagnostics


# ── 2. go_to_definition — 跳转到定义 ─────────────────────────

class GoToDefinitionInput(BaseModel):
    file_path: str = Field(..., description="文件绝对路径")
    symbol: str = Field(..., description="要查找定义的符号名称，如函数名、类名、变量名")
    line: int = Field(0, description="符号所在行号（可选，提高精度）")


@tool(args_schema=GoToDefinitionInput)
def go_to_definition(file_path: str, symbol: str, line: int = 0) -> str:
    """查找指定符号（函数/类/变量）的定义位置。
    等同于 IDE 中右键 "Go to Definition"。
    目前支持 Python（基于 ast 分析），会在当前文件及同目录文件中搜索。

    Args:
        file_path: 当前文件路径
        symbol: 要查找的符号名称，例如 "MyClass" 或 "my_function"
        line: 符号在当前文件中出现的行号（可选，提高查找精度）
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    if ext != '.py':
        return f"提示: go_to_definition 目前仅支持 Python 文件"

    content = _read_file_safe(file_path)
    results = []

    # 在当前文件中搜索定义
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return f"错误: 文件有语法错误，无法解析"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                results.append({
                    "file": file_path,
                    "line": node.lineno,
                    "type": "class" if isinstance(node, ast.ClassDef) else "function",
                    "name": node.name,
                })
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    results.append({
                        "file": file_path,
                        "line": node.lineno,
                        "type": "variable",
                        "name": target.id,
                    })

    # 如果当前文件没找到，搜索同目录文件
    if not results:
        dir_path = os.path.dirname(file_path)
        for fname in os.listdir(dir_path):
            if not fname.endswith('.py') or fname == os.path.basename(file_path):
                continue
            fpath = os.path.join(dir_path, fname)
            fcontent = _read_file_safe(fpath)
            if not fcontent:
                continue
            try:
                ftree = ast.parse(fcontent, filename=fpath)
            except SyntaxError:
                continue
            for node in ast.walk(ftree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == symbol:
                        results.append({
                            "file": fpath,
                            "line": node.lineno,
                            "type": "class" if isinstance(node, ast.ClassDef) else "function",
                            "name": node.name,
                        })

    if not results:
        return f"未找到符号 '{symbol}' 的定义"

    lines = [f"找到 {len(results)} 处 '{symbol}' 的定义:\n"]
    for r in results:
        lines.append(f"  📌 {r['file']}:{r['line']}  [{r['type']}] {r['name']}")
    return "\n".join(lines)


# ── 3. get_hover_info — 悬停提示 ─────────────────────────────

class GetHoverInfoInput(BaseModel):
    file_path: str = Field(..., description="文件绝对路径")
    symbol: str = Field(..., description="要获取信息的符号名称")
    line: int = Field(0, description="符号所在行号（可选）")


@tool(args_schema=GetHoverInfoInput)
def get_hover_info(file_path: str, symbol: str, line: int = 0) -> str:
    """获取符号的悬停提示信息（类似 IDE 中鼠标悬停看到的文档）。
    包括函数签名、参数列表、docstring、类继承关系等。

    Args:
        file_path: 文件路径
        symbol: 符号名称
        line: 行号（可选）
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    if ext != '.py':
        return f"提示: get_hover_info 目前仅支持 Python 文件"

    content = _read_file_safe(file_path)
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return f"错误: 文件有语法错误"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            # 构建函数签名
            args = []
            for arg in node.args.args:
                arg_str = arg.arg
                if arg.annotation:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                args.append(arg_str)
            if node.args.vararg:
                args.append(f"*{node.args.vararg.arg}")
            if node.args.kwarg:
                args.append(f"**{node.args.kwarg.arg}")

            returns = ""
            if node.returns:
                returns = f" -> {ast.unparse(node.returns)}"

            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            signature = f"{prefix}def {symbol}({', '.join(args)}){returns}"

            # 获取 docstring
            docstring = ast.get_docstring(node) or ""

            # 装饰器
            decorators = []
            for dec in node.decorator_list:
                try:
                    decorators.append(f"@{ast.unparse(dec)}")
                except Exception:
                    pass

            lines = []
            if decorators:
                lines.extend(decorators)
            lines.append(f"📝 {signature}")
            lines.append(f"   定义位置: {file_path}:{node.lineno}")
            if docstring:
                lines.append(f"\n   {docstring}")
            return "\n".join(lines)

        elif isinstance(node, ast.ClassDef) and node.name == symbol:
            # 类信息
            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    pass

            base_str = f"({', '.join(bases)})" if bases else ""
            docstring = ast.get_docstring(node) or ""

            # 列出方法和属性
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            methods.append(f"{target.id} (属性)")

            lines = [f"📝 class {symbol}{base_str}"]
            lines.append(f"   定义位置: {file_path}:{node.lineno}")
            if docstring:
                lines.append(f"\n   {docstring}")
            if methods:
                lines.append(f"\n   成员: {', '.join(methods[:15])}")
                if len(methods) > 15:
                    lines.append(f"   ... 共 {len(methods)} 个成员")
            return "\n".join(lines)

    # 尝试从内置模块获取文档
    try:
        mod = importlib.import_module(symbol)
        doc = inspect.getdoc(mod)
        if doc:
            return f"📝 模块 {symbol}\n\n{doc[:2000]}"
    except Exception:
        pass

    try:
        obj = getattr(__builtins__, symbol, None)
        if obj is None:
            import builtins
            obj = getattr(builtins, symbol, None)
        if obj is not None:
            doc = inspect.getdoc(obj) or ""
            sig = ""
            try:
                sig = str(inspect.signature(obj))
            except (ValueError, TypeError):
                pass
            return f"📝 {symbol}{sig}\n\n{doc[:2000]}"
    except Exception:
        pass

    return f"未找到符号 '{symbol}' 的信息"


# ── 4. find_references — 查找引用 ────────────────────────────

class FindReferencesInput(BaseModel):
    file_path: str = Field(..., description="起始文件路径")
    symbol: str = Field(..., description="要查找引用的符号名称")
    search_dir: str = Field("", description="搜索目录（可选，默认为文件所在目录）")


@tool(args_schema=FindReferencesInput)
def find_references(file_path: str, symbol: str, search_dir: str = "") -> str:
    """在整个项目目录中查找指定符号的所有引用位置。
    等同于 IDE 中右键 "Find All References"。

    Args:
        file_path: 起始文件路径
        symbol: 符号名称
        search_dir: 搜索目录（可选，默认为文件所在目录）
    """
    file_path = file_path.strip().strip('"').strip("'")
    search_dir = search_dir.strip().strip('"').strip("'") if search_dir else ""
    if not search_dir:
        search_dir = os.path.dirname(file_path)

    if not os.path.isdir(search_dir):
        return f"错误: 搜索目录不存在 - {search_dir}"

    results = []
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build'}

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(root, fname)
            content = _read_file_safe(fpath)
            if not content:
                continue
            try:
                tree = ast.parse(content, filename=fpath)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == symbol:
                    results.append({"file": fpath, "line": node.lineno, "col": node.col_offset + 1, "context": "引用"})
                elif isinstance(node, ast.Attribute) and node.attr == symbol:
                    results.append({"file": fpath, "line": node.lineno, "col": node.col_offset + 1, "context": "属性引用"})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
                    results.append({"file": fpath, "line": node.lineno, "col": node.col_offset + 1, "context": "定义"})

    if not results:
        return f"未找到符号 '{symbol}' 的引用"

    # 去重
    seen = set()
    unique = []
    for r in results:
        key = (r["file"], r["line"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    lines = [f"找到 {len(unique)} 处 '{symbol}' 的引用:\n"]
    for r in unique:
        rel = os.path.relpath(r["file"], search_dir)
        lines.append(f"  {r['context']:4s} {rel}:{r['line']}:{r['col']}")
    return "\n".join(lines)


# ── 5. get_document_symbols — 获取文件符号列表 ─────────────────

class GetDocumentSymbolsInput(BaseModel):
    file_path: str = Field(..., description="文件绝对路径")


@tool(args_schema=GetDocumentSymbolsInput)
def get_document_symbols(file_path: str) -> str:
    """获取文件中的所有符号（类、函数、变量、导入等）。
    等同于 IDE 左侧的 "Outline" / "符号大纲" 面板。

    Args:
        file_path: 文件路径
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    content = _read_file_safe(file_path)

    if ext == '.py':
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as e:
            return f"错误: 语法错误 - {e.msg} (行 {e.lineno})"

        symbols = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                args = [a.arg for a in node.args.args[:5]]
                symbols.append(f"  📋 {kind} {node.name}({', '.join(args)})  [行 {node.lineno}]")
            elif isinstance(node, ast.ClassDef):
                bases = ', '.join(
                    getattr(b, 'id', getattr(b, 'attr', '')) for b in node.bases
                )
                base_str = f"({bases})" if bases else ""
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))][:8]
                symbols.append(f"  📋 class {node.name}{base_str}  [行 {node.lineno}]")
                for m in methods:
                    symbols.append(f"      └─ def {m}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.append(f"  📦 import {alias.name}  [行 {node.lineno}]")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ''
                names = ', '.join(a.name for a in node.names[:6])
                symbols.append(f"  📦 from {mod} import {names}  [行 {node.lineno}]")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.append(f"  📎 variable {target.id}  [行 {node.lineno}]")

        if not symbols:
            return f"{os.path.basename(file_path)} 无符号"
        return f"符号大纲: {os.path.basename(file_path)}\n" + "\n".join(symbols)

    else:
        # 基础正则匹配
        symbols = []
        # 函数定义
        for m in re.finditer(r'(?:function|def|func)\s+(\w+)\s*\(', content):
            line = content[:m.start()].count('\n') + 1
            symbols.append(f"  📋 function {m.group(1)}  [行 {line}]")
        # 类定义
        for m in re.finditer(r'class\s+(\w+)', content):
            line = content[:m.start()].count('\n') + 1
            symbols.append(f"  📋 class {m.group(1)}  [行 {line}]")
        # 变量定义（const/let/var）
        for m in re.finditer(r'(?:const|let|var)\s+(\w+)', content):
            line = content[:m.start()].count('\n') + 1
            symbols.append(f"  📎 variable {m.group(1)}  [行 {line}]")

        if not symbols:
            return f"{os.path.basename(file_path)} 无符号"
        return f"符号大纲: {os.path.basename(file_path)}\n" + "\n".join(symbols)


# ── 6. format_code — 代码格式化 ──────────────────────────────

class FormatCodeInput(BaseModel):
    file_path: str = Field(..., description="文件绝对路径")
    formatter: str = Field("auto", description="格式化工具：auto/black/autopep8/prettier，默认 auto")


@tool(args_schema=FormatCodeInput)
def format_code(file_path: str, formatter: str = "auto") -> str:
    """格式化代码文件。支持 Python（black/autopep8）和 JS/TS（prettier）。
    等同于 IDE 中的 Shift+Alt+F 格式化。

    Args:
        file_path: 文件路径
        formatter: 格式化工具，可选 auto/black/autopep8/prettier
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.py':
        # 尝试 black
        if formatter in ("auto", "black"):
            try:
                result = subprocess.run(
                    ["python", "-m", "black", "--quiet", file_path],
                    capture_output=True, timeout=15,
                    encoding="utf-8", errors="replace",
                    startupinfo=_get_startupinfo(),
                )
                if result.returncode == 0:
                    return f"✓ 已用 black 格式化 {os.path.basename(file_path)}"
            except FileNotFoundError:
                pass
            except Exception:
                pass

        # 尝试 autopep8
        if formatter in ("auto", "autopep8"):
            try:
                result = subprocess.run(
                    ["python", "-m", "autopep8", "--in-place", "--aggressive", file_path],
                    capture_output=True, timeout=15,
                    encoding="utf-8", errors="replace",
                    startupinfo=_get_startupinfo(),
                )
                if result.returncode == 0:
                    return f"✓ 已用 autopep8 格式化 {os.path.basename(file_path)}"
            except FileNotFoundError:
                pass
            except Exception:
                pass

        return f"提示: 未安装 black 或 autopep8，请运行 pip install black autopep8"

    elif ext in ('.js', '.jsx', '.ts', '.tsx', '.json', '.css', '.scss', '.html'):
        try:
            result = subprocess.run(
                ["npx", "--yes", "prettier", "--write", file_path],
                capture_output=True, timeout=30,
                encoding="utf-8", errors="replace",
                startupinfo=_get_startupinfo(),
            )
            if result.returncode == 0:
                return f"✓ 已用 prettier 格式化 {os.path.basename(file_path)}"
            return f"格式化失败: {result.stderr}"
        except FileNotFoundError:
            return f"提示: 未安装 prettier，请运行 npm install -g prettier"
        except Exception as e:
            return f"格式化失败: {e}"

    return f"提示: 文件类型 {ext} 暂不支持格式化"
