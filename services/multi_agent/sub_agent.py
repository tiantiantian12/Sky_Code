"""
多Agent协作模块 - 子Agent执行器
将现有的 AgentService ReAct 循环包装为可独立运行的子Agent
"""
from __future__ import annotations
import time
import threading
import logging
from typing import Generator, Optional

import requests as _requests

from services.multi_agent.models import SubAgentDef, SubTaskResult

logger = logging.getLogger(__name__)


class SubAgentExecutor:
    """
    子Agent执行器：基于 AgentService 的 ReAct 循环，
    但使用限定的工具集和独立的系统提示词。
    """

    def __init__(self, agent_def: SubAgentDef, agent_service, tool_map: dict):
        """
        Args:
            agent_def: 子Agent定义
            agent_service: 共享的 AgentService 实例（用于调用 _run_api_tool_iter）
            tool_map: {工具名: 工具函数} 映射
        """
        self.def_ = agent_def
        self._agent_service = agent_service
        self._tool_map = tool_map

    def execute(
        self,
        task: str,
        task_id: str,
        model_display: str = "MiMo-V2-Flash",
        history: list = None,
        app_session_id: str = None,
        workspace_path: str = None,
        stop_event: Optional[threading.Event] = None,
        status_callback=None,
        max_steps_override: int = None,
    ) -> Generator:
        """
        执行子任务，yield 事件供 UI 展示。

        yield 的事件类型:
          - {"type": "sub_agent_start", "agent": str, "task": str}
          - {"type": "sub_agent_step", "agent": str, "tool": str, "input": str, "output": str}
          - {"type": "sub_agent_chunk", "agent": str, "content": str}  # 流式输出
          - {"type": "sub_agent_done", "agent": str, "output": str, "steps": list, "duration_ms": float}
          - {"type": "sub_agent_error", "agent": str, "error": str}
        """
        start_time = time.time()
        agent_name = self.def_.name
        agent_display = self.def_.display_name

        yield {"type": "sub_agent_start", "agent": agent_name,
               "display": agent_display, "task": task}

        try:
            # 构建限定的工具集
            limited_tools = self._build_limited_tools()

            # 构建子Agent专用的系统提示词
            system_prompt = self._build_role_prompt()

            # 使用 AgentService 的内部迭代器执行
            steps = []
            full_output = ""

            # 判断模型
            model_info = None
            try:
                from services.core.api_service import find_model_by_display, is_api_agent_model
                model_info = find_model_by_display(model_display)
            except Exception:
                pass

            model_name = model_info["model"] if model_info else "mimo-v2.5"
            actual_model = model_display
            if self.def_.llm_model != "inherit":
                actual_model = self.def_.llm_model

            # 获取自定义模型配置
            custom_base_url = None
            custom_api_key = None
            if model_info and model_info.get("is_custom"):
                custom_base_url = model_info.get("base_url")
                custom_api_key = model_info.get("api_key")

            # 构建消息列表
            messages = [{"role": "system", "content": system_prompt}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": task})

            # 子Agent的 ReAct 循环
            max_steps = max_steps_override if max_steps_override is not None else self.def_.max_steps
            from services.core.api_service import (
                chat_completion_with_tools_stream,
                build_openai_tools_schema,
                ToolCallingNotSupportedError,
            )
            from services.core.agent_service import (
                _build_system_prompt,
                _build_direct_tool_calls,
                _looks_like_run_request,
            )

            openai_tools = build_openai_tools_schema(limited_tools)
            tool_map_local = {t.name: t for t in limited_tools}
            use_tools_api = True
            # 追踪是否已执行过实质性操作（write_file/edit_file/execute_code 等）
            has_substantive_action = False
            has_called_any_tool = False  # 是否调用过任何工具（包括只读工具）
            tool_result_summary = []  # 收集工具结果摘要，用于 followup
            api_failed = False  # 追踪 API 调用是否最终失败

            for step_idx in range(max_steps):
                if stop_event is not None and stop_event.is_set():
                    yield {"type": "sub_agent_error", "agent": agent_name,
                           "error": "已停止"}
                    break

                if not use_tools_api:
                    break

                # ── 带重试的流式 API 调用 ──
                MAX_RETRIES = 3
                NETWORK_ERRORS = (
                    _requests.exceptions.ReadTimeout,
                    _requests.exceptions.ConnectTimeout,
                    _requests.exceptions.ConnectionError,
                    _requests.exceptions.Timeout,
                    _requests.exceptions.HTTPError,  # 429 限流、500 服务器错误等
                )
                api_call_success = False
                full_content = ""
                api_tool_calls = []

                for retry_idx in range(MAX_RETRIES):
                    try:
                        full_content = ""
                        api_tool_calls = []

                        for chunk in chat_completion_with_tools_stream(
                            messages=messages, tools=openai_tools, model=model_name,
                            temperature=self.def_.temperature,
                            max_tokens=4096,
                            custom_base_url=custom_base_url,
                            custom_api_key=custom_api_key,
                            stop_event=stop_event,
                        ):
                            if chunk.get("done"):
                                full_content = chunk.get("content", full_content)
                                api_tool_calls = chunk.get("tool_calls", [])
                            else:
                                text = chunk.get("content", "")
                                if text:
                                    full_content += text
                                    yield {"type": "sub_agent_chunk",
                                           "agent": agent_name, "content": text}
                        api_call_success = True
                        break  # 成功，跳出重试循环

                    except ToolCallingNotSupportedError:
                        use_tools_api = False
                        break
                    except NETWORK_ERRORS as e:
                        if retry_idx < MAX_RETRIES - 1:
                            delay = 2 ** retry_idx
                            logger.warning(
                                f"子Agent [{agent_name}] 网络异常，第{retry_idx+1}次重试（共{MAX_RETRIES}次）: {e}"
                            )
                            yield {
                                "type": "sub_agent_chunk",
                                "agent": agent_name,
                                "content": (
                                    f"\n[网络异常，{delay}秒后重试 "
                                    f"({retry_idx + 2}/{MAX_RETRIES})...]\n"
                                ),
                            }
                            time.sleep(delay)
                            continue
                        # 重试耗尽：不 raise，而是记录错误并跳出
                        logger.error(f"子Agent [{agent_name}] 网络重试耗尽: {e}")
                        full_output += f"\n[API 请求失败（重试耗尽）: {e}]"
                        api_tool_calls = []
                        api_failed = True
                        break
                    except Exception as e:
                        # 捕获所有其他异常（JSON 解析错误、认证错误等）
                        if retry_idx < MAX_RETRIES - 1:
                            delay = 2 ** retry_idx
                            logger.warning(
                                f"子Agent [{agent_name}] API 调用异常，第{retry_idx+1}次重试（共{MAX_RETRIES}次）: {e}"
                            )
                            yield {
                                "type": "sub_agent_chunk",
                                "agent": agent_name,
                                "content": (
                                    f"\n[API 异常，{delay}秒后重试 "
                                    f"({retry_idx + 2}/{MAX_RETRIES})...]\n"
                                ),
                            }
                            time.sleep(delay)
                            continue
                        # 重试耗尽：记录错误并跳出，不 raise
                        logger.error(f"子Agent [{agent_name}] API 重试耗尽: {e}")
                        full_output += f"\n[API 请求失败（重试耗尽）: {e}]"
                        api_tool_calls = []
                        api_failed = True
                        break

                if not use_tools_api:
                    break

                # 如果 API 调用未成功且没有内容，直接退出 ReAct 循环
                if not api_call_success and not full_content and not api_tool_calls:
                    logger.warning(f"子Agent [{agent_name}] API 调用失败，退出 ReAct 循环")
                    break

                # 累积模型输出的文本到 full_output（无论是否有工具调用）
                # 这样即使模型同时输出文本和工具调用，文本也不会丢失
                if full_content:
                    full_output += full_content

                # 如果没有工具调用，模型认为当前任务已完成（或无法继续）
                # 直接退出 ReAct 循环，让编排器决定下一步
                if not api_tool_calls:
                    break

                # 执行工具调用
                for tc in api_tool_calls:
                    tc_name = tc.get("function", {}).get("name", "")
                    tc_args_str = tc.get("function", {}).get("arguments", "{}")

                    if tc_name not in tool_map_local:
                        continue

                    import json as _json
                    try:
                        tc_args = _json.loads(tc_args_str) if isinstance(tc_args_str, str) else tc_args_str
                    except _json.JSONDecodeError:
                        tc_args = {"input": tc_args_str}

                    # ── 工具调用：发送到思考面板 ──
                    arg_preview = str(tc_args)[:80]
                    # 思考面板：完整信息
                    yield {"type": "sub_agent_step", "agent": agent_name,
                           "tool": tc_name, "input": str(tc_args)[:200]}

                    # 通知 UI 开始编辑文件
                    if tc_name in ("write_file", "edit_file"):
                        fp = tc_args.get("file_path", "") if isinstance(tc_args, dict) else str(tc_args)
                        if fp:
                            yield {"type": "tool_start", "tool": tc_name, "file_path": str(fp)}

                    try:
                        tool_fn = tool_map_local[tc_name]
                        result = tool_fn.invoke(tc_args) if hasattr(tool_fn, 'invoke') else tool_fn(**tc_args)
                        result_str = str(result)
                    except Exception as e:
                        result_str = f"工具执行错误: {e}"

                    # 思考面板：结果详情
                    yield {"type": "sub_agent_step", "agent": agent_name,
                           "tool": tc_name, "output": result_str[:500]}

                    steps.append({
                        "tool": tc_name,
                        "input": str(tc_args)[:200],
                        "output": result_str[:500],
                    })

                    # 将工具结果追加到消息中
                    messages.append({
                        "role": "assistant",
                        "content": full_content or None,
                        "tool_calls": [tc],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_str,
                    })

                    # 追踪实质性操作
                    if tc_name in ("write_file", "edit_file", "execute_code", "run_command", "delete_file"):
                        has_substantive_action = True
                    has_called_any_tool = True
                    tool_result_summary.append(f"{tc_name}: {result_str[:200]}")

                # 工具执行完毕后，添加 followup 消息推动模型继续
                # 仅在还有剩余步数时添加，且不强制要求模型继续调工具
                if tool_result_summary and step_idx < max_steps - 1:
                    body = "\n".join(tool_result_summary[-3:])  # 最近3个工具结果
                    # 检查是否有错误
                    has_error = any("错误" in s or "error" in s.lower() or "失败" in s
                                    for s in tool_result_summary[-3:])
                    if has_error:
                        followup = (
                            f"工具执行出错。结果：\n{body[:500]}\n\n"
                            f"请修正错误并重试。如果无法修复，简短说明原因。"
                        )
                    else:
                        followup = (
                            f"工具执行完成。请简述结果和下一步计划（1-2句话），如任务未完成请继续调工具。"
                        )
                    messages.append({"role": "user", "content": followup})
                    tool_result_summary = []  # 重置，避免重复追加

            duration_ms = (time.time() - start_time) * 1000
            if api_failed and not has_called_any_tool:
                # API 调用失败且未执行过任何工具，视为错误
                yield {
                    "type": "sub_agent_error",
                    "agent": agent_name,
                    "display": agent_display,
                    "error": full_output.strip() or "API 请求失败",
                    "duration_ms": duration_ms,
                }
            else:
                yield {
                    "type": "sub_agent_done",
                    "agent": agent_name,
                    "display": agent_display,
                    "output": full_output or "任务完成",
                    "steps": steps,
                    "duration_ms": duration_ms,
                }

        except Exception as e:
            logger.error(f"子Agent [{agent_name}] 执行异常: {e}", exc_info=True)
            duration_ms = (time.time() - start_time) * 1000
            yield {
                "type": "sub_agent_error",
                "agent": agent_name,
                "display": agent_display,
                "error": str(e),
                "duration_ms": duration_ms,
            }

    def _build_limited_tools(self) -> list:
        """构建该子Agent限定的工具集"""
        if not self.def_.tool_names:
            # 空列表 = 使用全部工具
            return self._agent_service._tools

        tools = []
        for name in self.def_.tool_names:
            if name in self._tool_map:
                tools.append(self._tool_map[name])
        return tools

    def _build_role_prompt(self) -> str:
        """构建子Agent的系统提示词"""
        prompt = self.def_.role_prompt
        if not prompt:
            # 使用默认系统提示词
            prompt = "你是一个有用的AI助手，请用中文回答用户的问题。"

        # 追加多Agent协作指令：简洁但有进度更新
        collaboration_rules = (
            "\n\n## 多Agent协作规则（必须遵守）\n"
            "- 你是团队中的一员，你的任务由编排器分配，请专注于完成你的部分\n"
            "- **禁止反问用户**：不要问\"需要我继续吗？\"\"要我实现X吗？\"等确认性提问\n"
            "- **必须执行具体操作**：使用 write_file/execute_code/run_command 等工具实际操作\n"
            "- 如果在现有代码基础上工作，请先 read_file 了解已创建的文件\n"
            "- 如果任务不明确，基于已有上下文做出最佳判断并执行，不要停下来提问\n"
            "\n## 输出规则（必须遵守）\n"
            "- **进度汇报**：在调用工具前，用一句话说明你要做什么（如\"好的，让我来读取具体代码。\"）\n"
            "- 工具执行后，用一句话简述结果和下一步计划（如\"代码已读完，现在让我分析实现逻辑。\"）\n"
            "- **禁止冗长**：不要在工具调用前后输出大段分析文字，进度说明控制在1-2句话\n"
            "- 不要重复工具返回的原始内容\n"
            "- 完成任务后输出一段简短的结果摘要（2-3句话），不要用编号列表\n"
            "- 最终的详细分析和综合回答由编排器统一生成，你不需要代劳\n"
            "- 禁止输出\"以上是XX的分析\"\"总结如下\"等冗余收尾语"
        )
        return prompt + collaboration_rules
