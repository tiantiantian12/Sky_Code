"""
Agent 服务模块
自定义 ReAct 循环，直接调用 LLM API + 工具执行
不依赖 LangChain 的文本格式解析，更健壮
"""

import json
import re
import queue
import threading
from typing import Generator

from services.api_service import chat_completion, find_model_by_display
from services.tools import get_all_tools
from services.config import get_agent_config


def _build_system_prompt(tools) -> str:
    """构建系统提示词"""
    lines = []
    for t in tools:
        # 提取参数信息
        schema = t.args_schema.model_json_schema() if hasattr(t, 'args_schema') and t.args_schema else {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        param_parts = []
        for pname, pinfo in props.items():
            mark = "（必填）" if pname in required else "（可选）"
            param_parts.append(f"{pname}: {pinfo.get('description', '')}{mark}")
        param_desc = "; ".join(param_parts) if param_parts else "无参数"
        desc_first = t.description.strip().split(chr(10))[0]
        lines.append(f"- {t.name}: {desc_first}\n  参数: {param_desc}")

    tool_desc = "\n".join(lines)
    tool_names = ", ".join(t.name for t in tools)
    return f"""你是一个多功能智能助手，可以使用以下工具：

{tool_desc}

当需要使用工具时，输出以下 JSON 格式：
{{"tool": "工具名称", "input": {{"参数名1": "值1", "参数名2": "值2"}}}}

对于只有一个参数的工具，input 可以直接用字符串：
{{"tool": "read_file", "input": "D:/path/file.txt"}}

对于多个参数的工具，input 必须用对象：
{{"tool": "write_file", "input": {{"file_path": "D:/path/file.py", "content": "print('hello')"}}}}
{{"tool": "run_command", "input": {{"command": "dir D:\\\\project"}}}}
{{"tool": "search_files", "input": {{"dir_path": "D:\\\\project", "keyword": "test"}}}}

重要规则：
1. 每次只调用一个工具，等收到结果后再决定下一步
2. 工具名称必须是 [{tool_names}] 之一
3. 绝对不要猜测或编造文件内容、目录结构
4. 要查看文件或目录，必须先用工具读取
5. 写文件时必须把完整的文件内容放在 content 参数中
6. 可以使用 run_command 执行 Windows 命令如 dir、tree、type、findstr
7. 你必须持续使用工具直到任务完全完成，不要中途停下来解释
8. 只有当所有文件都已创建/修改完毕后，才可以输出最终的中文总结
9. 不要输出"接下来我会..."这样的话，直接调用工具执行
10. 如果用户要求创建文件，你必须调用 write_file 工具实际创建它，不要只是描述
11. 调用工具时只输出纯 JSON，不要用 ```json``` 代码块包裹，不要在 JSON 前后加任何文字
12. 文件路径中使用正斜杠 / 或双反斜杠 \\\\，不要用单个反斜杠 \\
13. content 中的字符串必须正确转义：换行用 \\\\n，引号用 \\\\\"，反斜杠用 \\\\\\\\

工具分类说明：
- 文件操作：read_file, write_file, list_directory, search_files, run_command
- 网络访问：http_request, fetch_webpage, api_call, download_file
- 数据分析：read_csv, read_excel, analyze_data, create_chart, transform_data
- 文档解析：parse_pdf, parse_word, parse_ppt, parse_document, extract_document_metadata
- 工作流编排：workflow_start, workflow_set_variable, workflow_get_variable, workflow_get_status,
  execute_sequence, execute_parallel, execute_conditional, execute_loop"""


def _parse_tool_call(text: str) -> dict | None:
    """从 LLM 输出中解析工具调用（支持嵌套 JSON）"""
    text = str(text) if not isinstance(text, str) else text
    # 直接尝试解析整个文本
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool" in data and "input" in data:
            return data
    except json.JSONDecodeError:
        pass

    # 尝试从代码块中提取
    code_block_re = re.compile(r'```(?:json|python|javascript)?\s*\n?(.*?)\n?```', re.DOTALL)
    for m in code_block_re.finditer(text):
        candidate = m.group(1).strip()
        result = _try_parse_json_object(candidate)
        if result:
            return result

    # 从文本中逐字符扫描，找到所有匹配的 {} 块
    result = _try_parse_json_object(text)
    if result:
        return result

    return None


def _try_parse_json_object(text: str) -> dict | None:
    """从文本中找到第一个合法的 {"tool": ..., "input": ...} JSON 对象"""
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            in_string = False
            escape_next = False
            for j in range(i, len(text)):
                ch = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j + 1]
                        # 先尝试直接解析
                        result = _safe_json_loads(candidate)
                        if result:
                            return result
                        break
        i += 1
    return None


def _safe_json_loads(text: str) -> dict | None:
    """尝试解析 JSON，失败时修复常见问题（如未转义的反斜杠）"""
    # 1. 直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool" in data and "input" in data:
            return data
    except Exception:
        pass

    # 2. 修复未转义的反斜杠后解析
    try:
        fixed = _fix_backslashes(text)
        data = json.loads(fixed)
        if isinstance(data, dict) and "tool" in data and "input" in data:
            return data
    except Exception:
        pass

    # 3. 正则提取 tool 名 + 手动提取 input 值
    try:
        tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', text)
        if not tool_match:
            return None
        tool_name = tool_match.group(1)

        # 找到 "input" 的位置
        input_match = re.search(r'"input"\s*:\s*', text)
        if not input_match:
            return None
        input_start = input_match.end()

        # input 值可能是对象 {...} 或字符串 "..."
        input_val = _extract_json_value(text, input_start)
        if input_val is not None:
            return {"tool": tool_name, "input": input_val}
    except Exception:
        pass

    return None


def _extract_json_value(text: str, start: int):
    """从指定位置提取 JSON 值（对象、字符串、数字等）"""
    if start >= len(text):
        return None
    ch = text[start]

    # 对象 {...}
    if ch == '{':
        return _extract_braced_object(text, start)

    # 字符串 "..."
    if ch == '"':
        end = start + 1
        while end < len(text):
            if text[end] == '\\':
                end += 2
                continue
            if text[end] == '"':
                raw = text[start + 1:end]
                # 尝试修复反斜杠后作为 JSON 字符串解析
                try:
                    return json.loads('"' + _fix_backslashes(raw) + '"')
                except Exception:
                    return raw
            end += 1
        return text[start + 1:]

    # 其他（数字、bool、null）
    end = start
    while end < len(text) and text[end] not in (',', '}', ']', '\n'):
        end += 1
    raw = text[start:end].strip()
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _extract_braced_object(text: str, start: int) -> dict | None:
    """用括号匹配提取完整的 {} 对象，然后尝试解析"""
    depth = 0
    in_string = False
    escape_next = False
    for j in range(start, len(text)):
        ch = text[j]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                candidate = text[start:j + 1]
                # 尝试直接解析
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
                # 修复反斜杠后解析
                try:
                    fixed = _fix_backslashes(candidate)
                    data = json.loads(fixed)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
                return None
    return None


def _fix_backslashes(text: str) -> str:
    """修复 JSON 字符串中未转义的反斜杠"""
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string:
            if i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch in ('"', '\\', '/', 'b', 'f', 'n', 'r', 't'):
                    result.append(ch)
                    result.append(next_ch)
                    i += 2
                    continue
                elif next_ch == 'u':
                    # \u 后必须跟 4 位十六进制才是合法 JSON 转义
                    hex_part = text[i+2:i+6] if i+6 <= len(text) else ""
                    if len(hex_part) == 4 and all(c in '0123456789abcdefABCDEF' for c in hex_part):
                        result.append(ch)
                        result.append(next_ch)
                        i += 2
                        continue
                    else:
                        result.append('\\\\')
                        i += 1
                        continue
                else:
                    result.append('\\\\')
                    i += 1
                    continue
            else:
                result.append('\\\\')
                i += 1
                continue
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


def _looks_like_tool_call(text: str) -> bool:
    """检测响应是否看起来像是一个工具调用（但解析失败了）"""
    if not text:
        return False
    text = text.strip()
    # 包含 "tool" 和 "input" 关键字
    has_tool = '"tool"' in text
    has_input = '"input"' in text
    # 或者被 markdown 代码块包裹
    in_code_block = '```' in text and ('"tool"' in text or '"input"' in text)
    return (has_tool and has_input) or in_code_block


class AgentService:
    """Agent 服务 — 自定义 ReAct 循环"""

    def __init__(self):
        self._tools = get_all_tools()
        self._tool_map = {t.name: t for t in self._tools}
        self._config = get_agent_config()

    def clear_cache(self):
        pass

    def run(self, user_message: str, model_display: str = "MiMo-V2-Flash",
            history: list = None, max_steps: int = None) -> dict:
        results = list(self._run_iter(user_message, model_display, history, max_steps))
        output, steps = "", []
        for event in results:
            if event["type"] == "step":
                steps.append({"tool": event["tool"], "input": event["input"], "output": event["output"]})
            elif event["type"] in ("result", "error"):
                output = event["output"]
        return {"output": output, "steps": steps}

    def run_stream(self, user_message: str, model_display: str = "MiMo-V2-Flash",
                   on_step=None, history: list = None, max_steps: int = None) -> Generator:
        event_queue = queue.Queue()

        def run_in_thread():
            try:
                for event in self._run_iter(user_message, model_display, history, max_steps):
                    event_queue.put(event)
            except Exception as e:
                event_queue.put({"type": "error", "output": f"Agent 错误: {e}"})
            finally:
                event_queue.put(None)

        threading.Thread(target=run_in_thread, daemon=True).start()

        timeout = self._config.get("timeout", 240)
        yield {"type": "thinking", "output": "正在分析问题..."}
        while True:
            try:
                event = event_queue.get(timeout=timeout)
            except queue.Empty:
                yield {"type": "error", "output": "Agent 执行超时"}
                break
            if event is None:
                break
            yield event

    def _run_iter(self, user_message: str, model_display: str,
                  history: list = None, max_steps: int = None) -> Generator:
        """核心 ReAct 循环"""
        if not isinstance(user_message, str):
            user_message = str(user_message)
        if max_steps is None:
            max_steps = self._config.get("max_steps", 10)

        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else "mimo-v2.5"
        system_prompt = _build_system_prompt(self._tools)

        # 获取自定义模型配置
        custom_base_url = None
        custom_api_key = None
        if model_info and model_info.get("is_custom"):
            custom_base_url = model_info.get("base_url")
            custom_api_key = model_info.get("api_key")

        messages = [{"role": "system", "content": system_prompt}]
        # 注入历史对话
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        llm_params = self._config.get("llm_params", {})
        temperature = llm_params.get("temperature", 0.3)
        max_tokens = llm_params.get("max_tokens", 4096)

        for _ in range(max_steps):
            try:
                response = chat_completion(messages=messages, model=model_name,
                                           temperature=temperature, max_tokens=max_tokens,
                                           custom_base_url=custom_base_url,
                                           custom_api_key=custom_api_key)
            except Exception as e:
                yield {"type": "error", "output": f"LLM 调用失败: {e}"}
                return

            if not response:
                # 再试一次，可能模型暂时无响应
                try:
                    response = chat_completion(messages=messages, model=model_name,
                                               temperature=temperature, max_tokens=max_tokens,
                                               custom_base_url=custom_base_url,
                                               custom_api_key=custom_api_key)
                except Exception:
                    pass
                if not response:
                    yield {"type": "error", "output": f"LLM 返回空响应。模型: {model_name}，请检查模型名称是否正确。"}
                    return

            tool_call = _parse_tool_call(response)

            # 如果解析失败但响应看起来包含工具调用，重试一次
            if not tool_call and _looks_like_tool_call(response):
                yield {"type": "thought", "output": "工具调用解析失败，正在重试..."}
                # 提示模型只输出纯 JSON，用更低温度
                retry_messages = messages + [
                    {"role": "assistant", "content": response},
                    {"role": "user", "content": "你的输出格式有误，请只输出一个纯 JSON 工具调用，不要包含任何其他文字或 markdown 标记。"}
                ]
                try:
                    retry_response = chat_completion(messages=retry_messages, model=model_name,
                                                     temperature=max(temperature - 0.2, 0.0),
                                                     max_tokens=max_tokens,
                                                     custom_base_url=custom_base_url,
                                                     custom_api_key=custom_api_key)
                    retry_call = _parse_tool_call(retry_response) if retry_response else None
                    if retry_call:
                        response = retry_response
                        tool_call = retry_call
                except Exception:
                    pass

            if tool_call:
                tool_name = tool_call.get("tool", "")
                tool_input = tool_call.get("input", "")

                if tool_name in self._tool_map:
                    try:
                        tool = self._tool_map[tool_name]
                        # 支持 dict 和 str 两种 input 格式
                        if isinstance(tool_input, dict):
                            tool_result = tool.invoke(tool_input)
                        else:
                            tool_result = tool.invoke(tool_input)
                    except Exception as e:
                        tool_result = f"工具执行错误: {e}"

                    thought = response.split("{")[0].strip() if "{" in response else ""
                    if thought:
                        yield {"type": "thought", "output": thought}
                    yield {"type": "step", "tool": tool_name,
                           "input": str(tool_input)[:200], "output": str(tool_result)[:500]}

                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (f"工具 {tool_name} 的返回结果:\n{tool_result}\n\n"
                                    "请根据以上工具返回的结果回答用户的问题。如果还需要更多信息，可以继续调用工具。")})
                else:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user",
                                     "content": f"错误: 工具 '{tool_name}' 不存在。可用: {', '.join(self._tool_map.keys())}"})
            else:
                yield {"type": "result", "output": response}
                return

        yield {"type": "result", "output": response if response else "达到最大推理轮次"}

    def get_tools_info(self) -> list:
        return [{"name": t.name, "description": t.description} for t in self._tools]
