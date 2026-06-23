"""
记忆服务模块
管理每个会话的独立上下文记忆
支持 token 计数、上下文使用率监控和自动摘要压缩
"""

from langchain_core.messages import HumanMessage, AIMessage


# 默认上下文窗口大小（token）
DEFAULT_CONTEXT_LIMIT = 128000
# 触发自动摘要的阈值（百分比）
SUMMARIZE_THRESHOLD = 0.80


def estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数
    中文约 1 字 ≈ 2 token，英文约 4 字符 ≈ 1 token
    """
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - cn_chars
    return cn_chars * 2 + max(1, other_chars // 4)


class SessionMemory:
    """单个会话的记忆封装"""

    def __init__(self, session_id: str, k: int = 20, context_limit: int = DEFAULT_CONTEXT_LIMIT):
        self.session_id = session_id
        self.context_limit = context_limit
        self._k = k  # 保留最近 k 轮对话
        self._messages = []  # 消息列表

    def add_user_message(self, content: str):
        """添加用户消息"""
        self._messages.append(HumanMessage(content=content))
        self._trim_messages()

    def add_ai_message(self, content: str):
        """添加 AI 消息"""
        self._messages.append(AIMessage(content=content))
        self._trim_messages()

    def _trim_messages(self):
        """保留最近 k*2 条消息（k 轮对话）"""
        max_messages = self._k * 2
        if len(self._messages) > max_messages:
            self._messages = self._messages[-max_messages:]

    def get_messages(self) -> list:
        """获取所有消息"""
        return self._messages.copy()

    def get_api_messages(self) -> list:
        """获取 API 格式的消息列表"""
        messages = []
        for msg in self._messages:
            if isinstance(msg, HumanMessage):
                messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                messages.append({"role": "assistant", "content": msg.content})
        return messages

    def get_token_count(self) -> int:
        """计算当前记忆的总 token 数"""
        total = 0
        for msg in self._messages:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            total += estimate_tokens(content)
        return total

    def get_context_usage(self) -> float:
        """返回上下文使用率 (0.0 ~ 1.0)"""
        tokens = self.get_token_count()
        return min(1.0, tokens / self.context_limit) if self.context_limit > 0 else 0.0

    def needs_summarize(self) -> bool:
        """判断是否需要摘要压缩"""
        return self.get_context_usage() >= SUMMARIZE_THRESHOLD

    def get_summarize_messages(self) -> list:
        """获取需要被摘要的旧消息（保留最近 4 轮）"""
        msgs = self.get_api_messages()
        if len(msgs) <= 8:
            return []
        # 保留最后 8 条（4 轮），摘要前面的
        return msgs[:-8]

    def replace_with_summary(self, summary: str, keep_recent: list):
        """用摘要替换旧消息"""
        self._messages.clear()
        if summary:
            self._messages.append(HumanMessage(content="[以下是之前对话的摘要]"))
            self._messages.append(AIMessage(content=summary))
        # 恢复最近的消息
        for msg in keep_recent:
            if msg["role"] == "user":
                self._messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                self._messages.append(AIMessage(content=msg["content"]))

    def clear(self):
        """清除所有消息"""
        self._messages.clear()


class MemoryService:
    """记忆服务管理器"""

    def __init__(self, window_size: int = 20, context_limit: int = DEFAULT_CONTEXT_LIMIT):
        self._memories: dict[str, SessionMemory] = {}
        self._window_size = window_size
        self._context_limit = context_limit

    def get_or_create(self, session_id: str) -> SessionMemory:
        """获取或创建会话记忆"""
        if session_id not in self._memories:
            self._memories[session_id] = SessionMemory(
                session_id, k=self._window_size, context_limit=self._context_limit
            )
        return self._memories[session_id]

    def remove(self, session_id: str):
        """移除会话记忆"""
        self._memories.pop(session_id, None)

    def clear(self, session_id: str):
        """清除会话记忆"""
        if session_id in self._memories:
            self._memories[session_id].clear()

    def has_session(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return session_id in self._memories

    def list_sessions(self) -> list[str]:
        """列出所有会话 ID"""
        return list(self._memories.keys())
