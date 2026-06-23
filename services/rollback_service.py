"""
回滚服务模块
记录每轮对话中 AI 对文件的修改，支持撤销
"""

import os
import time
from typing import Dict, List, Optional


class TurnSnapshot:
    """一轮对话的文件快照"""

    def __init__(self, turn_id: int):
        self.turn_id = turn_id
        # 文件原始内容 {绝对路径: 原始内容或 None(表示文件是新建的)}
        self.original_files: Dict[str, Optional[str]] = {}
        self.created_files: List[str] = []  # AI 新建的文件路径


class RollbackManager:
    """回滚管理器"""

    def __init__(self):
        self._turns: Dict[int, TurnSnapshot] = {}
        self._current_turn_id: int = 0

    def begin_turn(self) -> int:
        """开始新一轮对话，返回 turn_id"""
        self._current_turn_id += 1
        self._turns[self._current_turn_id] = TurnSnapshot(self._current_turn_id)
        return self._current_turn_id

    def get_current_turn_id(self) -> int:
        return self._current_turn_id

    def record_write(self, file_path: str, new_content: str):
        """记录文件写入操作（在实际写入前调用）"""
        turn = self._turns.get(self._current_turn_id)
        if not turn:
            return

        abs_path = os.path.abspath(file_path)

        # 如果这个文件在本轮已经被记录过，不再覆盖原始快照
        if abs_path in turn.original_files:
            return

        if os.path.exists(abs_path):
            # 文件已存在，保存原始内容
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    turn.original_files[abs_path] = f.read()
            except Exception:
                turn.original_files[abs_path] = None
        else:
            # 文件不存在，标记为新建
            turn.original_files[abs_path] = None
            turn.created_files.append(abs_path)

    def rollback(self, turn_id: int) -> dict:
        """
        回滚指定轮次的所有文件修改

        Returns:
            {"restored": [...], "deleted": [...], "errors": [...]}
        """
        turn = self._turns.get(turn_id)
        if not turn:
            return {"restored": [], "deleted": [], "errors": [f"未找到轮次 {turn_id}"]}

        restored = []
        deleted = []
        errors = []

        for file_path, original_content in turn.original_files.items():
            try:
                if original_content is None:
                    # 文件是这轮新建的，删除它
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        deleted.append(file_path)
                else:
                    # 文件之前就存在，恢复原始内容
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(original_content)
                    restored.append(file_path)
            except Exception as e:
                errors.append(f"{file_path}: {e}")

        # 清除该轮的记录
        del self._turns[turn_id]

        return {"restored": restored, "deleted": deleted, "errors": errors}

    def has_changes(self, turn_id: int) -> bool:
        """检查指定轮次是否有文件修改"""
        turn = self._turns.get(turn_id)
        return bool(turn and turn.original_files)

    def remove_turn(self, turn_id: int):
        """移除轮次记录（不回滚）"""
        self._turns.pop(turn_id, None)
