"""
后台 Agent 服务
允许 Agent 在后台执行长时间运行的任务，不阻塞主对话流。

核心功能：
  - 后台任务队列管理
  - 异步执行 + 状态查询
  - 任务结果收集和通知
  - 支持多个并发后台任务

使用场景：
  - 长时间运行的脚本（训练模型、大数据处理）
  - 需要等待外部事件的任务
  - 多步骤工作流的异步执行

UI 集成：
  BackgroundAgentPanel 显示任务列表和状态
"""

import os
import json
import threading
import time
import uuid
import queue
import logging
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """后台任务数据模型"""
    task_id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: str = ""
    error: str = ""
    progress: float = 0.0  # 0.0 ~ 1.0
    progress_message: str = ""
    logs: list = field(default_factory=list)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result[:500],
            "error": self.error[:500],
            "progress": self.progress,
            "progress_message": self.progress_message,
            "logs": self.logs[-20:],
            "duration": (self.completed_at - self.started_at) if self.started_at and self.completed_at else
                       (time.time() - self.started_at) if self.started_at else 0,
        }

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def log(self, message: str):
        """添加日志"""
        self.logs.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "message": message,
        })
        logger.info(f"[BackgroundTask:{self.name}] {message}")

    def update_progress(self, progress: float, message: str = ""):
        """更新进度"""
        self.progress = max(0.0, min(1.0, progress))
        if message:
            self.progress_message = message


class BackgroundAgentManager:
    """后台 Agent 管理器 — 单例"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks: Dict[str, BackgroundTask] = {}
                    cls._instance._max_tasks = 10
                    cls._instance._callbacks: list = []
        return cls._instance

    def register_callback(self, callback: Callable):
        """注册状态变更回调"""
        self._callbacks.append(callback)

    def _notify_callbacks(self, task: BackgroundTask, event: str):
        """通知所有回调"""
        for cb in self._callbacks:
            try:
                cb(task.to_dict(), event)
            except Exception as e:
                logger.error(f"回调通知失败: {e}")

    def submit_task(
        self,
        name: str,
        description: str,
        target: Callable,
        args: tuple = (),
        kwargs: dict = None,
    ) -> str:
        """提交一个后台任务

        Args:
            name: 任务名称
            description: 任务描述
            target: 可调用对象，接受 task 作为第一个参数
            args: 额外位置参数
            kwargs: 额外关键字参数

        Returns:
            task_id
        """
        if len(self._tasks) >= self._max_tasks:
            # 清理已完成的旧任务
            self._cleanup_completed()

        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(
            task_id=task_id,
            name=name,
            description=description,
        )

        kwargs = kwargs or {}

        def _run():
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.log("任务开始")
            self._notify_callbacks(task, "started")
            try:
                result = target(task, *args, **kwargs)
                if task.is_cancelled():
                    task.status = TaskStatus.CANCELLED
                    task.log("任务已取消")
                else:
                    task.result = str(result) if result else "完成"
                    task.status = TaskStatus.COMPLETED
                    task.update_progress(1.0, "完成")
                    task.log("任务完成")
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.FAILED
                task.log(f"任务失败: {e}")
                logger.exception(f"后台任务 {name} 失败")
            finally:
                task.completed_at = time.time()
                self._notify_callbacks(task, "completed")

        thread = threading.Thread(target=_run, daemon=True, name=f"bg-{task_id}")
        task._thread = thread
        self._tasks[task_id] = task
        thread.start()

        self._notify_callbacks(task, "created")
        return task_id

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list:
        """获取所有任务（按创建时间倒序）"""
        return [t.to_dict() for t in sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True
        )]

    def get_active_tasks(self) -> list:
        """获取正在运行的任务"""
        return [t.to_dict() for t in self._tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False
        task._cancel_event.set()
        task.status = TaskStatus.CANCELLED
        task.log("用户请求取消")
        self._notify_callbacks(task, "cancelled")
        return True

    def clear_completed(self):
        """清除所有已完成的任务"""
        to_remove = [tid for tid, t in self._tasks.items()
                     if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)]
        for tid in to_remove:
            del self._tasks[tid]
        return len(to_remove)

    def _cleanup_completed(self):
        """清理旧任务"""
        completed = [(tid, t) for tid, t in self._tasks.items()
                     if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)]
        completed.sort(key=lambda x: x[1].completed_at or 0)
        while len(self._tasks) >= self._max_tasks and completed:
            tid, _ = completed.pop(0)
            del self._tasks[tid]


def get_background_manager() -> BackgroundAgentManager:
    """获取后台 Agent 管理器单例"""
    return BackgroundAgentManager()
