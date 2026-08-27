"""
对话服务模块
整合 API 调用与 LangChain 记忆管理，提供统一的对话接口
支持自动上下文摘要压缩和多Agent协作
"""

import logging
from typing import List, Dict, Generator, Callable, Optional
import threading

from services.core.api_service import chat_completion_stream, find_model_by_display
from services.core.memory_service import MemoryService
from services.core.agent_service import AgentService

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "你是一个有用的AI助手，请用中文回答用户的问题。"


class ChatService:

    def __init__(self, window_size: int = 20, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
        self.memory_service = MemoryService(window_size=window_size)
        self.agent_service = AgentService()
        self.system_prompt = system_prompt
        self.agent_mode = True  # 默认启用 Agent 模式
        self.multi_agent_enabled = False  # 是否启用多Agent协作
        self._orchestrator = None  # 延迟初始化
        self._multi_agent_config = {}
        # 尝试加载多Agent配置
        self._init_multi_agent()

    def send_message_stream(
        self,
        session_id: str,
        user_message,
        model_display: str = "MiMo-V2.5-PRO",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Generator[str, None, None]:
        memory = self.memory_service.get_or_create(session_id)

        # 自动摘要压缩
        if memory.needs_summarize():
            self._auto_summarize(session_id, memory, model_display)

        api_messages = []
        if self.system_prompt:
            api_messages.append({"role": "system", "content": self.system_prompt})
        api_messages.extend(memory.get_api_messages())
        api_messages.append({"role": "user", "content": user_message})

        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else None

        # 获取自定义模型配置
        custom_base_url = None
        custom_api_key = None
        if model_info and model_info.get("is_custom"):
            custom_base_url = model_info.get("base_url")
            custom_api_key = model_info.get("api_key")

        full_response = ""
        for chunk in chat_completion_stream(
            messages=api_messages,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            custom_base_url=custom_base_url,
            custom_api_key=custom_api_key,
            app_session_id=session_id,
            status_callback=status_callback,
        ):
            full_response += chunk
            yield chunk

        memory_text = user_message
        if isinstance(user_message, list):
            text_parts = [p.get("text", "") for p in user_message if p.get("type") == "text"]
            memory_text = " ".join(text_parts) if text_parts else "[图片]"
        memory.add_user_message(memory_text)
        memory.add_ai_message(full_response)

    def _auto_summarize(self, session_id: str, memory, model_display: str):
        """调用 LLM 摘要压缩旧消息"""
        old_messages = memory.get_summarize_messages()
        if not old_messages:
            return
        summary_prompt = "请将以下对话内容压缩为简洁的摘要，保留关键信息：\n\n"
        for msg in old_messages:
            role = "用户" if msg["role"] == "user" else "AI"
            content = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            summary_prompt += f"{role}: {content[:200]}\n"

        model_info = find_model_by_display(model_display)
        model_name = model_info["model"] if model_info else None

        summary = ""
        for chunk in chat_completion_stream(
            messages=[{"role": "user", "content": summary_prompt}],
            model=model_name,
            temperature=0.3,
            max_tokens=500,
            app_session_id=session_id,
        ):
            summary += chunk

        if summary:
            all_msgs = memory.get_api_messages()
            keep_recent = all_msgs[-8:] if len(all_msgs) > 8 else all_msgs
            memory.replace_with_summary(summary, keep_recent)

    def get_context_info(self, session_id: str) -> dict:
        memory = self.memory_service.get_or_create(session_id)
        return {
            "token_count": memory.get_token_count(),
            "context_limit": memory.context_limit,
            "usage": memory.get_context_usage(),
            "needs_summarize": memory.needs_summarize(),
        }

    def send_agent_message(
        self,
        session_id: str,
        user_message: str,
        model_display: str = "MiMo-V2-Flash",
        max_steps: int = 10,
        workspace_path: str = None,
    ) -> dict:
        """
        通过 Agent（ReAct）处理消息，支持工具调用

        Returns:
            {"output": str, "steps": list, "thinking": str}
        """
        memory = self.memory_service.get_or_create(session_id)
        result = self.agent_service.run(user_message, model_display,
                                        history=memory.get_api_messages(),
                                        max_steps=max_steps,
                                        app_session_id=session_id,
                                        workspace_path=workspace_path)

        # 保存到记忆
        memory.add_user_message(user_message)
        memory.add_ai_message(result["output"])

        # 格式化工具调用步骤
        thinking = ""
        if result["steps"]:
            thinking_parts = []
            for i, step in enumerate(result["steps"], 1):
                thinking_parts.append(
                    f"**步骤 {i}**: 使用 `{step['tool']}`\n"
                    f"输入: `{step['input']}`\n"
                    f"结果: {step['output'][:200]}"
                )
            thinking = "\n\n".join(thinking_parts)

        return {
            "output": result["output"],
            "steps": result["steps"],
            "thinking": thinking,
        }

    # ── 多Agent协作 ─────────────────────────────────────

    def _init_multi_agent(self) -> None:
        """尝试初始化多Agent协作模式"""
        try:
            from services.multi_agent.config_loader import (
                load_multi_agent_config, is_multi_agent_enabled
            )
            if is_multi_agent_enabled():
                global_config, agent_defs = load_multi_agent_config()
                self.multi_agent_enabled = bool(global_config.get("enabled", False) and agent_defs)
                self._multi_agent_config = global_config
                if self.multi_agent_enabled:
                    logger.info(f"多Agent协作模式已启用: {len(agent_defs)} 个Agent")
                else:
                    logger.info("多Agent配置已加载但未启用")
            else:
                logger.info("多Agent协作模式未启用")
        except Exception as e:
            logger.warning(f"加载多Agent配置失败: {e}，使用单Agent模式")
            self.multi_agent_enabled = False

    def reload_multi_agent_config(self) -> None:
        """运行时重新加载多Agent配置"""
        try:
            from services.multi_agent.config_loader import load_multi_agent_config
            global_config, agent_defs = load_multi_agent_config()
            self.multi_agent_enabled = bool(global_config.get("enabled", False) and agent_defs)
            self._multi_agent_config = global_config
            self._orchestrator = None  # 重置编排器
            logger.info(f"多Agent配置重新加载: enabled={self.multi_agent_enabled}, "
                        f"agents={len(agent_defs)}")
        except Exception as e:
            logger.error(f"重新加载多Agent配置失败: {e}")

    def toggle_multi_agent(self, enabled: bool) -> None:
        """切换多Agent协作模式"""
        self.multi_agent_enabled = enabled and bool(self._multi_agent_config.get("enabled", False))
        if not self.multi_agent_enabled:
            self._orchestrator = None
        logger.info(f"多Agent协作模式: {'开启' if self.multi_agent_enabled else '关闭'}")

    def get_available_agents(self) -> list[dict]:
        """获取所有可用的子Agent信息"""
        try:
            from services.multi_agent.registry import get_registry
            registry = get_registry()
            return [
                {
                    "name": a.name,
                    "display": a.display_name,
                    "tool_count": len(a.tool_names) if a.tool_names else len(self.agent_service._tools),
                    "keywords": a.trigger_keywords[:5],
                }
                for a in registry.list_all()
            ]
        except Exception:
            return []

    def _get_orchestrator(self):
        """延迟初始化编排器"""
        if self._orchestrator is None:
            from services.multi_agent.orchestrator import OrchestratorAgent
            self._orchestrator = OrchestratorAgent(
                agent_service=self.agent_service,
                config=self._multi_agent_config,
            )
        return self._orchestrator

    def _normalize_multi_agent_event(self, event: dict) -> dict:
        """
        将多Agent事件标准化为UI能识别的格式。
        UI 当前识别: thinking, thought, step, result_chunk, result_clear, result, error, plan
        多Agent新增: orchestrator_*, sub_agent_*, synthesize_chunk, plan
        """
        etype = event.get("type", "")

        # RePlan 计划事件 → 透传 plan 类型
        if etype == "plan":
            return event  # 原样透传，UI 端解析 action 字段

        # 编排器事件 → thinking/thought 映射
        if etype == "orchestrator_analyzing":
            return {"type": "thinking", "output": event["output"]}
        elif etype == "orchestrator_routing":
            agents_str = ", ".join(event.get("agents", []))
            reason = event.get("reason", "")
            return {"type": "thought", "output": f"{event['output']} ({reason})"}
        elif etype == "orchestrator_synthesizing":
            # 在聊天区输出分隔符，区分进度更新和最终回答
            return {"type": "result_chunk", "output": "\n\n"}

        # 子Agent事件
        elif etype == "sub_agent_start":
            return {
                "type": "thought",
                "output": f"【{event.get('display', event.get('agent', ''))}】开始处理: {event.get('task', '')[:80]}"
            }
        elif etype == "sub_agent_step":
            return {
                "type": "step",
                "tool": event.get("tool", ""),
                "input": event.get("input", ""),
                "output": event.get("output", ""),
            }
        elif etype == "sub_agent_chunk":
            # 子Agent的流式文本输出到聊天区（让用户看到执行进度）
            return {"type": "result_chunk", "output": event.get("content", "")}
        elif etype == "sub_agent_done":
            # 在聊天区输出步骤完成标记，让用户看到进度
            display = event.get('display', '')
            return {
                "type": "result_chunk",
                "output": f"\n\n---\n"
            }
        elif etype == "sub_agent_error":
            return {
                "type": "result_chunk",
                "output": f"\n\n❌ **{event.get('display', '')}执行出错**: {event.get('error', '')[:200]}\n\n---\n"
            }

        # 合成流式输出 → result_chunk（实时 markdown 渲染）
        elif etype == "synthesize_chunk":
            return {"type": "result_chunk", "output": event.get("content", "")}

        # 工具开始执行（子Agent透传）→ 透传用于 UI 转圈指示
        elif etype == "tool_start":
            return event

        # 编排器完成 → 只发元信息，正文已通过 synthesize_chunk 或 result_chunk 流式输出
        elif etype == "orchestrator_done":
            result = event.get("result")
            if result is None:
                # 简单任务完成（_run_simple_chat 发送空 result）
                return {"type": "result", "output": ""}
            if hasattr(result, "final_answer"):
                return {"type": "result", "output": ""}
            return {"type": "result", "output": "任务完成"}

        return event  # 透传未知事件

    # ── Agent 消息流式接口 ──────────────────────────────

    def send_agent_message_stream(
        self,
        session_id: str,
        user_message: str,
        model_display: str = "MiMo-V2-Flash",
        max_steps: int = 10,
        status_callback: Optional[Callable[[str], None]] = None,
        workspace_path: str = None,
        stop_event: Optional[threading.Event] = None,
    ) -> Generator:
        """
        Agent 模式流式调用，带记忆。支持单Agent和多Agent协作模式。
        """
        memory = self.memory_service.get_or_create(session_id)
        history = memory.get_api_messages()
        full_response = ""

        # 多Agent模式路径
        if self.multi_agent_enabled and self._multi_agent_config.get("enabled"):
            orchestrator = self._get_orchestrator()
            for event in orchestrator.run(
                user_message=user_message,
                model_display=model_display,
                history=history,
                app_session_id=session_id,
                workspace_path=workspace_path,
                stop_event=stop_event,
                status_callback=status_callback,
                max_steps=max_steps,
            ):
                normalized = self._normalize_multi_agent_event(event)
                # 收集最终回复
                if normalized.get("type") == "result":
                    result_output = normalized.get("output", "")
                    if result_output:  # 非空才覆盖，防止空 result 事件清空已累积的 chunk
                        full_response = result_output
                elif normalized.get("type") == "result_chunk":
                    full_response += normalized.get("output", "")
                yield normalized

            # 保存到记忆（full_response 来自 result_chunk 累积或 result 事件）
            if full_response:
                memory.add_user_message(user_message)
                memory.add_ai_message(full_response)
            return

        # 单Agent模式路径（原有逻辑）
        for event in self.agent_service.run_stream(
            user_message, model_display, history=history, max_steps=max_steps,
            app_session_id=session_id,
            status_callback=status_callback,
            workspace_path=workspace_path,
            stop_event=stop_event,
        ):
            if event["type"] == "result":
                full_response = event.get("output", "")
            elif event["type"] == "result_chunk":
                full_response += event.get("output", "")
            elif event["type"] == "error" and not full_response:
                full_response = event.get("output", "")
            yield event

        # 保存到记忆
        if full_response:
            memory.add_user_message(user_message)
            memory.add_ai_message(full_response)

    def get_tools_info(self) -> list:
        return self.agent_service.get_tools_info()

    def get_session_history(self, session_id: str) -> List[Dict[str, str]]:
        if self.memory_service.has_session(session_id):
            return self.memory_service.get_or_create(session_id).get_api_messages()
        return []

    def clear_session(self, session_id: str):
        self.memory_service.clear(session_id)
        from services.providers.browser_context import reset_provider_tracking
        reset_provider_tracking(session_id)

    def remove_session(self, session_id: str):
        self.memory_service.remove(session_id)
        from services.providers.browser_context import reset_provider_tracking
        reset_provider_tracking(session_id)

    def switch_session(self, session_id: str):
        self.memory_service.get_or_create(session_id)
