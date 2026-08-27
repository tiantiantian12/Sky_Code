"""
代码审查 & Bug Finder 工具集
为 Agent 提供自动化代码质量分析能力：
  - review_code       : AI 辅助代码审查（模式匹配 + 启发式规则）
  - find_bugs         : 静态分析查找潜在 Bug
  - security_scan     : 安全漏洞扫描
  - check_code_smells : 代码坏味道检测
  - get_code_metrics  : 代码复杂度指标

设计理念：
  不依赖外部 LLM，而是使用 Python ast 模块 + 启发式规则
  做静态分析，快速发现问题并返回结构化报告。
  Agent 可以调用这些工具发现问题，再自己生成修复建议。
"""

import os
import ast
import re
import json
import subprocess
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ── 辅助 ─────────────────────────────────────────────────────

def _get_startupinfo():
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


# ── 安全规则定义 ─────────────────────────────────────────────

SECURITY_RULES = [
    {
        "id": "SEC001",
        "pattern": r"eval\s*\(",
        "severity": "critical",
        "message": "使用 eval() 可能导致代码注入",
        "suggestion": "使用 ast.literal_eval() 或 json.loads() 替代",
    },
    {
        "id": "SEC002",
        "pattern": r"exec\s*\(",
        "severity": "critical",
        "message": "使用 exec() 可能导致代码注入",
        "suggestion": "重构代码避免动态执行",
    },
    {
        "id": "SEC003",
        "pattern": r"subprocess\.(Popen|run|call)\s*\(.*shell\s*=\s*True",
        "severity": "high",
        "message": "subprocess 使用 shell=True 可能导致命令注入",
        "suggestion": "避免 shell=True，使用列表传参",
    },
    {
        "id": "SEC004",
        "pattern": r"pickle\.loads?\s*\(",
        "severity": "high",
        "message": "pickle 反序列化不安全数据可导致任意代码执行",
        "suggestion": "使用 json 或其他安全格式",
    },
    {
        "id": "SEC005",
        "pattern": r"os\.system\s*\(",
        "severity": "high",
        "message": "os.system() 可能导致命令注入",
        "suggestion": "使用 subprocess.run() 并避免 shell=True",
    },
    {
        "id": "SEC006",
        "pattern": r"(password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
        "severity": "high",
        "message": "代码中硬编码了密钥/密码",
        "suggestion": "使用环境变量或配置文件管理敏感信息",
    },
    {
        "id": "SEC007",
        "pattern": r"verify\s*=\s*False",
        "severity": "medium",
        "message": "禁用了 SSL 证书验证",
        "suggestion": "生产环境必须启用证书验证",
    },
    {
        "id": "SEC008",
        "pattern": r"\bYAML\b.*\bload\s*\([^)]*\)\s*(?!Loader=)",
        "severity": "medium",
        "message": "yaml.load() 不安全，可能导致任意对象构造",
        "suggestion": "使用 yaml.safe_load() 替代",
    },
]


# ── Bug 规则定义 ─────────────────────────────────────────────

BUG_RULES = [
    {
        "id": "BUG001",
        "pattern": r"except\s*:",
        "severity": "warning",
        "message": "裸 except 捕获所有异常（包括 KeyboardInterrupt、SystemExit）",
        "suggestion": "使用 except Exception: 或更具体的异常类型",
    },
    {
        "id": "BUG002",
        "pattern": r"except\s+Exception\s*:\s*\n\s*pass",
        "severity": "warning",
        "message": "捕获异常后直接 pass，吞掉了错误",
        "suggestion": "至少记录日志或重新抛出",
    },
    {
        "id": "BUG003",
        "pattern": r"==\s*None|!=\s*None",
        "severity": "info",
        "message": "使用 == 比较 None，应使用 is None",
        "suggestion": "使用 'is None' 或 'is not None'",
    },
    {
        "id": "BUG004",
        "pattern": r"==\s*True|==\s*False|!=\s*True|!=\s*False",
        "severity": "info",
        "message": "使用 == 比较 True/False",
        "suggestion": "直接使用变量或 not 变量",
    },
    {
        "id": "BUG005",
        "pattern": r"\bglobal\s+\w+",
        "severity": "warning",
        "message": "使用 global 修改全局变量",
        "suggestion": "考虑使用类属性或返回值替代",
    },
    {
        "id": "BUG006",
        "pattern": r"mutable\s+default.*\[\]|def\s+\w+\([^)]*=\s*\[\]",
        "severity": "warning",
        "message": "函数参数使用可变默认值 []",
        "suggestion": "使用 None 作为默认值，在函数内创建列表",
    },
]


# ── 1. review_code — 代码审查 ────────────────────────────────

class ReviewCodeInput(BaseModel):
    file_path: str = Field(..., description="要审查的文件绝对路径")
    focus: str = Field("all", description="审查重点：all/security/bugs/style/performance，默认 all")


@tool(args_schema=ReviewCodeInput)
def review_code(file_path: str, focus: str = "all") -> str:
    """对代码文件进行全面审查，发现安全问题、潜在 Bug、代码风格问题和性能隐患。
    类似于 PR Review，但自动化执行。

    Args:
        file_path: 文件路径
        focus: 审查重点，可选 all/security/bugs/style/performance
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    ext = os.path.splitext(file_path)[1].lower()

    findings = []

    if ext == '.py':
        if focus in ("all", "security"):
            findings.extend(_scan_security(content, file_path))
        if focus in ("all", "bugs"):
            findings.extend(_scan_bugs(content, file_path))
        if focus in ("all", "style"):
            findings.extend(_scan_style(content, file_path))
        if focus in ("all", "performance"):
            findings.extend(_scan_performance(content, file_path))
    elif ext in ('.js', '.jsx', '.ts', '.tsx'):
        findings.extend(_scan_js(content, file_path))
    else:
        return f"提示: 文件类型 {ext} 暂不支持代码审查"

    if not findings:
        return f"✓ {os.path.basename(file_path)} 审查通过，未发现问题"

    # 按严重程度排序
    severity_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
    findings.sort(key=lambda x: severity_order.get(x["severity"], 99))

    # 统计
    stats = {}
    for f in findings:
        stats[f["severity"]] = stats.get(f["severity"], 0) + 1

    lines = [f"代码审查报告: {os.path.basename(file_path)}"]
    lines.append(f"问题统计: " + " / ".join(f"{k}: {v}" for k, v in sorted(stats.items(), key=lambda x: severity_order.get(x[0], 99))))
    lines.append("")

    for f in findings:
        icon = {"critical": "🔴", "high": "🟠", "warning": "🟡", "info": "🔵"}.get(f["severity"], "⚪")
        lines.append(f"{icon} [{f['id']}] 行 {f['line']} — {f['message']}")
        if f.get("suggestion"):
            lines.append(f"   💡 建议: {f['suggestion']}")
        lines.append("")

    return "\n".join(lines)


def _scan_security(content: str, file_path: str) -> list:
    findings = []
    for rule in SECURITY_RULES:
        for m in re.finditer(rule["pattern"], content, re.IGNORECASE):
            line = content[:m.start()].count('\n') + 1
            findings.append({
                "id": rule["id"],
                "line": line,
                "severity": rule["severity"],
                "message": rule["message"],
                "suggestion": rule["suggestion"],
            })
    return findings


def _scan_bugs(content: str, file_path: str) -> list:
    findings = []
    lines = content.split('\n')

    for i, line in enumerate(lines, 1):
        for rule in BUG_RULES:
            if re.search(rule["pattern"], line):
                findings.append({
                    "id": rule["id"],
                    "line": i,
                    "severity": rule["severity"],
                    "message": rule["message"],
                    "suggestion": rule["suggestion"],
                })

    # AST 级别检查
    try:
        tree = ast.parse(content, filename=file_path)
        for node in ast.walk(tree):
            # 可变默认参数
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        findings.append({
                            "id": "BUG006",
                            "line": node.lineno,
                            "severity": "warning",
                            "message": f"函数 {node.name} 使用可变默认参数",
                            "suggestion": "使用 None 作为默认值，在函数内创建可变对象",
                        })
            # 未使用的导入
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split('.')[0]
                    # 简单检查：名称是否在文件中除 import 行之外出现
                    if content.count(name) <= 1:  # 只在 import 行出现
                        findings.append({
                            "id": "BUG007",
                            "line": node.lineno,
                            "severity": "info",
                            "message": f"可能未使用的导入: {name}",
                            "suggestion": "移除未使用的导入",
                        })
    except SyntaxError:
        pass

    return findings


def _scan_style(content: str, file_path: str) -> list:
    findings = []
    lines = content.split('\n')

    for i, line in enumerate(lines, 1):
        # 行过长
        if len(line) > 120:
            findings.append({
                "id": "STY001",
                "line": i,
                "severity": "info",
                "message": f"行过长 ({len(line)} 字符 > 120)",
                "suggestion": "考虑换行或缩短变量名",
            })
        # 行尾空格
        if line != line.rstrip():
            findings.append({
                "id": "STY002",
                "line": i,
                "severity": "info",
                "message": "行尾有多余空格",
                "suggestion": "移除行尾空格",
            })
        # Tab 混合空格
        if '\t' in line and '    ' in line:
            findings.append({
                "id": "STY003",
                "line": i,
                "severity": "warning",
                "message": "Tab 和空格混用",
                "suggestion": "统一使用空格缩进（PEP 8 推荐 4 空格）",
            })

    return findings


def _scan_performance(content: str, file_path: str) -> list:
    findings = []
    lines = content.split('\n')

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 字符串拼接在循环中
        if re.search(r'\+=\s*[\'"]', stripped) and i > 1:
            prev_stripped = lines[i - 2].strip() if i >= 2 else ""
            if prev_stripped.startswith('for ') or prev_stripped.startswith('while '):
                findings.append({
                    "id": "PERF001",
                    "line": i,
                    "severity": "warning",
                    "message": "在循环中使用字符串拼接 (+=) 性能较差",
                    "suggestion": "使用列表 append + join 替代",
                })
        # 在循环中打开文件
        if re.search(r'open\s*\(', stripped):
            # 检查上方是否有循环
            for j in range(max(0, i - 5), i - 1):
                if j < len(lines):
                    prev = lines[j].strip()
                    if prev.startswith('for ') or prev.startswith('while '):
                        findings.append({
                            "id": "PERF002",
                            "line": i,
                            "severity": "warning",
                            "message": "在循环中打开文件，应考虑批量处理",
                            "suggestion": "将文件打开移到循环外部",
                        })
                        break

    # AST 级别：检查嵌套循环
    try:
        tree = ast.parse(content, filename=file_path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                for child in ast.walk(node):
                    if child is not node and isinstance(child, (ast.For, ast.While)):
                        findings.append({
                            "id": "PERF003",
                            "line": child.lineno,
                            "severity": "info",
                            "message": "嵌套循环，时间复杂度可能较高",
                            "suggestion": "考虑优化算法或使用数据结构（如 dict/set）",
                        })
                        break
    except SyntaxError:
        pass

    return findings


def _scan_js(content: str, file_path: str) -> list:
    findings = []
    js_rules = [
        (r"eval\s*\(", "critical", "JS001", "eval() 可能导致代码注入", "避免使用 eval"),
        (r"document\.write\s*\(", "warning", "JS002", "document.write() 不安全", "使用 DOM API 替代"),
        (r"innerHTML\s*=", "warning", "JS003", "innerHTML 赋值可能导致 XSS", "使用 textContent 或 DOM API"),
        (r"var\s+\w+", "info", "JS004", "使用 var 声明变量", "使用 let 或 const 替代"),
        (r"==\s*(?!=", "info", "JS005", "使用 == 而非 ===", "使用严格相等 ==="),
    ]
    for pattern, severity, rid, message, suggestion in js_rules:
        for m in re.finditer(pattern, content):
            line = content[:m.start()].count('\n') + 1
            findings.append({
                "id": rid,
                "line": line,
                "severity": severity,
                "message": message,
                "suggestion": suggestion,
            })
    return findings


# ── 2. find_bugs — 查找 Bug ──────────────────────────────────

class FindBugsInput(BaseModel):
    file_path: str = Field(..., description="要分析的文件路径")
    include_style: bool = Field(False, description="是否包含风格问题，默认 False 只关注 Bug")


@tool(args_schema=FindBugsInput)
def find_bugs(file_path: str, include_style: bool = False) -> str:
    """静态分析查找文件中的潜在 Bug。
    聚焦于可能导致运行时错误的问题：空指针、类型错误、未处理异常、资源泄漏等。

    Args:
        file_path: 文件路径
        include_style: 是否包含风格问题，默认 False
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    ext = os.path.splitext(file_path)[1].lower()

    bugs = []
    if ext == '.py':
        bugs.extend(_scan_bugs(content, file_path))
        if include_style:
            bugs.extend(_scan_style(content, file_path))
    else:
        return f"提示: find_bugs 目前仅支持 Python 文件"

    if not bugs:
        return f"✓ {os.path.basename(file_path)} 未发现潜在 Bug"

    severity_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
    bugs.sort(key=lambda x: severity_order.get(x["severity"], 99))

    lines = [f"Bug 分析报告: {os.path.basename(file_path)} ({len(bugs)} 个问题)\n"]
    for b in bugs:
        icon = {"critical": "🔴", "high": "🟠", "warning": "🟡", "info": "🔵"}.get(b["severity"], "⚪")
        lines.append(f"{icon} [{b['id']}] 行 {b['line']} — {b['message']}")
        if b.get("suggestion"):
            lines.append(f"   💡 {b['suggestion']}")
    return "\n".join(lines)


# ── 3. security_scan — 安全扫描 ──────────────────────────────

class SecurityScanInput(BaseModel):
    file_path: str = Field(..., description="要扫描的文件路径，或目录路径")
    is_directory: bool = Field(False, description="是否扫描整个目录，默认 False")


@tool(args_schema=SecurityScanInput)
def security_scan(file_path: str, is_directory: bool = False) -> str:
    """扫描代码中的安全漏洞。检测常见安全问题：注入、硬编码密钥、不安全反序列化等。

    Args:
        file_path: 文件或目录路径
        is_directory: 是否扫描整个目录
    """
    file_path = file_path.strip().strip('"').strip("'")

    if is_directory:
        if not os.path.isdir(file_path):
            return f"错误: 目录不存在 - {file_path}"
        all_findings = []
        skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode'}
        for root, dirs, files in os.walk(file_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if not fname.endswith(('.py', '.js', '.ts', '.jsx', '.tsx')):
                    continue
                fpath = os.path.join(root, fname)
                content = _read_file_safe(fpath)
                for rule in SECURITY_RULES:
                    for m in re.finditer(rule["pattern"], content, re.IGNORECASE):
                        line = content[:m.start()].count('\n') + 1
                        all_findings.append({
                            "file": fpath,
                            "line": line,
                            **{k: v for k, v in rule.items() if k != "pattern"}
                        })

        if not all_findings:
            return f"✓ 目录扫描完成，未发现安全问题"

        all_findings.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x["severity"], 99))
        lines = [f"安全扫描报告: {file_path} ({len(all_findings)} 个问题)\n"]
        for f in all_findings:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(f["severity"], "⚪")
            rel = os.path.relpath(f["file"], file_path)
            lines.append(f"{icon} [{f['id']}] {rel}:{f['line']} — {f['message']}")
            if f.get("suggestion"):
                lines.append(f"   💡 {f['suggestion']}")
        return "\n".join(lines)
    else:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        content = _read_file_safe(file_path)
        findings = _scan_security(content, file_path)

        if not findings:
            return f"✓ {os.path.basename(file_path)} 未发现安全问题"

        findings.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x["severity"], 99))
        lines = [f"安全扫描报告: {os.path.basename(file_path)} ({len(findings)} 个问题)\n"]
        for f in findings:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(f["severity"], "⚪")
            lines.append(f"{icon} [{f['id']}] 行 {f['line']} — {f['message']}")
            if f.get("suggestion"):
                lines.append(f"   💡 {f['suggestion']}")
        return "\n".join(lines)


# ── 4. check_code_smells — 代码坏味道 ────────────────────────

class CheckCodeSmellsInput(BaseModel):
    file_path: str = Field(..., description="文件路径")


@tool(args_schema=CheckCodeSmellsInput)
def check_code_smells(file_path: str) -> str:
    """检测代码坏味道（Code Smells）：过长函数、过大类、重复代码、过深嵌套等。
    帮助识别需要重构的代码。

    Args:
        file_path: 文件路径
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext != '.py':
        return f"提示: check_code_smells 目前仅支持 Python 文件"

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as e:
        return f"错误: 语法错误 - {e.msg} (行 {e.lineno})"

    smells = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 函数过长
            end_line = getattr(node, 'end_lineno', node.lineno + 1)
            length = end_line - node.lineno
            if length > 50:
                smells.append({
                    "line": node.lineno,
                    "severity": "warning" if length > 80 else "info",
                    "type": "长函数",
                    "message": f"函数 '{node.name}' 有 {length} 行（建议 < 50 行）",
                    "suggestion": "考虑拆分为更小的函数",
                })

            # 参数过多
            arg_count = len(node.args.args) + len(node.args.kwonlyargs)
            if arg_count > 5:
                smells.append({
                    "line": node.lineno,
                    "severity": "info",
                    "type": "参数过多",
                    "message": f"函数 '{node.name}' 有 {arg_count} 个参数（建议 < 5）",
                    "suggestion": "考虑使用参数对象或 **kwargs",
                })

            # 嵌套过深
            max_depth = _get_nesting_depth(node)
            if max_depth > 4:
                smells.append({
                    "line": node.lineno,
                    "severity": "warning" if max_depth > 6 else "info",
                    "type": "嵌套过深",
                    "message": f"函数 '{node.name}' 最大嵌套深度 {max_depth}（建议 < 4）",
                    "suggestion": "使用提前返回或提取子函数减少嵌套",
                })

        elif isinstance(node, ast.ClassDef):
            # 类过大
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if len(methods) > 20:
                smells.append({
                    "line": node.lineno,
                    "severity": "info",
                    "type": "过大类",
                    "message": f"类 '{node.name}' 有 {len(methods)} 个方法（建议 < 20）",
                    "suggestion": "考虑拆分为更小的类",
                })

    if not smells:
        return f"✓ {os.path.basename(file_path)} 未检测到代码坏味道"

    lines = [f"代码坏味道报告: {os.path.basename(file_path)} ({len(smells)} 个)\n"]
    for s in smells:
        icon = {"warning": "🟡", "info": "🔵"}.get(s["severity"], "⚪")
        lines.append(f"{icon} 行 {s['line']} [{s['type']}] {s['message']}")
        if s.get("suggestion"):
            lines.append(f"   💡 {s['suggestion']}")
    return "\n".join(lines)


def _get_nesting_depth(node, current=0):
    """递归计算最大嵌套深度"""
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.For, ast.While, ast.If, ast.With, ast.Try)):
            depth = _get_nesting_depth(child, current + 1)
            if depth > max_depth:
                max_depth = depth
        else:
            depth = _get_nesting_depth(child, current)
            if depth > max_depth:
                max_depth = depth
    return max_depth


# ── 5. get_code_metrics — 代码指标 ───────────────────────────

class GetCodeMetricsInput(BaseModel):
    file_path: str = Field(..., description="文件路径")


@tool(args_schema=GetCodeMetricsInput)
def get_code_metrics(file_path: str) -> str:
    """计算代码指标：行数、圈复杂度、函数数、类数、注释率等。
    用于评估代码质量和可维护性。

    Args:
        file_path: 文件路径
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    lines = content.split('\n')

    # 基础指标
    total_lines = len(lines)
    blank_lines = sum(1 for l in lines if not l.strip())
    comment_lines = 0
    code_lines = 0

    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            comment_lines += 1
            if '*/' in stripped or '"""' in stripped or "'''" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith('#') or stripped.startswith('//'):
            comment_lines += 1
        elif stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith('/*'):
            comment_lines += 1
            if not (stripped.count('"""') >= 2 or stripped.count("'''") >= 2 or '*/' in stripped):
                in_block_comment = True
        elif stripped:
            code_lines += 1

    metrics = {
        "总行数": total_lines,
        "代码行": code_lines,
        "注释行": comment_lines,
        "空行": blank_lines,
        "注释率": f"{comment_lines / max(code_lines, 1) * 100:.1f}%",
    }

    if ext == '.py':
        try:
            tree = ast.parse(content, filename=file_path)
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]

            metrics["函数数"] = len(functions)
            metrics["类数"] = len(classes)
            metrics["导入数"] = len(imports)

            # 圈复杂度（简化版：计算 if/for/while/and/or/try 的数量 + 1）
            total_complexity = 0
            for func in functions:
                complexity = 1
                for node in ast.walk(func):
                    if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler)):
                        complexity += 1
                    elif isinstance(node, ast.BoolOp):
                        complexity += len(node.values) - 1
                total_complexity += complexity

            if functions:
                metrics["平均圈复杂度"] = f"{total_complexity / len(functions):.1f}"
                max_complexity = max(
                    (1 + sum(1 for n in ast.walk(f) if isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler)))
                     + sum(len(n.values) - 1 for n in ast.walk(f) if isinstance(n, ast.BoolOp))
                     for f in functions)
                )
                metrics["最大圈复杂度"] = max_complexity
                if max_complexity > 10:
                    metrics["⚠ 复杂度警告"] = f"最大圈复杂度 {max_complexity} 过高，建议拆分函数"

        except SyntaxError:
            metrics["⚠ 解析错误"] = "文件有语法错误，无法计算 AST 指标"

    # 格式化输出
    lines = [f"代码指标: {os.path.basename(file_path)}\n"]
    for k, v in metrics.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
