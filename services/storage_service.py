"""
存储服务模块
使用 SQLite 持久化会话和消息，重启后自动恢复
"""

import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict

# 数据库路径
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "sessions.db")


def _ensure_db_dir():
    os.makedirs(DB_DIR, exist_ok=True)


class StorageService:
    """SQLite 存储服务"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        _ensure_db_dir()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新对话',
                    file_path TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """)
            # 兼容旧表：尝试添加 file_path 列
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN file_path TEXT DEFAULT ''")
            except Exception:
                pass

    # ---- 会话 CRUD ----

    def create_session(self, session_id: str, title: str = "新对话") -> dict:
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
        return {"id": session_id, "title": title, "created_at": now}

    def get_all_sessions(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, file_path, created_at, updated_at FROM sessions ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_session_title(self, session_id: str, title: str):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, session_id),
            )

    def update_session_file_path(self, session_id: str, file_path: str):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET file_path = ?, updated_at = ? WHERE id = ?",
                (file_path, now, session_id),
            )

    def get_session_file_path(self, session_id: str) -> str:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT file_path FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row["file_path"] if row and row["file_path"] else ""

    def touch_session(self, session_id: str):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )

    def delete_session(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ---- 消息 CRUD ----

    def add_message(self, session_id: str, role: str, content: str):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
        # 更新会话的 updated_at，确保按最新消息时间排序
        self.touch_session(session_id)

    def get_messages(self, session_id: str) -> List[Dict[str, str]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in rows]

    def get_display_messages(self, session_id: str) -> List[tuple]:
        """获取 UI 显示格式 [(text, is_user, timestamp), ...]"""
        messages = self.get_messages(session_id)
        result = []
        for m in messages:
            # 从 ISO 格式提取时间 HH:MM:SS
            timestamp = ""
            if m.get("created_at"):
                try:
                    dt = datetime.fromisoformat(m["created_at"])
                    timestamp = dt.strftime("%H:%M:%S")
                except Exception:
                    pass
            result.append((m["content"], m["role"] == "user", timestamp))
        return result

    def clear_messages(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
