"""
工具链编排工具集
支持多工具协同的复杂工作流
"""

import json
import os
import re
import tempfile
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool


class WorkflowContext:
    """工作流上下文，存储中间结果"""

    def __init__(self):
        self.variables: Dict[str, Any] = {}
        self.results: List[Dict[str, Any]] = []
        self.current_step = 0

    def set_variable(self, name: str, value: Any):
        self.variables[name] = value

    def get_variable(self, name: str) -> Any:
        return self.variables.get(name)

    def add_result(self, step: int, tool: str, input_data: Any, output: Any):
        self.results.append({
            "step": step,
            "tool": tool,
            "input": str(input_data)[:500],
            "output": str(output)[:500]
        })

    def to_dict(self) -> Dict:
        return {
            "variables": {k: str(v)[:200] for k, v in self.variables.items()},
            "results": self.results,
            "current_step": self.current_step
        }


# 全局工作流上下文
_workflow_context = WorkflowContext()


@tool
def workflow_start(workflow_name: str = "default") -> str:
    """开始一个新的工作流。重置上下文和步骤计数。

    Args:
        workflow_name: 工作流名称（可选）
    """
    global _workflow_context
    _workflow_context = WorkflowContext()
    return f"工作流 '{workflow_name}' 已开始"


@tool
def workflow_set_variable(name: str, value: str) -> str:
    """在工作流上下文中设置变量。用于在步骤之间传递数据。

    Args:
        name: 变量名
        value: 变量值（字符串）
    """
    _workflow_context.set_variable(name, value)
    return f"变量 '{name}' 已设置"


@tool
def workflow_get_variable(name: str) -> str:
    """从工作流上下文中获取变量值。

    Args:
        name: 变量名
    """
    value = _workflow_context.get_variable(name)
    if value is None:
        return f"错误: 变量 '{name}' 不存在"
    return str(value)


@tool
def workflow_get_status() -> str:
    """获取当前工作流状态，包括所有变量和执行历史。"""
    return json.dumps(_workflow_context.to_dict(), ensure_ascii=False, indent=2)


@tool
def execute_sequence(steps_json: str) -> str:
    """按顺序执行多个工具调用。每个步骤可以使用前一步骤的结果。

    Args:
        steps_json: 步骤定义的 JSON 字符串，格式如下:
        [
            {"tool": "工具名", "input": {...}, "output_var": "结果变量名"},
            {"tool": "工具名", "input": {...}, "output_var": "结果变量名"}
        ]
        
        input 中可以使用 ${var_name} 引用之前步骤的结果变量。
    """
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return f"错误: JSON 解析失败 - {e}"

    from services.tools import get_all_tools
    tools_map = {t.name: t for t in get_all_tools()}

    results = []
    for i, step in enumerate(steps):
        tool_name = step.get("tool")
        tool_input = step.get("input", {})
        output_var = step.get("output_var")

        if tool_name not in tools_map:
            results.append(f"步骤 {i+1}: 错误 - 工具 '{tool_name}' 不存在")
            continue

        # 替换变量引用（递归解析 dict/list/str 中的 ${var}）
        tool_input = _resolve_var(tool_input)
        unresolved = _check_unresolved(tool_input)
        if unresolved:
            results.append(
                f"步骤 {i+1}: 错误 - 以下变量未定义: {', '.join(set(unresolved))}"
            )
            continue

        # 执行工具
        try:
            tool = tools_map[tool_name]
            if isinstance(tool_input, dict):
                result = tool.invoke(tool_input)
            else:
                result = tool.invoke(tool_input)

            # 存储结果
            if output_var:
                _workflow_context.set_variable(output_var, result)

            _workflow_context.add_result(i + 1, tool_name, tool_input, result)
            results.append(f"步骤 {i+1} ({tool_name}): 成功")

        except Exception as e:
            results.append(f"步骤 {i+1} ({tool_name}): 错误 - {e}")
            break

    return "\n".join(results)


_VAR_PATTERN = re.compile(r'\$\{(\w+)\}')


def _resolve_var(value: Any) -> Any:
    """递归解析字符串/字典/列表中的 ${var_name} 变量引用，支持嵌入替换。"""
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            resolved = _workflow_context.get_variable(var_name)
            if resolved is not None:
                return str(resolved)
            return match.group(0)  # 变量不存在则保持原样
        return _VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_var(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_var(v) for v in value]
    return value


def _check_unresolved(value: Any) -> List[str]:
    """检查解析后的值中是否存在未解析的 ${var}，返回未解析变量名列表。"""
    unresolved: List[str] = []
    if isinstance(value, str):
        unresolved.extend(_VAR_PATTERN.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            unresolved.extend(_check_unresolved(v))
    elif isinstance(value, list):
        for v in value:
            unresolved.extend(_check_unresolved(v))
    return unresolved


@tool
def execute_parallel(tasks_json: str) -> str:
    """并行执行多个独立的工具调用。

    Args:
        tasks_json: 任务定义的 JSON 字符串，格式如下:
        [
            {"tool": "工具名", "input": {...}, "output_var": "结果变量名"},
            {"tool": "工具名", "input": {...}, "output_var": "结果变量名"}
        ]
    """
    import concurrent.futures

    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"错误: JSON 解析失败 - {e}"

    from services.tools import get_all_tools
    tools_map = {t.name: t for t in get_all_tools()}

    def execute_task(task):
        tool_name = task.get("tool")
        tool_input = task.get("input", {})
        output_var = task.get("output_var")

        if tool_name not in tools_map:
            return {"task": task, "error": f"工具 '{tool_name}' 不存在"}

        try:
            tool = tools_map[tool_name]
            resolved_input = _resolve_var(tool_input)
            unresolved = _check_unresolved(resolved_input)
            if unresolved:
                return {"task": task, "error": f"变量未定义: {', '.join(set(unresolved))}"}
            if isinstance(resolved_input, dict):
                result = tool.invoke(resolved_input)
            else:
                result = tool.invoke(resolved_input)

            return {"task": task, "result": result, "output_var": output_var}
        except Exception as e:
            return {"task": task, "error": str(e)}

    # 并行执行
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(execute_task, task): i for i, task in enumerate(tasks)}
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                if "error" in result:
                    results.append(f"任务 {idx+1} ({result['task'].get('tool')}): 错误 - {result['error']}")
                else:
                    if result.get("output_var"):
                        _workflow_context.set_variable(result["output_var"], result["result"])
                    results.append(f"任务 {idx+1} ({result['task'].get('tool')}): 成功")
            except Exception as e:
                results.append(f"任务 {idx+1}: 执行异常 - {e}")

    return "\n".join(sorted(results))


@tool
def execute_conditional(condition_var: str, condition: str, condition_value: str,
                        true_tool: str, true_input: str,
                        false_tool: Optional[str] = None,
                        false_input: Optional[str] = None) -> str:
    """条件执行工具。根据条件选择执行不同的工具。

    Args:
        condition_var: 条件变量名
        condition: 比较操作符，支持 ==、!=、>、<、>=、<=、contains、not_contains
        condition_value: 与变量比较的值
        true_tool: 条件为真时执行的工具名
        true_input: 条件为真时的工具输入 JSON 字符串
        false_tool: 条件为假时执行的工具名（可选）
        false_input: 条件为假时的工具输入 JSON 字符串（可选）
    """
    from services.tools import get_all_tools
    tools_map = {t.name: t for t in get_all_tools()}

    # 获取变量值
    var_value = _workflow_context.get_variable(condition_var)
    if var_value is None:
        return f"错误: 变量 '{condition_var}' 不存在"

    # 评估条件
    condition_met = False
    try:
        if condition == "==":
            condition_met = str(var_value) == str(condition_value)
        elif condition == "!=":
            condition_met = str(var_value) != str(condition_value)
        elif condition == ">":
            condition_met = float(str(var_value)) > float(condition_value)
        elif condition == "<":
            condition_met = float(str(var_value)) < float(condition_value)
        elif condition == ">=":
            condition_met = float(str(var_value)) >= float(condition_value)
        elif condition == "<=":
            condition_met = float(str(var_value)) <= float(condition_value)
        elif condition == "contains":
            condition_met = str(condition_value) in str(var_value)
        elif condition == "not_contains":
            condition_met = str(condition_value) not in str(var_value)
        else:
            return f"错误: 不支持的条件 '{condition}'"
    except Exception as e:
        return f"错误: 条件评估失败 - {e}"

    # 执行对应的工具
    if condition_met:
        tool_name = true_tool
        tool_input = true_input
    else:
        tool_name = false_tool
        tool_input = false_input

    if not tool_name:
        return f"条件为{'真' if condition_met else '假'}，无对应工具执行"

    if tool_name not in tools_map:
        return f"错误: 工具 '{tool_name}' 不存在"

    try:
        tool = tools_map[tool_name]
        parsed_input = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
        # 替换 input 中的变量引用
        resolved_input = _resolve_var(parsed_input)
        unresolved = _check_unresolved(resolved_input)
        if unresolved:
            return f"错误: 变量未定义: {', '.join(set(unresolved))}"
        result = tool.invoke(resolved_input)
        return f"条件为{'真' if condition_met else '假'}，执行 {tool_name}:\n{result}"
    except Exception as e:
        return f"错误: 工具执行失败 - {e}"


@tool
def execute_loop(items_var: str, tool_name: str, tool_input_template: str,
                 output_var: Optional[str] = None, max_iterations: int = 10) -> str:
    """循环执行工具。对列表中的每个元素执行相同的工具。

    Args:
        items_var: 包含列表的变量名
        tool_name: 要执行的工具名
        tool_input_template: 工具输入模板，使用 ${item} 引用当前元素
        output_var: 存储所有结果的变量名（可选）
        max_iterations: 最大迭代次数（默认 10）
    """
    from services.tools import get_all_tools
    tools_map = {t.name: t for t in get_all_tools()}

    # 获取列表
    items = _workflow_context.get_variable(items_var)
    if items is None:
        return f"错误: 变量 '{items_var}' 不存在"

    # 尝试解析为列表
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = [items]

    if not isinstance(items, (list, tuple)):
        return f"错误: 变量 '{items_var}' 不是列表"

    if tool_name not in tools_map:
        return f"错误: 工具 '{tool_name}' 不存在"

    tool = tools_map[tool_name]
    results = []

    for i, item in enumerate(items[:max_iterations]):
        # 替换模板中的变量
        input_str = tool_input_template.replace("${item}", str(item))
        try:
            parsed_input = json.loads(input_str)
            # 替换模板后再解析变量引用
            resolved_input = _resolve_var(parsed_input)
            unresolved = _check_unresolved(resolved_input)
            if unresolved:
                results.append(f"循环第{i+1}次: 错误 - 变量未定义: {', '.join(set(unresolved))}")
                continue
            result = tool.invoke(resolved_input)
            results.append(result)
        except Exception as e:
            results.append(f"错误: {e}")

    if output_var:
        _workflow_context.set_variable(output_var, results)

    return f"循环完成，执行了 {len(results)} 次:\n" + "\n---\n".join(str(r)[:200] for r in results)
