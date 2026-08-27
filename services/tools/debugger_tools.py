"""
调试器集成工具集
为 Agent 提供代码调试能力：
  - debug_script        : 运行脚本并捕获详细调试信息（变量、调用栈、异常）
  - set_breakpoint      : 在指定行设置断点并运行
  - get_call_stack      : 获取异常调用栈
  - inspect_variables   : 在运行时检查变量
  - run_with_trace      : 带执行追踪运行脚本

设计理念：
  使用 Python pdb / trace 模块实现轻量级调试。
  不依赖外部调试器进程，通过子进程 + 输出解析实现。
"""

import os
import re
import ast
import json
import subprocess
import tempfile
import sys
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _get_startupinfo():
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


# ── 1. debug_script — 调试运行脚本 ───────────────────────────

class DebugScriptInput(BaseModel):
    file_path: str = Field(..., description="Python 脚本绝对路径")
    args: str = Field("", description="脚本参数（可选）")
    working_dir: str = Field("", description="工作目录（可选）")
    capture_locals: bool = Field(True, description="是否捕获局部变量，默认 True")


@tool(args_schema=DebugScriptInput)
def debug_script(file_path: str, args: str = "", working_dir: str = "",
                 capture_locals: bool = True) -> str:
    """以调试模式运行 Python 脚本，捕获详细错误信息。
    发生异常时会输出完整的调用栈、局部变量值和错误上下文。
    比 execute_code 提供更丰富的调试信息。

    Args:
        file_path: Python 脚本路径
        args: 命令行参数
        working_dir: 工作目录
        capture_locals: 是否捕获局部变量值
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"
    if not file_path.lower().endswith('.py'):
        return f"错误: 仅支持 Python 脚本"

    working_dir = working_dir.strip().strip('"').strip("'") if working_dir else os.path.dirname(file_path)
    working_dir = os.path.abspath(working_dir) if working_dir else os.getcwd()
    file_path = os.path.abspath(file_path)

    # 创建调试包装脚本
    wrapper_code = '''import sys
import traceback
import json
import os
import inspect

script_path = {script_path!r}
script_args = {script_args!r}
capture_locals = {capture_locals!r}

# 设置参数
sys.argv = [script_path] + script_args

# 切换工作目录
os.chdir({working_dir!r})

# 将脚本目录加入 path
script_dir = os.path.dirname(script_path)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

try:
    with open(script_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    # 编译并执行
    compiled = compile(code, script_path, 'exec')
    exec(compiled, {{"__name__": "__main__", "__file__": script_path}})
    
    print("\\n✅ 脚本执行成功")

except SystemExit as e:
    print(f"\\n⚠ 脚本调用 sys.exit({{e.code}})")

except Exception as e:
    print(f"\\n❌ 异常类型: {{type(e).__name__}}")
    print(f"❌ 异常信息: {{e}}")
    print()
    
    # 获取完整的调用栈
    tb = traceback.extract_tb(e.__traceback__)
    print("📋 调用栈:")
    for i, frame in enumerate(tb):
        print(f"  {{i}}. {{frame.filename}}:{{frame.lineno}} in {{frame.name}}")
        if frame.line:
            print(f"     → {{frame.line}}")
    
    print()
    
    # 捕获局部变量
    if capture_locals:
        try:
            # 获取异常发生帧的局部变量
            tb_frame = e.__traceback__.tb_frame
            while tb_frame.tb_next:
                tb_frame = tb_frame.tb_next
            
            locals_dict = {{}}
            for k, v in tb_frame.tb_frame.f_locals.items():
                if k.startswith('__'):
                    continue
                try:
                    # 尝试获取变量的字符串表示
                    repr_v = repr(v)
                    if len(repr_v) > 200:
                        repr_v = repr_v[:200] + "..."
                    locals_dict[k] = repr_v
                except Exception:
                    locals_dict[k] = "<无法序列化>"
            
            if locals_dict:
                print("🔍 局部变量 (异常发生时):")
                for k, v in locals_dict.items():
                    print(f"  {{k}} = {{v}}")
            else:
                print("🔍 无局部变量")
        except Exception as ex:
            print(f"🔍 获取局部变量失败: {{ex}}")
    
    # 全局变量中的关键信息
    print()
    print("📊 关键全局变量:")
    g = tb_frame.tb_frame.f_globals if 'tb_frame' in dir() else {{}}
    for k in ['__name__', '__file__']:
        if k in g:
            print(f"  {{k}} = {{g[k]!r}}")
'''.format(
        script_path=file_path,
        script_args=args.split() if args else [],
        capture_locals=capture_locals,
        working_dir=working_dir,
    )

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
        tmp.write(wrapper_code)
        wrapper_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, wrapper_path],
            capture_output=True,
            timeout=30,
            cwd=working_dir,
            encoding='utf-8',
            errors='replace',
            startupinfo=_get_startupinfo(),
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr

        return output[:8000] if output else "(无输出)"

    except subprocess.TimeoutExpired:
        return "错误: 脚本执行超时 (30秒)。可能存在死循环或等待输入。"
    finally:
        try:
            os.unlink(wrapper_path)
        except Exception:
            pass


# ── 2. set_breakpoint — 设置断点运行 ─────────────────────────

class SetBreakpointInput(BaseModel):
    file_path: str = Field(..., description="脚本路径")
    line: int = Field(..., description="断点行号")
    condition: str = Field("", description="断点条件表达式（可选），如 'x > 10'")
    args: str = Field("", description="脚本参数（可选）")


@tool(args_schema=SetBreakpointInput)
def set_breakpoint(file_path: str, line: int, condition: str = "", args: str = "") -> str:
    """在指定行设置断点并运行脚本，断点处会输出变量状态。
    使用 Python 的 breakpoint() 机制，在断点行暂停并收集上下文。

    Args:
        file_path: 脚本路径
        line: 断点行号
        condition: 条件表达式（可选），满足条件才暂停
        args: 脚本参数
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    content = _read_file_safe(file_path)
    lines = content.split('\n')

    if line < 1 or line > len(lines):
        return f"错误: 行号 {line} 超出范围 (1-{len(lines)})"

    # 注入断点追踪代码
    # 在目标行前插入断点检查
    bp_code = f'    # === BREAKPOINT INJECTED ===\n'
    bp_code += f'    import sys as _bp_sys\n'
    bp_code += f'    _bp_frame = _bp_sys._getframe()\n'
    if condition:
        bp_code += f'    try:\n'
        bp_code += f'        if {condition}:\n'
        bp_code += f'            print("\\n🔴 断点命中: {file_path}:{line} (条件: {condition})")\n'
        bp_code += f'            print("🔍 局部变量:")\n'
        bp_code += f'            for _k, _v in _bp_frame.f_locals.items():\n'
        bp_code += f'                if not _k.startswith("_bp"):\n'
        bp_code += f'                    try: print(f"  {{_k}} = {{repr(_v)[:200]}}")\n'
        bp_code += f'                    except: print(f"  {{_k}} = <无法序列化>")\n'
        bp_code += f'    except Exception as _bp_ex:\n'
        bp_code += f'        print(f"⚠ 断点条件评估失败: {{_bp_ex}}")\n'
    else:
        bp_code += f'    print("\\n🔴 断点命中: {file_path}:{line}")\n'
        bp_code += f'    print("🔍 局部变量:")\n'
        bp_code += f'    for _k, _v in _bp_frame.f_locals.items():\n'
        bp_code += f'        if not _k.startswith("_bp"):\n'
        bp_code += f'            try: print(f"  {{_k}} = {{repr(_v)[:200]}}")\n'
        bp_code += f'            except: print(f"  {{_k}} = <无法序列化>")\n'

    # 确定缩进
    target_line = lines[line - 1]
    indent = len(target_line) - len(target_line.lstrip())
    bp_code_indented = '\n'.join(' ' * indent + l if l.strip() else l for l in bp_code.split('\n'))

    # 插入断点代码
    new_lines = lines[:line - 1] + [bp_code_indented] + lines[line - 1:]
    new_content = '\n'.join(new_lines)

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
        tmp.write(new_content)
        tmp_path = tmp.name

    try:
        script_args = args.split() if args else []
        result = subprocess.run(
            [sys.executable, tmp_path] + script_args,
            capture_output=True,
            timeout=30,
            cwd=os.path.dirname(file_path),
            encoding='utf-8',
            errors='replace',
            startupinfo=_get_startupinfo(),
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            # 过滤掉断点注入相关的 stderr
            stderr_lines = [l for l in result.stderr.split('\n') if '_bp' not in l and tmp_path not in l]
            filtered = '\n'.join(stderr_lines).strip()
            if filtered:
                output += "\n[stderr]\n" + filtered

        if not output:
            output = "(无输出 - 断点可能未被触发，请检查行号和执行路径)"

        return output[:8000]

    except subprocess.TimeoutExpired:
        return "错误: 脚本执行超时 (30秒)"
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ── 3. get_call_stack — 获取调用栈 ───────────────────────────

class GetCallStackInput(BaseModel):
    file_path: str = Field(..., description="脚本路径")
    args: str = Field("", description="脚本参数（可选）")


@tool(args_schema=GetCallStackInput)
def get_call_stack(file_path: str, args: str = "") -> str:
    """运行脚本并在异常时输出详细的调用栈信息。
    比标准 traceback 更详细，包含每一帧的局部变量。

    Args:
        file_path: 脚本路径
        args: 脚本参数
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    wrapper = '''import sys
import os
import traceback

sys.argv = {argv!r}
os.chdir({cwd!r})
script_dir = os.path.dirname({fp!r})
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

try:
    with open({fp!r}, 'r', encoding='utf-8') as f:
        exec(compile(f.read(), {fp!r}, 'exec'), {{"__name__": "__main__", "__file__": {fp!r}}})
    print("✅ 执行成功，无异常")
except Exception as e:
    print(f"❌ {type(e).__name__}: {e}\\n")
    tb = e.__traceback__
    frames = []
    while tb:
        frames.append(tb)
        tb = tb.tb_next
    
    print(f"📋 调用栈 ({len(frames)} 帧):\\n")
    for i, frame in enumerate(frames):
        f = frame.tb_frame
        print(f"━━━ 帧 {i}: {f.f_code.co_filename}:{frame.tb_lineno} in {f.f_code.co_name} ━━━")
        
        # 源代码行
        try:
            with open(f.f_code.co_filename, 'r', encoding='utf-8', errors='replace') as sf:
                src_lines = sf.readlines()
            if 0 < frame.tb_lineno <= len(src_lines):
                src = src_lines[frame.tb_lineno - 1].rstrip()
                print(f"  源码: {src}")
        except:
            pass
        
        # 局部变量
        locals_str = []
        for k, v in f.f_locals.items():
            if k.startswith('__'):
                continue
            try:
                rv = repr(v)
                if len(rv) > 150:
                    rv = rv[:150] + '...'
                locals_str.append(f"    {k} = {rv}")
            except:
                locals_str.append(f"    {k} = <无法序列化>")
        if locals_str:
            print(f"  局部变量:")
            print('\\n'.join(locals_str))
        print()
'''.format(
        argv=[file_path] + (args.split() if args else []),
        cwd=os.path.dirname(file_path),
        fp=file_path,
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
        tmp.write(wrapper)
        wrapper_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, wrapper_path],
            capture_output=True, timeout=30,
            cwd=os.path.dirname(file_path),
            encoding='utf-8', errors='replace',
            startupinfo=_get_startupinfo(),
        )
        output = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
        return output[:8000] if output else "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误: 执行超时"
    finally:
        try:
            os.unlink(wrapper_path)
        except Exception:
            pass


# ── 4. run_with_trace — 执行追踪 ─────────────────────────────

class RunWithTraceInput(BaseModel):
    file_path: str = Field(..., description="脚本路径")
    max_lines: int = Field(100, description="最多输出追踪行数，默认 100")
    args: str = Field("", description="脚本参数（可选）")


@tool(args_schema=RunWithTraceInput)
def run_with_trace(file_path: str, max_lines: int = 100, args: str = "") -> str:
    """带执行追踪运行脚本，输出每一行被执行的代码。
    用于理解脚本执行流程、定位死循环或跳过的代码块。

    Args:
        file_path: 脚本路径
        max_lines: 最多输出追踪行数
        args: 脚本参数
    """
    file_path = file_path.strip().strip('"').strip("'")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在 - {file_path}"

    wrapper = f'''import sys
import os
import trace

sys.argv = {[file_path] + (args.split() if args else [])!r}
os.chdir({os.path.dirname(file_path)!r})

tracer = trace.Trace(
    count=0,
    trace=1,
    ignoredirs=[sys.prefix, os.path.dirname(os.__file__)],
)

import io
from contextlib import redirect_stdout

buf = io.StringIO()
try:
    with redirect_stdout(buf):
        tracer.runfunc(exec, compile(open({file_path!r}, encoding='utf-8').read(), {file_path!r}, 'exec'), {{"__name__": "__main__"}})
except Exception as e:
    print(f"\\n❌ 异常: {{type(e).__name__}}: {{e}}", file=sys.stderr)

output = buf.getvalue()
lines = output.split('\\n')
if len(lines) > {max_lines}:
    print('\\n'.join(lines[:{max_lines}]))
    print(f"... (共 {{len(lines)}} 行追踪，已截断)")
else:
    print(output)
'''.replace('{file_path!r}', repr(file_path))

    # 简化版：直接用 python -m trace
    try:
        script_args = args.split() if args else []
        result = subprocess.run(
            [sys.executable, '-m', 'trace', '--trace', '--no-report', file_path] + script_args,
            capture_output=True, timeout=30,
            cwd=os.path.dirname(file_path),
            encoding='utf-8', errors='replace',
            startupinfo=_get_startupinfo(),
        )

        output = result.stdout or ""
        if result.stderr:
            stderr_filtered = '\n'.join(
                l for l in result.stderr.split('\n')
                if 'Writing' not in l and 'modules' not in l.lower()
            ).strip()
            if stderr_filtered:
                output += "\n[stderr]\n" + stderr_filtered

        lines = output.split('\n')
        if len(lines) > max_lines:
            output = '\n'.join(lines[:max_lines]) + f"\n... (共 {len(lines)} 行，已截断)"

        return output[:8000] if output else "(无输出)"

    except subprocess.TimeoutExpired:
        return "错误: 执行超时 (30秒)"
