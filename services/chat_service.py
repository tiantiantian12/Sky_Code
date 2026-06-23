"""
对话服务模块
整合 API 调用与 LangChain 记忆管理，提供统一的对话接口
支持自动上下文摘要压缩
"""

from typing import List, Dict, Generator

from services.api_service import chat_completion_stream, find_model_by_display
from services.memory_service import MemoryService
from services.agent_service import AgentService


DEFAULT_SYSTEM_PROMPT = "你是一个有用的AI助手，请用中文回答用户的问题。"


class ChatService:

    def __init__(self, window_size: int = 20, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
        self.memory_service = MemoryService(window_size=window_size)
        self.agent_service = AgentService()
        self.system_prompt = system_prompt
        self.agent_mode = False  # 是否启用 Agent 模式

    def send_message_stream(
        self,
        session_id: str,
        user_message,
        model_display: str = "MiMo-V2.5-PRO",
        temperature: float = 0.7,
        max_tokens: int = 2048,
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
    ) -> dict:
        """
        通过 Agent（ReAct）处理消息，支持工具调用

        Returns:
            {"output": str, "steps": list, "thinking": str}
        """
        memory = self.memory_service.get_or_create(session_id)
        result = self.agent_service.run(user_message, model_display,
                                        history=memory.get_api_messages(),
                                        max_steps=max_steps)

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

    def send_agent_message_stream(
        self,
        session_id: str,
        user_message: str,
        model_display: str = "MiMo-V2-Flash",
        max_steps: int = 10,
    ) -> Generator:
        """
        Agent 模式流式调用，带记忆
        """
        memory = self.memory_service.get_or_create(session_id)
        history = memory.get_api_messages()
        full_response = ""

        for event in self.agent_service.run_stream(
            user_message, model_display, history=history, max_steps=max_steps
        ):
            # 收集最终回复用于保存记忆
            if event["type"] == "result":
                full_response = event.get("output", "")
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

    def remove_session(self, session_id: str):
        self.memory_service.remove(session_id)

    def switch_session(self, session_id: str):
        self.memory_service.get_or_create(session_id)
