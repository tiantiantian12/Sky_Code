"""
回滚服务模块
记录每轮对话中 AI 对文件的修改，支持撤销
"""

import os
import time
from typing import Dict, List, Optional, Callable


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
        # 文件操作回调函数
        self._file_operation_callback: Optional[Callable] = None

    def set_file_operation_callback(self, callback: Callable):
        """设置文件操作回调，用于在UI上显示文件操作信息"""
        self._file_operation_callback = callback

    def begin_turn(self) -> int:
        """开始新一轮对话，返回 turn_id"""
        self._current_turn_id += 1
        self._turns[self._current_turn_id] = TurnSnapshot(self._current_turn_id)
        return self._current_turn_id

    def get_current_turn_id(self) -> int:
        return self._current_turn_id

    def record_write(self, file_path: str, new_content: str):
        """记录文件写入操作（在实际写入前调用）"""
        import difflib
        turn = self._turns.get(self._current_turn_id)
        if not turn:
            return

        abs_path = os.path.abspath(file_path)
        operation_type = None
        added_lines = 0
        removed_lines = 0

        # 如果这个文件在本轮已经被记录过，不再覆盖原始快照
        if abs_path in turn.original_files:
            # 计算行数变化
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    old_content = f.read()
                old_lines = old_content.splitlines(keepends=True)
                new_lines = new_content.splitlines(keepends=True)
                diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                for line in diff:
                    if line.startswith('+') and not line.startswith('+++'):
                        added_lines += 1
                    elif line.startswith('-') and not line.startswith('---'):
                        removed_lines += 1
                operation_type = 'modify'
            except Exception:
                operation_type = 'create'
                added_lines = new_content.count('\n') + 1 if new_content else 0
        else:
            if os.path.exists(abs_path):
                # 文件已存在，保存原始内容
                try:
                    with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                        old_content = f.read()
                    turn.original_files[abs_path] = old_content
                    old_lines = old_content.splitlines(keepends=True)
                    new_lines = new_content.splitlines(keepends=True)
                    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
                    for line in diff:
                        if line.startswith('+') and not line.startswith('+++'):
                            added_lines += 1
                        elif line.startswith('-') and not line.startswith('---'):
                            removed_lines += 1
                    operation_type = 'modify'
                except Exception:
                    turn.original_files[abs_path] = None
                    operation_type = 'create'
                    added_lines = new_content.count('\n') + 1 if new_content else 0
            else:
                # 文件不存在，标记为新建
                turn.original_files[abs_path] = None
                turn.created_files.append(abs_path)
                operation_type = 'create'
                added_lines = new_content.count('\n') + 1 if new_content else 0

        # 触发回调显示文件操作信息
        if self._file_operation_callback and operation_type:
            self._file_operation_callback(operation_type, abs_path, added_lines, removed_lines)

    def record_delete(self, file_path: str):
        """记录文件删除操作"""
        turn = self._turns.get(self._current_turn_id)
        if not turn:
            return

        abs_path = os.path.abspath(file_path)
        
        if os.path.exists(abs_path):
            # 读取原文件内容用于回滚
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    original_content = f.read()
                turn.original_files[abs_path] = original_content
                removed_lines = original_content.count('\n') + 1 if original_content else 0
            except Exception:
                turn.original_files[abs_path] = ""
                removed_lines = 0
            
            # 触发回调显示文件删除信息
            if self._file_operation_callback:
                self._file_operation_callback('delete', abs_path, 0, removed_lines)

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
                        # 从向量数据库移除索引
                        self._remove_file_from_rag(file_path)
                else:
                    # 文件之前就存在，恢复原始内容
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(original_content)
                    restored.append(file_path)
                    # 重新索引恢复后的文件
                    self._index_file_in_rag(file_path)
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

    def _index_file_in_rag(self, file_path: str):
        """后台非阻塞：重新索引恢复后的文件到向量数据库"""
        def _do():
            try:
                from services.tools.rag_tools import index_file
                index_file(file_path)
            except Exception:
                pass
        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _remove_file_from_rag(self, file_path: str):
        """后台非阻塞：从向量数据库移除被删除文件的索引"""
        def _do():
            try:
                from services.tools.rag_tools import remove_file_from_index
                remove_file_from_index(file_path)
            except Exception:
                pass
        import threading
        threading.Thread(target=_do, daemon=True).start()
