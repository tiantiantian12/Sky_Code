"""
自定义组件模块
包含所有可复用的UI组件
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QFrame, QListWidgetItem, QLineEdit,
                               QGraphicsDropShadowEffect, QApplication, QSizePolicy,
                               QPlainTextEdit, QComboBox, QSpinBox, QFileDialog,
                               QScrollArea, QTextEdit, QButtonGroup, QStackedWidget)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject, QUrl
from PySide6.QtGui import (QColor, QPixmap, QPainter, QPen, QFontMetrics,
                           QSyntaxHighlighter, QTextCharFormat, QFont,
                           QShortcut, QKeySequence, QTextCursor)

import sys
import os
import time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.styles import get_style


class GlassEffect(QGraphicsDropShadowEffect):
    """毛玻璃效果"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBlurRadius(22)
        self.setColor(QColor(0, 0, 0, 16))
        self.setOffset(0, 1)


class TitleBarButton(QPushButton):
    """自绘窗口控制按钮（最小化 / 最大化 / 关闭），颜色随主题切换。

    kind: 'min' | 'max' | 'close'
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(40, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self._theme = 'light'
        self._maximized = False
        self.setObjectName(f"titlebar_{kind}")

    def set_theme(self, theme: str):
        """随主题切换图标颜色"""
        self._theme = theme
        self.update()

    def set_maximized_state(self, maximized: bool):
        """最大化状态切换时更新图标（□ / ❐）"""
        self._maximized = maximized
        self.update()

    def paintEvent(self, event):
        dark = (self._theme == 'dark')
        hovered = self.underMouse()
        pressed = self.isDown()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # ── 背景 ──
        bg = None
        if self.kind == 'close':
            if hovered or pressed:
                bg = QColor(232, 17, 35)  # Windows 关闭按钮红
        else:
            if pressed:
                bg = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 40)
            elif hovered:
                bg = QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 16)
        if bg is not None:
            p.fillRect(self.rect(), bg)

        # ── 图标颜色 ──
        if self.kind == 'close' and (hovered or pressed):
            icon_color = QColor(255, 255, 255)
        else:
            icon_color = QColor(217, 218, 224) if dark else QColor(29, 29, 31)

        pen = QColor(icon_color)
        p.setPen(pen)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self.kind == 'min':
            # 一条水平线
            p.setPen(QPen(icon_color, 1.6))
            p.drawLine(int(cx - 9), int(cy), int(cx + 9), int(cy))
        elif self.kind == 'max':
            if self._maximized:
                # 还原：两个重叠小方块
                p.setPen(QPen(icon_color, 1.4))
                p.drawRect(int(cx - 9), int(cy - 7), 11, 11)
                p.drawRect(int(cx + 1), int(cy - 4), 10, 10)
                # 前面的方块盖住后面的线
                p.setPen(QPen(icon_color, 1.4))
                p.drawLine(int(cx - 8), int(cy - 4), int(cx - 1), int(cy - 4))
            else:
                # 一个方块
                p.setPen(QPen(icon_color, 1.4))
                p.drawRect(int(cx - 8), int(cy - 7), 16, 14)
        else:  # close
            p.setPen(QPen(icon_color, 1.7))
            p.drawLine(int(cx - 7), int(cy - 7), int(cx + 7), int(cy + 7))
            p.drawLine(int(cx + 7), int(cy - 7), int(cx - 7), int(cy + 7))

        p.end()


class StreamingWorker(QObject):
    """流式输出工作线程"""
    text_ready = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.is_running = True

    def run(self):
        """模拟流式输出"""
        try:
            for i, char in enumerate(self.text):
                if not self.is_running:
                    break
                QThread.msleep(50)
                self.text_ready.emit(self.text[:i + 1])
            if self.is_running:
                self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        """停止流式输出"""
        self.is_running = False


class AgentStatusLine(QWidget):
    """Agent 执行中的实时状态行显示

    显示工具执行进度等状态信息，使用小字体、不同样式与正文区分。
    例如：🌐 正在 web_search... | 📁 正在读取文件...
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_status = ""
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_animation)
        self._anim_index = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(8)

        # 图标 + 状态文本
        self._icon_label = QLabel("")
        self._icon_label.setStyleSheet("font-size: 13px; background: transparent;")

        self._text_label = QLabel("")
        # 根据当前主题设置状态文本颜色
        try:
            main_window = self.window()
            theme = getattr(main_window, 'current_theme', 'light') if main_window else 'light'
        except Exception:
            theme = 'light'
        status_color = '#6e7fe0' if theme == 'dark' else '#6366f1'
        self._text_label.setStyleSheet(
            f"color: {status_color}; font-size: 12px; font-style: italic; "
            "background: transparent;"
        )
        self._text_label.setWordWrap(True)

        layout.addWidget(self._icon_label)
        layout.addWidget(self._text_label, 1)
        layout.addStretch()

        self.hide()

    def apply_theme(self, theme: str):
        """主题切换时更新状态文本颜色"""
        if not hasattr(self, '_text_label'):
            return
        status_color = '#6e7fe0' if theme == 'dark' else '#6366f1'
        self._text_label.setStyleSheet(
            f"color: {status_color}; font-size: 12px; font-style: italic; "
            "background: transparent;"
        )

    # 工具图标映射
    _TOOL_ICONS = {
        'web_search': '🌐',
        'read_file': '📄',
        'write_file': '✍️',
        'edit_file': '✏️',
        'execute_code': '▶️',
        'list_files': '📁',
        'file_search': '🔍',
        'browser_action': '🌍',
        'api_call': '📡',
        'default': '⚙️',
    }

    def set_status(self, tool_name: str, action: str):
        """设置当前状态

        Args:
            tool_name: 工具名称，用于选择图标
            action: 状态描述文本，例如 "正在搜索..." 或 "正在读取..."
        """
        icon = self._TOOL_ICONS.get(tool_name, self._TOOL_ICONS['default'])
        self._icon_label.setText(icon)
        self._current_status = action
        self._text_label.setText(action)
        if not self.isVisible():
            self.show()
        if not self._timer.isActive():
            self._timer.start()

    def clear(self):
        """清除状态"""
        self._current_status = ""
        self._text_label.setText("")
        self._icon_label.setText("")
        self.hide()
        if self._timer.isActive():
            self._timer.stop()

    def _update_animation(self):
        """周期性添加"..."动画效果"""
        if not self._current_status:
            return
        dots = "..." * ((self._anim_index % 3) + 1)
        self._text_label.setText(self._current_status + dots)
        self._anim_index += 1


class ChatMessageWidget(QWidget):
    """单条消息组件（带头像）"""
    stop_generation = Signal()
    retry_requested = Signal()
    copy_requested = Signal(str)  # text to copy

    # 全局头像（可通过外部设置）
    _user_avatar: QPixmap = None
    _agent_avatar: QPixmap = None

    @classmethod
    def set_user_avatar(cls, pixmap: QPixmap):
        cls._user_avatar = pixmap

    @classmethod
    def set_agent_avatar(cls, pixmap: QPixmap):
        cls._agent_avatar = pixmap

    def __init__(self, text: str, is_user: bool, parent=None, timestamp: str = "", thinking_time: str = ""):
        super().__init__(parent)
        self.text = text
        self.is_user = is_user
        self.timestamp = timestamp
        self.thinking_time = thinking_time
        self.streaming_worker = None
        self.streaming_thread = None
        # 当前主题（深色/浅色），用于 markdown 渲染颜色
        try:
            main_window = self.window()
            self._theme = getattr(main_window, 'current_theme', 'light') if main_window else 'light'
        except Exception:
            self._theme = 'light'
        self.setup_ui()

    def _make_avatar_label(self, is_user_avatar: bool) -> QLabel:
        """创建头像标签"""
        avatar = QLabel()
        avatar.setFixedSize(36, 36)
        avatar.setStyleSheet("""
            QLabel {
                background: transparent;
                border-radius: 18px;
            }
        """)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setScaledContents(True)

        if is_user_avatar and self._user_avatar:
            avatar.setPixmap(self._user_avatar)
        elif not is_user_avatar and self._agent_avatar:
            avatar.setPixmap(self._agent_avatar)
        else:
            # 默认头像：emoji
            if is_user_avatar:
                avatar.setText("👤")
                avatar.setStyleSheet("""
                    QLabel {
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                            stop:0 #6366f1, stop:1 #a855f7);
                        border-radius: 18px; font-size: 18px; color: white;
                    }
                """)
            else:
                avatar.setText("✦")
                avatar.setStyleSheet("""
                    QLabel {
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                            stop:0 #10b981, stop:1 #06b6d4);
                        border-radius: 18px; font-size: 18px; color: white;
                    }
                """)
        return avatar

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(8)

        if self.is_user:
            avatar = self._make_avatar_label(True)
            message_container = QWidget()
            message_container.setMaximumWidth(480)
            message_container.setStyleSheet(get_style('user_message', self._theme))
            self._message_container = message_container
            message_layout = QVBoxLayout(message_container)
            # 时间标签
            if self.timestamp:
                time_label = QLabel(self.timestamp)
                time_label.setStyleSheet(
                    "color: rgba(255,255,255,0.6); font-size: 11px; background: transparent;")
                time_label.setAlignment(Qt.AlignRight)
                message_layout.addWidget(time_label)
            self.message_label = QLabel(self.text)
            self.message_label.setWordWrap(True)
            self.message_label.setStyleSheet(
                "color: white; font-size: 14px; background: transparent;")
            self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            message_layout.addWidget(self.message_label)
            layout.addStretch()
            layout.addWidget(message_container)
            layout.addWidget(avatar)
        else:
            layout.setContentsMargins(16, 12, 16, 8)
            layout.setSpacing(0)

            # 顶部：头像 + 名称（无气泡）
            header_row = QWidget()
            header_row.setStyleSheet("background: transparent;")
            header_layout = QHBoxLayout(header_row)
            header_layout.setContentsMargins(0, 0, 0, 8)
            header_layout.setSpacing(8)

            avatar = self._make_avatar_label(False)
            self._agent_name_label = QLabel("Sky Code")
            # 根据当前主题设置名称颜色（深色主题使用浅色文字）
            theme = self._theme
            name_color = '#d9dae0' if theme == 'dark' else '#1d1d1f'
            self._agent_name_label.setStyleSheet(
                f"color: {name_color}; font-size: 14px; font-weight: bold; background: transparent;")
            header_layout.addWidget(avatar)
            header_layout.addWidget(self._agent_name_label)
            header_layout.addStretch()

            self._raw_text = self.text
            self._status_text = ""
            self._is_html_mode = False

            self.status_block = QLabel()
            self.status_block.setWordWrap(True)
            self.status_block.setTextFormat(Qt.RichText)
            self.status_block.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.status_block.hide()
            # 根据当前主题设置状态块样式（深色主题使用深色背景）
            theme = self._theme
            if theme == 'dark':
                self.status_block.setStyleSheet("""
                    QLabel {
                        background: #1e1e2e;
                        border: 1px solid rgba(255,255,255,0.08);
                        border-left: 3px solid #6e7fe0;
                        border-radius: 8px;
                        padding: 10px 12px;
                        color: #a6adc8;
                        font-size: 12px;
                        line-height: 1.5;
                    }
                """)
            else:
                self.status_block.setStyleSheet("""
                    QLabel {
                        background: #eef2ff;
                        border: 1px solid #c7d2fe;
                        border-left: 3px solid #6366f1;
                        border-radius: 8px;
                        padding: 10px 12px;
                        color: #475569;
                        font-size: 12px;
                        line-height: 1.5;
                    }
                """)

            self.message_container = QWidget()
            self.message_container.setStyleSheet("background: transparent;")
            message_layout = QVBoxLayout(self.message_container)
            message_layout.setContentsMargins(0, 0, 0, 0)
            message_layout.setSpacing(8)

            if self.thinking_time:
                self._time_label = QLabel(self.thinking_time)
                self._time_label.setStyleSheet(
                    "color: #86868b; font-size: 11px; background: transparent;")
                message_layout.addWidget(self._time_label)

            self.message_label = QLabel()
            self.message_label.setWordWrap(True)
            self.message_label.setTextFormat(Qt.RichText)
            self.message_label.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            self.message_label.setOpenExternalLinks(False)
            # 根据当前主题设置文本颜色（深色主题使用浅色文字）
            try:
                # 尝试从主窗口获取当前主题
                main_window = self.window()
                theme = getattr(main_window, 'current_theme', 'light') if main_window else 'light'
            except Exception:
                theme = 'light'
            text_color = '#ececec' if theme == 'dark' else '#1d1d1f'
            self.message_label.setStyleSheet(f"""
                QLabel {{
                    color: {text_color};
                    font-size: 14px;
                    line-height: 1.6;
                    background: transparent;
                    padding: 0;
                }}
            """)

            if self.text:
                self._render_html(self.text)
            else:
                self.message_label.setText("")

            self._file_panel_host = QWidget()
            self._file_panel_host.setStyleSheet("background: transparent;")
            self._file_panel_layout = QVBoxLayout(self._file_panel_host)
            self._file_panel_layout.setContentsMargins(0, 4, 0, 0)
            self._file_panel_layout.setSpacing(0)
            self._file_panel_host.hide()

            self.stop_container = QWidget()
            stop_layout = QHBoxLayout(self.stop_container)
            stop_layout.setContentsMargins(0, 8, 0, 0)
            self.stop_btn = QPushButton("停止生成")
            self.stop_btn.setFixedSize(72, 26)
            self.stop_btn.setStyleSheet(get_style('stop_btn'))
            self.stop_btn.clicked.connect(self.on_stop_clicked)
            stop_layout.addWidget(self.stop_btn)
            stop_layout.addStretch()

            self.cursor_label = QLabel("▌")
            self.cursor_label.setStyleSheet(get_style('cursor'))
            self.cursor_label.hide()
            self.cursor_timer = QTimer()
            self.cursor_timer.timeout.connect(self.toggle_cursor)

            message_layout.addWidget(self.status_block)
            message_layout.addWidget(self.message_label)
            message_layout.addWidget(self._file_panel_host)
            message_layout.addWidget(self.cursor_label)
            message_layout.addWidget(self.stop_container)

            # Agent 状态行（工具执行中实时状态）
            self._agent_status_line = AgentStatusLine()
            message_layout.addWidget(self._agent_status_line)

            # 生成状态标签（流式输出时显示 token 计数）
            self._gen_status_label = QLabel("")
            self._gen_status_label.setStyleSheet(
                "color: #30d158; font-size: 11px; background: transparent;")
            self._gen_status_label.hide()
            message_layout.addWidget(self._gen_status_label)

            # 消息操作栏（复制/重试，流式完成后显示）
            self._action_bar = MessageActionBar()
            self._action_bar.hide()
            self._action_bar.copy_clicked.connect(self._on_copy)
            self._action_bar.retry_clicked.connect(self.retry_requested.emit)
            message_layout.addWidget(self._action_bar)

            outer = QVBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(header_row)
            outer.addWidget(self.message_container)

            outer_widget = QWidget()
            outer_widget.setStyleSheet("background: transparent;")
            outer_widget.setLayout(outer)

            layout.addWidget(outer_widget, 1)
            self.stop_container.hide()

    def start_streaming(self, full_text: str):
        """开始流式输出"""
        self.text = full_text
        self.stop_container.show()
        self.cursor_label.show()
        self.cursor_timer.start(500)

        self.streaming_thread = QThread()
        self.streaming_worker = StreamingWorker(full_text)
        self.streaming_worker.moveToThread(self.streaming_thread)

        self.streaming_thread.started.connect(self.streaming_worker.run)
        self.streaming_worker.text_ready.connect(self.update_text)
        self.streaming_worker.finished.connect(self.on_streaming_finished)
        self.streaming_worker.error.connect(self.on_streaming_error)

        self.streaming_thread.start()

    def set_status_log(self, text: str):
        """显示连接/登录等状态日志（与正文回复区分样式）"""
        if self.is_user or not hasattr(self, "status_block"):
            return
        import html as html_module
        self._status_text = (text or "").rstrip()
        lines = [ln.strip() for ln in self._status_text.splitlines() if ln.strip()]
        if not lines:
            self.status_block.hide()
            return
        rows = "".join(
            f'<div style="margin:2px 0;">'
            f'<span style="color:#6366f1;font-weight:600;">●</span> '
            f'{html_module.escape(line)}</div>'
            for line in lines
        )
        self.status_block.setText(
            f'<div style="font-family:Segoe UI,Microsoft YaHei,sans-serif;">{rows}</div>'
        )
        self.status_block.show()

    def update_text(self, text: str, streaming: bool = False):
        """更新正文（不含状态日志）。

        streaming=True 时仅用纯文本，外部已用 16ms 定时器批处理调用，
        保证稳定 60fps 渲染，避免每个微小 chunk 触发 O(n) 全文换行重算。
        流式结束后由 finalize_markdown() 统一渲染 HTML。
        """
        self._raw_text = text
        self.text = text

        if streaming:
            # 关键：一旦 HTML 渲染已启动（_is_html_mode=True），
            # 不要用纯文本覆盖，否则 markdown 格式会闪烁消失。
            # 仅更新 _raw_text，让 HTML 定时器在下次 tick 时重新渲染。
            if self._is_html_mode:
                return
            # 初始阶段：使用纯文本快速渲染（60fps）
            if not hasattr(self, '_stream_plain_set'):
                self.message_label.setTextFormat(Qt.PlainText)
                self._stream_plain_set = True
            self.message_label.setText(text)
        else:
            self._stream_plain_set = False
            self.message_label.setTextFormat(Qt.PlainText)
            self.message_label.setText(text)
            self._is_html_mode = False

    def _current_theme(self) -> str:
        """获取当前主题（deep）"""
        return getattr(self, '_theme', 'light') or 'light'

    def _stream_render_html(self):
        """流式阶段实时渲染 Markdown HTML（节流回调）
        使用 render_markdown_fast 跳过 pygments 高亮，大幅提升性能。"""
        text = getattr(self, '_raw_text', '') or ''
        if not text:
            return
        try:
            from ui.markdown_renderer import render_markdown_fast
            result = render_markdown_fast(text, theme=self._current_theme())
            self._code_blocks = result.code_blocks
            self.message_label.setTextFormat(Qt.RichText)
            self.message_label.setText(result.html)
            self._is_html_mode = True
            # 只连接一次 linkActivated，避免重复 disconnect/connect
            if not getattr(self, '_link_connected', False):
                self.message_label.linkActivated.connect(self._on_link_clicked)
                self._link_connected = True
        except Exception:
            # 渲染失败时回退纯文本
            self.message_label.setTextFormat(Qt.PlainText)
            self.message_label.setText(text)
            self._is_html_mode = False

    def _render_html(self, markdown_text: str):
        """将 markdown 渲染为带内联样式的 HTML 并显示（完整渲染，含 pygments）"""
        try:
            from ui.markdown_renderer import render_markdown
            result = render_markdown(markdown_text, theme=self._current_theme())
            self._code_blocks = result.code_blocks
            self.message_label.setTextFormat(Qt.RichText)
            self.message_label.setText(result.html)
            self._is_html_mode = True
            # 只连接一次 linkActivated，避免重复 disconnect/connect
            if not getattr(self, '_link_connected', False):
                self.message_label.linkActivated.connect(self._on_link_clicked)
                self._link_connected = True
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "Markdown 渲染失败，回退到纯文本: %s", e, exc_info=True)
            self.message_label.setTextFormat(Qt.PlainText)
            self.message_label.setText(markdown_text)

    def apply_theme(self, theme: str):
        """主题切换时刷新消息内所有文字颜色并重新渲染 markdown"""
        self._theme = theme
        is_dark = theme == 'dark'

        # 用户气泡容器背景（setup_ui 时已按构造时主题设过，这里确保切换/初始化一致）
        if self.is_user and hasattr(self, '_message_container'):
            try:
                self._message_container.setStyleSheet(get_style('user_message', theme))
            except Exception:
                pass

        # AI 名称
        if hasattr(self, '_agent_name_label'):
            name_color = '#d9dae0' if is_dark else '#1d1d1f'
            self._agent_name_label.setStyleSheet(
                f"color: {name_color}; font-size: 14px; font-weight: bold; background: transparent;")

        # 正文标签基础颜色（用户消息气泡内保持白色）
        if hasattr(self, 'message_label'):
            if self.is_user:
                self.message_label.setStyleSheet(
                    "color: white; font-size: 14px; background: transparent;")
            else:
                text_color = '#d9dae0' if is_dark else '#1d1d1f'
                self.message_label.setStyleSheet(f"""
                    QLabel {{
                        color: {text_color};
                        font-size: 14px;
                        line-height: 1.6;
                        background: transparent;
                        padding: 0;
                    }}
                """)
            # 已有 markdown 内容重新用新主题渲染（仅 AI 消息）
            if (not self.is_user and getattr(self, '_is_html_mode', False)
                    and getattr(self, '_raw_text', '')):
                try:
                    from ui.markdown_renderer import render_markdown
                    result = render_markdown(self._raw_text, theme=theme)
                    self._code_blocks = result.code_blocks
                    self.message_label.setTextFormat(Qt.RichText)
                    self.message_label.setText(result.html)
                except Exception:
                    pass

        # 耗时标签
        if hasattr(self, '_time_label'):
            time_color = '#a0a0a0' if is_dark else '#86868b'
            self._time_label.setStyleSheet(
                f"color: {time_color}; font-size: 11px; background: transparent;")

        # 状态块（工具执行状态）
        if hasattr(self, 'status_block'):
            if is_dark:
                self.status_block.setStyleSheet("""
                    QLabel {
                        background: #1e1e2e;
                        border: 1px solid rgba(255,255,255,0.08);
                        border-left: 3px solid #6e7fe0;
                        border-radius: 8px;
                        padding: 10px 12px;
                        color: #a6adc8;
                        font-size: 12px;
                        line-height: 1.5;
                    }
                """)
            else:
                self.status_block.setStyleSheet("""
                    QLabel {
                        background: #eef2ff;
                        border: 1px solid #c7d2fe;
                        border-left: 3px solid #6366f1;
                        border-radius: 8px;
                        padding: 10px 12px;
                        color: #475569;
                        font-size: 12px;
                        line-height: 1.5;
                    }
                """)

        # Agent 状态行（工具执行中）
        if hasattr(self, '_agent_status_line') and hasattr(self._agent_status_line, 'apply_theme'):
            try:
                self._agent_status_line.apply_theme(theme)
            except Exception:
                pass

        # 消息操作栏（复制/重试按钮）
        if hasattr(self, '_action_bar') and hasattr(self._action_bar, 'apply_theme'):
            try:
                self._action_bar.apply_theme(theme)
            except Exception:
                pass

        # 思考面板（Agent 模式下挂在消息下的 CollapsibleThinking）
        if hasattr(self, '_thinking_widget') and hasattr(self._thinking_widget, 'apply_theme'):
            try:
                self._thinking_widget.apply_theme(theme)
            except Exception:
                pass

    def showEvent(self, event):
        """消息显示时自动同步主窗口主题。

        实时输出等路径直接创建 ChatMessageWidget 而未显式调用 apply_theme，
        创建时窗口可能尚未挂载导致 _theme 误判为 light。
        这里在消息首次显示时兜底同步，确保任何创建路径颜色都正确。
        """
        super().showEvent(event)
        try:
            main_window = self.window()
            theme = getattr(main_window, 'current_theme', None) if main_window else None
            if theme and theme != getattr(self, '_theme', None):
                self.apply_theme(theme)
        except Exception:
            pass

    def _on_link_clicked(self, link: str):
        """处理复制按钮点击"""
        if link.startswith("copy_"):
            try:
                idx = int(link.split("_")[1])
                if hasattr(self, '_code_blocks') and idx < len(self._code_blocks):
                    clipboard = QApplication.clipboard()
                    clipboard.setText(self._code_blocks[idx])
            except (ValueError, IndexError):
                pass

    def finalize_markdown(self):
        """将正文从纯文本渲染为 Markdown HTML（代码块背景、高亮等）"""
        if self.is_user:
            return
        # 停止流式渲染定时器（如果正在运行）
        if hasattr(self, '_stream_render_timer'):
            self._stream_render_timer.stop()
        if hasattr(self, "cursor_timer"):
            try:
                self.cursor_timer.stop()
            except Exception:
                pass
        if hasattr(self, "cursor_label"):
            self.cursor_label.hide()
        if hasattr(self, "stop_container"):
            self.stop_container.hide()
        text = (getattr(self, "_raw_text", None) or self.text or "").strip()
        if not text:
            return
        self._raw_text = text
        self.text = text
        import logging
        logging.getLogger(__name__).debug(
            "finalize_markdown: text length=%d, preview=%.80s", len(text), text[:80])
        self._render_html(text)
        # 显示操作栏
        if hasattr(self, '_action_bar'):
            self._action_bar.show()
        # 隐藏生成状态
        if hasattr(self, '_gen_status_label'):
            self._gen_status_label.hide()

    def on_streaming_finished(self):
        """流式输出完成 — 将纯文本转为富文本 HTML 渲染"""
        self.finalize_markdown()
        if self.streaming_thread:
            self.streaming_thread.quit()
            self.streaming_thread.wait()

    def set_thinking_time(self, text: str):
        """在 AI 消息顶部添加耗时标签"""
        if hasattr(self, 'message_container'):
            layout = self.message_container.layout()
            time_label = QLabel(text)
            time_label.setStyleSheet(
                "color: #86868b; font-size: 11px; background: transparent;")
            layout.insertWidget(0, time_label)

    def on_streaming_error(self, error_msg: str):
        """流式输出错误"""
        self.cursor_timer.stop()
        self.cursor_label.hide()
        self._raw_text = f"错误: {error_msg}"
        self.message_label.setTextFormat(Qt.PlainText)
        self.message_label.setText(self._raw_text)
        self.stop_container.hide()
        if hasattr(self, '_gen_status_label'):
            self._gen_status_label.hide()

    def update_gen_status(self, tokens: int, rate: float):
        """更新生成状态（token 计数 + 速率）"""
        if hasattr(self, '_gen_status_label') and not self.is_user:
            self._gen_status_label.setText(
                f"生成中 · {tokens} tokens · {rate:.1f} tok/s")
            self._gen_status_label.show()

    def finalize_gen_status(self, tokens: int, elapsed: float):
        """完成生成状态"""
        if hasattr(self, '_gen_status_label') and not self.is_user:
            if tokens > 0:
                self._gen_status_label.setText(
                    f"✓ {tokens} tokens · {elapsed:.1f}s")
            else:
                self._gen_status_label.hide()

    def set_agent_status(self, tool_name: str, action: str):
        """设置 Agent 工具执行状态

        Args:
            tool_name: 工具名称（如 'web_search', 'read_file'）
            action: 状态描述（如 '正在搜索...', '正在读取文件...'）
        """
        if hasattr(self, '_agent_status_line') and not self.is_user:
            self._agent_status_line.set_status(tool_name, action)

    def clear_agent_status(self):
        """清除 Agent 状态"""
        if hasattr(self, '_agent_status_line') and not self.is_user:
            self._agent_status_line.clear()

    def _on_copy(self):
        """复制按钮点击"""
        text = self.get_text()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def on_stop_clicked(self):
        """停止按钮点击"""
        if self.streaming_worker:
            self.streaming_worker.stop()
        self.stop_generation.emit()
        self.on_streaming_finished()
        stop_text = (self._raw_text or "") + "\n\n[已停止生成]"
        self._raw_text = stop_text
        self._render_html(stop_text)

    def toggle_cursor(self):
        """切换光标显示"""
        if self.cursor_label.isVisible():
            self.cursor_label.hide()
        else:
            self.cursor_label.show()

    def get_text(self) -> str:
        """获取原始文本（markdown 或纯文本）"""
        return getattr(self, '_raw_text', self.text) or self.text or ""

    def attach_file_panel(self, panel: QWidget):
        """在 AI 回复下方附加文件变更列表面板"""
        if not hasattr(self, '_file_panel_layout'):
            return
        while self._file_panel_layout.count():
            item = self._file_panel_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._file_panel_layout.addWidget(panel)
        self._file_panel_host.show()


class FileChangesPanel(QWidget):
    """可展开的文件变更列表（类似 CodeBuddy 文件列表）"""

    _SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files = {}
        self._expanded = False
        self._pending_files = {}   # {norm_path: tool_name}
        self._spinner_timer = None
        self._spinner_frame = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFixedHeight(36)
        self._header.setStyleSheet("""
            QWidget {
                background: #f3f4f6;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QWidget:hover { background: #eceef2; }
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        self._arrow = QLabel("▸")
        self._arrow.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        self._title = QLabel("文件列表 (0)")
        self._title.setStyleSheet(
            "color: #374151; font-size: 13px; font-weight: bold; background: transparent;")
        header_layout.addWidget(self._arrow)
        header_layout.addWidget(self._title)
        header_layout.addStretch()
        self._header.mousePressEvent = lambda e: self._toggle()

        self._body = QWidget()
        self._body.setStyleSheet("""
            QWidget {
                background: #fafbfc;
                border: 1px solid #e5e7eb;
                border-top: none;
                border-radius: 0 0 8px 8px;
            }
        """)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 8, 8, 8)
        self._body_layout.setSpacing(4)
        self._body.hide()

        layout.addWidget(self._header)
        layout.addWidget(self._body)

    def expand(self):
        """展开文件列表（恢复历史会话时使用）"""
        if not self._expanded:
            self._toggle()

    def _toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._body.show()
            self._arrow.setText("▾")
            self._header.setStyleSheet("""
                QWidget {
                    background: #f3f4f6;
                    border: 1px solid #e5e7eb;
                    border-radius: 8px 8px 0 0;
                }
            """)
        else:
            self._body.hide()
            self._arrow.setText("▸")
            self._header.setStyleSheet("""
                QWidget {
                    background: #f3f4f6;
                    border: 1px solid #e5e7eb;
                    border-radius: 8px;
                }
                QWidget:hover { background: #eceef2; }
            """)

    def add_file(self, op_type: str, file_path: str, added=0, removed=0):
        name = os.path.basename(file_path) if file_path else "unknown"
        try:
            added = int(added)
        except (TypeError, ValueError):
            added = 0
        try:
            removed = int(removed)
        except (TypeError, ValueError):
            removed = 0
        # 规范化路径，与 mark_file_editing / resolve_file_editing 保持一致
        key = os.path.normpath(file_path or name).replace('\\', '/') if file_path else name
        if key in self._files:
            existing = self._files[key]
            existing["added"] += added
            existing["removed"] += removed
            if op_type == "新增" and existing["op_type"] != "删除":
                existing["op_type"] = "新增"
            elif op_type == "删除":
                existing["op_type"] = "删除"
            else:
                existing["op_type"] = "修改"
        else:
            self._files[key] = {
                "op_type": op_type,
                "name": name,
                "path": file_path or name,
                "added": added,
                "removed": removed,
            }
        self._rebuild()

    def _rebuild(self):
        self._title.setText(f"文件列表 ({len(self._files)})")
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for info in self._files.values():
            row = QWidget()
            row.setStyleSheet("""
                QWidget {
                    background: #ffffff;
                    border: 1px solid #eef0f3;
                    border-radius: 6px;
                }
            """)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 10, 6)
            row_layout.setSpacing(8)

            icon = QLabel("🐍" if info["name"].endswith(".py") else "📄")
            icon.setFixedWidth(18)
            icon.setStyleSheet("background: transparent; font-size: 14px;")

            name_label = QLabel(info["name"])
            name_label.setStyleSheet(
                "color: #1f2937; font-size: 13px; font-weight: bold; background: transparent;")
            path_label = QLabel(info["path"])
            path_label.setStyleSheet(
                "color: #9ca3af; font-size: 11px; background: transparent;")
            path_label.setToolTip(info["path"])

            stats = QWidget()
            stats_layout = QHBoxLayout(stats)
            stats_layout.setContentsMargins(0, 0, 0, 0)
            stats_layout.setSpacing(6)
            if info["added"] > 0:
                add_l = QLabel(f"+{info['added']}")
                add_l.setStyleSheet(
                    "color: #16a34a; font-size: 12px; font-weight: bold; background: transparent;")
                stats_layout.addWidget(add_l)
            if info["removed"] > 0:
                rm_l = QLabel(f"-{info['removed']}")
                rm_l.setStyleSheet(
                    "color: #ef4444; font-size: 12px; font-weight: bold; background: transparent;")
                stats_layout.addWidget(rm_l)
            if info["op_type"] == "新增" and info["added"] == 0 and info["removed"] == 0:
                add_l = QLabel("新增")
                add_l.setStyleSheet(
                    "color: #16a34a; font-size: 12px; background: transparent;")
                stats_layout.addWidget(add_l)
            elif info["op_type"] == "删除":
                rm_l = QLabel("删除")
                rm_l.setStyleSheet(
                    "color: #ef4444; font-size: 12px; background: transparent;")
                stats_layout.addWidget(rm_l)

            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            text_col.addWidget(name_label)
            text_col.addWidget(path_label)

            row_layout.addWidget(icon)
            row_layout.addLayout(text_col, 1)
            row_layout.addWidget(stats)

            # 正在编辑的文件名左侧显示转圈动画，否则显示绿点
            is_editing = info.get("editing", False)
            if is_editing:
                spinner = QLabel(self._SPINNER_FRAMES[self._spinner_frame])
                spinner.setStyleSheet(
                    "color: #f59e0b; font-size: 13px; font-weight: bold; background: transparent;")
                row._editing_spinner = spinner
                row_layout.addWidget(spinner)
            else:
                dot = QLabel("●")
                dot.setStyleSheet("color: #22c55e; font-size: 8px; background: transparent;")
                row_layout.addWidget(dot)

            self._body_layout.addWidget(row)

        if self._files and not self._expanded:
            self._toggle()

    # ── 文件编辑转圈动画 ──

    def mark_file_editing(self, file_path: str, tool_name: str = ""):
        """标记文件正在编辑，文件名左侧显示转圈动画"""
        norm = os.path.normpath(file_path).replace('\\', '/')
        self._pending_files[norm] = tool_name
        name = os.path.basename(file_path)
        if norm not in self._files:
            self._files[norm] = {
                "op_type": "编辑中",
                "name": name,
                "path": file_path,
                "added": 0,
                "removed": 0,
                "editing": True,
            }
        else:
            self._files[norm]["editing"] = True
        self._rebuild()
        self._start_spinner()

    def resolve_file_editing(self, file_path: str, op_type: str = "修改",
                              added: int = 0, removed: int = 0):
        """文件编辑完成：停止转圈，更新最终状态"""
        norm = os.path.normpath(file_path).replace('\\', '/')
        self._pending_files.pop(norm, None)
        if norm in self._files:
            self._files[norm]["editing"] = False
            if op_type:
                self._files[norm]["op_type"] = op_type
            self._files[norm]["added"] += max(0, added)
            self._files[norm]["removed"] += max(0, removed)
        if not self._pending_files:
            self._stop_spinner()
        self._rebuild()

    def resolve_all_pending(self):
        """清除所有待处理的文件编辑转圈（API 完成时的兜底清理）"""
        for norm in list(self._pending_files.keys()):
            if norm in self._files:
                self._files[norm]["editing"] = False
        self._pending_files.clear()
        self._stop_spinner()
        self._rebuild()

    def _start_spinner(self):
        if self._spinner_timer is None:
            self._spinner_timer = QTimer(self)
            self._spinner_timer.timeout.connect(self._tick_spinner)
            self._spinner_timer.start(120)

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer.deleteLater()
            self._spinner_timer = None

    def _tick_spinner(self):
        self._spinner_frame = (self._spinner_frame + 1) % len(self._SPINNER_FRAMES)
        frame = self._SPINNER_FRAMES[self._spinner_frame]
        for i in range(self._body_layout.count()):
            item = self._body_layout.itemAt(i)
            w = item.widget() if item else None
            if w and hasattr(w, '_editing_spinner'):
                w._editing_spinner.setText(frame)


class SessionItemWidget(QWidget):
    """会话列表项 - 增强版，支持图标、活动状态、预览"""
    delete_clicked = Signal()
    rename_clicked = Signal()
    pin_clicked = Signal()
    context_menu_requested = Signal(object)  # QPoint

    def __init__(self, title: str, last_message_time: str, parent=None,
                 preview: str = "", msg_count: int = 0, is_active: bool = False,
                 is_pinned: bool = False, theme: str = None):
        super().__init__(parent)
        self.title = title
        self.last_message_time = last_message_time
        self.preview = preview
        self.msg_count = msg_count
        self._is_active = is_active
        self._is_pinned = is_pinned
        # 创建时显式传入主题，避免依赖 self.window().current_theme 尚未初始化的时机
        self._theme = theme
        self.title_label = None
        self.time_label = None
        self.pin_icon = None
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("session_item")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(40)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.context_menu_requested.emit(pos))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(6)

        # Pin 图标
        self.pin_icon = QLabel("📌" if self._is_pinned else "")
        self.pin_icon.setFixedWidth(14)
        self.pin_icon.setStyleSheet("font-size: 10px; background: transparent; color: #f59e0b;")

        self.title_label = QLabel(self.title)
        self.title_label.setWordWrap(False)
        self._elide_title()
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.time_label = QLabel(self._format_time(self.last_message_time))
        self.time_label.setStyleSheet("""
            background: transparent;
        """)
        self.time_label.setFixedWidth(48)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setFixedWidth(48)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.delete_btn = QPushButton("✕")
        self.delete_btn.setFixedSize(18, 18)
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setToolTip("删除会话")
        self.delete_btn.hide()
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.1);
                color: #ef4444;
                border: none;
                border-radius: 9px;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.25);
                color: #dc2626;
            }
        """)
        self.delete_btn.clicked.connect(self._on_delete_clicked)

        layout.addWidget(self.pin_icon)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.time_label)
        layout.addWidget(self.delete_btn)

        self.setCursor(Qt.PointingHandCursor)
        self._apply_active_style()

    def _elide_title(self, max_width: int = 180):
        """根据可用宽度截断标题，超出部分显示省略号"""
        if not self.title_label or not self.title:
            return
        fm = self.title_label.fontMetrics()
        elided = fm.elidedText(self.title, Qt.ElideRight, max_width)
        self.title_label.setText(elided)

    def _format_time(self, time_str: str) -> str:
        """格式化时间显示，缩短过长的时间字符串"""
        if not time_str:
            return ""
        # 如果是 "YYYY-MM-DD HH:MM" 格式，简化为更短的形式
        try:
            if len(time_str) >= 16 and time_str[4] == '-':
                parts = time_str.split(' ')
                date_part = parts[0]  # YYYY-MM-DD
                time_part = parts[1][:5] if len(parts) > 1 else ""  # HH:MM
                today = datetime.now().strftime("%Y-%m-%d")
                yesterday = (datetime.now().replace(day=datetime.now().day - 1)).strftime("%Y-%m-%d")
                if date_part == today:
                    return time_part
                elif date_part == yesterday:
                    return "昨天"
                else:
                    # MM/DD 格式
                    return date_part[5:].replace('-', '/')
        except Exception:
            pass
        return time_str

    def _apply_active_style(self):
        """根据 active 状态应用样式"""
        # 优先使用构造时传入的 _theme，回退到主窗口当前主题
        if getattr(self, '_theme', None):
            theme = self._theme
        else:
            try:
                main_window = self.window()
                theme = getattr(main_window, 'current_theme', 'light') if main_window else 'light'
            except Exception:
                theme = 'light'
        
        if theme == 'dark':
            active_bg = 'rgba(110, 127, 224, 0.12)'
            active_border = '#6e7fe0'
            active_title = '#d9dae0'
            hover_bg = 'rgba(110, 127, 224, 0.06)'
            default_title = '#d9dae0'
            time_color = '#6f7178'
        else:
            active_bg = 'rgba(99, 102, 241, 0.1)'
            active_border = '#6366f1'
            active_title = '#1d1d1f'
            hover_bg = 'rgba(99, 102, 241, 0.06)'
            default_title = '#1d1d1f'
            time_color = '#b4b4ba'

        title_color = active_title if self._is_active else default_title
        self.time_label.setStyleSheet(f"""
            color: {time_color};
            font-size: 10.5px;
            background: transparent;
        """)

        if self._is_active:
            self.setStyleSheet(f"""
                QWidget#session_item {{
                    background: {active_bg};
                    border-radius: 8px;
                    border-left: 3px solid {active_border};
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {title_color};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            """)
        else:
            self.setStyleSheet(f"""
                QWidget#session_item {{
                    background: transparent;
                    border-radius: 8px;
                    border-left: 3px solid transparent;
                }}
                QWidget#session_item:hover {{
                    background: {hover_bg};
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {title_color};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            """)

    def set_active(self, active: bool):
        """设置当前活动状态"""
        self._is_active = active
        self._apply_active_style()

    def apply_theme(self, theme: str):
        """主题切换时刷新文字颜色与背景"""
        self._theme = theme
        self._apply_active_style()

    def is_active(self) -> bool:
        return self._is_active

    def set_pinned(self, pinned: bool):
        """设置置顶状态"""
        self._is_pinned = pinned
        self.pin_icon.setText("📌" if pinned else "")

    def is_pinned(self) -> bool:
        return self._is_pinned

    def set_title(self, title: str):
        """更新标题"""
        self.title = title
        if self.title_label:
            self._elide_title()

    def set_time(self, time_str: str):
        """更新时间"""
        self.last_message_time = time_str
        if self.time_label:
            self.time_label.setText(self._format_time(time_str))

    def set_preview(self, preview: str):
        """更新预览文本（单行模式下不再显示，仅存储）"""
        self.preview = preview

    def set_msg_count(self, count: int):
        """更新消息数量（单行模式下不再显示 badge，仅存储）"""
        self.msg_count = count

    def hide_separator(self):
        """隐藏分割线（兼容旧代码，分割线已移除）"""
        pass

    def _on_delete_clicked(self):
        self.delete_clicked.emit()

    def enterEvent(self, event):
        if hasattr(self, "delete_btn"):
            self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if hasattr(self, "delete_btn"):
            self.delete_btn.hide()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        """重写右键菜单事件 - 由父级处理"""
        self.context_menu_requested.emit(event.pos())
        event.accept()


class ModelCardWidget(QWidget):
    """模型卡片组件"""
    model_selected = Signal(str)

    def __init__(self, model_name: str, is_selected: bool = False, parent=None):
        super().__init__(parent)
        self.model_name = model_name
        self.is_selected = is_selected
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        self.container = QWidget()
        self.container.setFixedSize(110, 70)

        if self.is_selected:
            self.container.setStyleSheet(get_style('model_card_selected'))
        else:
            self.container.setStyleSheet(get_style('model_card'))

        container_layout = QVBoxLayout(self.container)

        name_label = QLabel(self.model_name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("""
            color: white;
            font-size: 12px;
            font-weight: bold;
            background: transparent;
        """)

        container_layout.addWidget(name_label)
        self.container.mousePressEvent = self.on_click

        layout.addWidget(self.container)

    def on_click(self, event):
        self.is_selected = True
        self.update_style()
        self.model_selected.emit(self.model_name)

    def update_style(self):
        if self.is_selected:
            self.container.setStyleSheet(get_style('model_card_selected'))
        else:
            self.container.setStyleSheet(get_style('model_card'))


class ToastWidget(QWidget):
    """Toast提示组件"""
    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.message = message
        self.setup_ui()
        self.show_toast()

    def setup_ui(self):
        self.setFixedSize(280, 44)
        self.setStyleSheet(get_style('toast'))

        layout = QHBoxLayout(self)
        label = QLabel(self.message)
        label.setStyleSheet("color: white; font-size: 13px; background: transparent; font-weight: bold;")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

    def show_toast(self):
        self.show()
        QTimer.singleShot(2000, self.hide)


class ModernDropdown(QWidget):
    """DeepSeek 风格模型选择下拉框 — 根据主题切换深浅配色（无倍率）"""
    currentChanged = Signal(str)

    # 主题配色表（dark / light）
    THEME = {
        'dark': {
            'btn_bg': '#32333a',
            'btn_bg_hover': '#3a3b43',
            'btn_text': '#d9dae0',
            'arrow': '#9a9ca6',
            'popup_bg': '#32333a',
            'row_text': '#d9dae0',
            'row_sel_bg': 'rgba(110,127,224,0.16)',
            'row_hover_bg': '#3a3b43',
            'check': '#6e7fe0',
        },
        'light': {
            'btn_bg': '#ffffff',
            'btn_bg_hover': '#f0f1f3',
            'btn_text': '#1d1d1f',
            'arrow': '#9aa0aa',
            'popup_bg': '#ffffff',
            'row_text': '#1d1d1f',
            'row_sel_bg': 'rgba(110,127,224,0.10)',
            'row_hover_bg': '#f3f4f6',
            'check': '#6e7fe0',
        },
    }

    def __init__(self, items: list = None, parent=None, theme: str = 'dark'):
        super().__init__(parent)
        self._items = items or []
        self._current_index = 0
        self._popup = None
        self._theme = theme
        self._setup_ui()

    def _setup_ui(self):
        self.setFixedHeight(38)
        self.setMinimumWidth(160)
        self.setCursor(Qt.PointingHandCursor)

        self._btn = QPushButton(self)
        self._btn.clicked.connect(self._toggle_popup)

        self._arrow = QLabel("▾", self._btn)
        self._arrow.setAlignment(Qt.AlignCenter)

        self._apply_theme_style()
        self._update_text()

    def _apply_theme_style(self):
        """根据当前主题刷新按钮与箭头样式"""
        t = self.THEME.get(self._theme, self.THEME['dark'])
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: {t['btn_bg']};
                color: {t['btn_text']};
                border: none;
                border-radius: 19px;
                padding: 0 34px 0 14px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {t['btn_bg_hover']};
            }}
            QPushButton:pressed {{
                background: {t['btn_bg_hover']};
            }}
        """)
        self._arrow.setStyleSheet(f"""
            color: {t['arrow']};
            background: transparent;
            font-size: 11px;
        """)

    def apply_theme(self, theme: str):
        """主题切换时刷新"""
        self._theme = theme
        self._apply_theme_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._btn.setGeometry(0, 0, self.width(), self.height())
        self._arrow.setGeometry(self.width() - 30, 0, 24, self.height())

    def setItems(self, items: list):
        self._items = items
        self._update_text()

    def currentText(self) -> str:
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index]
        return ""

    def setCurrentIndex(self, index: int):
        if 0 <= index < len(self._items):
            self._current_index = index
            self._update_text()

    def findText(self, text: str) -> int:
        try:
            return self._items.index(text)
        except ValueError:
            return -1

    def _update_text(self):
        self._btn.setText(self.currentText())

    def _toggle_popup(self):
        if self._popup and self._popup.isVisible():
            self._popup.close()
            return
        self._show_popup()

    def _show_popup(self):
        if self._popup:
            self._popup.close()
            self._popup.deleteLater()

        t = self.THEME.get(self._theme, self.THEME['dark'])

        self._popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint)
        self._popup.setAttribute(Qt.WA_TranslucentBackground)

        container = QWidget(self._popup)
        container.setStyleSheet(f"""
            background: {t['popup_bg']};
            border: none;
            border-radius: 12px;
        """)
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 6)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        for i, item in enumerate(self._items):
            is_sel = (i == self._current_index)
            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            row.setFixedHeight(38)

            if is_sel:
                row.setStyleSheet(f"""
                    background: {t['row_sel_bg']};
                    border-radius: 8px;
                """)
            else:
                row.setStyleSheet("background: transparent; border-radius: 8px;")

            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 0, 12, 0)
            row_layout.setSpacing(10)

            name_label = QLabel(item)
            name_label.setStyleSheet(
                f"color: {t['row_text']}; font-size: 12px; font-weight: 500; background: transparent;")
            row_layout.addWidget(name_label, 1)

            if is_sel:
                check = QLabel("✓")
                check.setStyleSheet(
                    f"color: {t['check']}; font-size: 12px; font-weight: bold; background: transparent;")
                row_layout.addWidget(check)

            idx = i
            row.mousePressEvent = lambda e, _idx=idx: self._on_item_clicked(_idx)

            if not is_sel:
                def _enter(e, w=row, th=t):
                    w.setStyleSheet(f"background: {th['row_hover_bg']}; border-radius: 8px;")
                def _leave(e, w=row):
                    w.setStyleSheet("background: transparent; border-radius: 8px;")
                row.enterEvent = _enter
                row.leaveEvent = _leave

            layout.addWidget(row)

        # 尺寸
        popup_w = max(self.width() + 24, 220)
        popup_h = len(self._items) * 42 + 16
        container.setGeometry(0, 0, popup_w, popup_h)
        self._popup.setFixedSize(popup_w, popup_h)

        # 向上展开：定位到按钮上方
        btn_pos = self._btn.mapToGlobal(self._btn.rect().topLeft())
        self._popup.move(btn_pos.x() - 6, btn_pos.y() - popup_h - 6)

        self._popup.show()

    def _on_item_clicked(self, index: int):
        self._current_index = index
        self._update_text()
        if self._popup:
            self._popup.close()
        self.currentChanged.emit(self.currentText())


class ToolCallCard(QWidget):
    """工具调用卡片 — 带图标、工具名、状态指示器、可折叠详情"""

    TOOL_ICONS = {
        'write_file': '📝', 'edit_file': '✏️', 'read_file': '📖',
        'run_command': '🖥', 'execute_code': '🐍', 'web_search': '🔍',
        'list_files': '📂', 'delete_file': '🗑', 'create_file': '📝',
        'web_fetch': '🌐', 'rag_search': '📚',
    }

    # 主题配色表（现代极简：浅色浅边框 + 中性灰，深色沿用现有 Tokyonight 风）
    THEME = {
        'light': {
            'card_bg': '#ffffff', 'card_border': '#e6e8ec',
            'header_bg': '#f7f8fa', 'header_hover': '#eef0f3',
            'name_color': '#1d1d2b', 'detail_color': '#9aa0aa',
            'detail_bg': '#f3f4f6', 'text_color': '#525866',
            'accent': '#6366f1',
        },
        'dark': {
            'card_bg': '#2d2e34', 'card_border': '#3a3a3a',
            'header_bg': '#27282e', 'header_hover': '#2d2e34',
            'name_color': '#ececec', 'detail_color': '#a0a0a0',
            'detail_bg': '#161616', 'text_color': '#9a9ca6',
            'accent': '#6e7fe0',
        },
    }

    def __init__(self, tool_name: str, tool_input: str = "",
                 tool_output: str = "", ok: bool = True, parent=None):
        super().__init__(parent)
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._tool_output = tool_output
        self._ok = ok
        self._collapsed = True
        self._theme = 'dark'
        self._setup_ui()

    def apply_theme(self, theme: str):
        """主题切换时刷新卡片配色"""
        self._theme = theme if theme in self.THEME else 'dark'
        c = self.THEME[self._theme]
        try:
            self.setStyleSheet(f"""
                QWidget#tool_card {{
                    background: {c['card_bg']};
                    border: 1px solid {c['card_border']};
                    border-radius: 8px;
                }}
            """)
            if hasattr(self, '_header'):
                self._header.setStyleSheet(f"""
                    QWidget {{ background: {c['header_bg']}; border-radius: 8px; }}
                    QWidget:hover {{ background: {c['header_hover']}; }}
                """)
            if hasattr(self, '_name_label'):
                self._name_label.setStyleSheet(
                    f"color: {c['name_color']}; font-size: 12px; font-weight: bold; background: transparent;")
            if hasattr(self, '_detail_label'):
                self._detail_label.setStyleSheet(
                    f"color: {c['detail_color']}; font-size: 11px; background: transparent;")
            if hasattr(self, '_status_label'):
                ok_color = '#16a34a' if self._ok else '#dc2626'
                if not self._tool_output:
                    ok_color = '#d97706'
                self._status_label.setStyleSheet(
                    f"color: {ok_color}; font-size: 11px; background: transparent;")
            if hasattr(self, '_arrow'):
                self._arrow.setStyleSheet(
                    f"color: {c['detail_color']}; font-size: 10px; background: transparent;")
            if hasattr(self, '_detail'):
                self._detail.setStyleSheet(f"background: {c['detail_bg']};")
            # 详情文本重新着色
            for lbl in getattr(self, '_detail_labels', []):
                lbl.setStyleSheet(
                    f"color: {c['text_color']}; font-size: 11px; background: transparent; "
                    f"font-family: Consolas, monospace;")
        except Exception:
            pass

    def _get_icon(self) -> str:
        low = self._tool_name.lower()
        for key, icon in self.TOOL_ICONS.items():
            if key in low:
                return icon
        return '🔧'

    def _setup_ui(self):
        c = self.THEME[self._theme]
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("tool_card")
        self.setStyleSheet(f"""
            QWidget#tool_card {{
                background: {c['card_bg']};
                border: 1px solid {c['card_border']};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setFixedHeight(32)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet(f"""
            QWidget {{ background: {c['header_bg']}; border-radius: 8px; }}
            QWidget:hover {{ background: {c['header_hover']}; }}
        """)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(10, 0, 8, 0)
        h_layout.setSpacing(6)

        icon_label = QLabel(self._get_icon())
        icon_label.setStyleSheet("background: transparent; font-size: 13px;")

        self._name_label = QLabel(self._tool_name)
        self._name_label.setStyleSheet(
            f"color: {c['name_color']}; font-size: 12px; font-weight: bold; background: transparent;")

        # Extract file name from input
        detail_text = ""
        if self._tool_input:
            import re as _re
            fp_match = _re.search(r"['\"]?([^'\"\s,]+?\.\w+)['\"]?", self._tool_input[:200])
            if fp_match:
                detail_text = os.path.basename(fp_match.group(1))
        self._detail_label = QLabel(detail_text)
        self._detail_label.setStyleSheet(
            f"color: {c['detail_color']}; font-size: 11px; background: transparent;")

        # Status icon
        if self._tool_output:
            status_icon = "✅" if self._ok else "❌"
            status_color = "#16a34a" if self._ok else "#dc2626"
        else:
            status_icon = "⏳"
            status_color = "#d97706"
        self._status_label = QLabel(status_icon)
        self._status_label.setStyleSheet(
            f"color: {status_color}; font-size: 11px; background: transparent;")

        self._arrow = QLabel("▸")
        self._arrow.setStyleSheet(f"color: {c['detail_color']}; font-size: 10px; background: transparent;")

        h_layout.addWidget(icon_label)
        h_layout.addWidget(self._name_label)
        if detail_text:
            h_layout.addWidget(self._detail_label)
        h_layout.addStretch()
        h_layout.addWidget(self._status_label)
        h_layout.addWidget(self._arrow)

        self._header.mousePressEvent = lambda e: self._toggle()
        layout.addWidget(self._header)

        # ── Detail (collapsible) ──
        self._detail = QWidget()
        self._detail.setStyleSheet(f"background: {c['detail_bg']};")
        d_layout = QVBoxLayout(self._detail)
        d_layout.setContentsMargins(12, 6, 12, 8)
        d_layout.setSpacing(4)
        self._detail_labels = []

        if self._tool_input:
            in_label = QLabel(f'<b style="color:{c["accent"]};">输入</b>')
            in_label.setStyleSheet("background: transparent; font-size: 11px;")
            in_text = QLabel(self._tool_input[:400])
            in_text.setWordWrap(True)
            in_text.setStyleSheet(
                f"color: {c['text_color']}; font-size: 11px; background: transparent; "
                f"font-family: Consolas, monospace;")
            in_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            d_layout.addWidget(in_label)
            d_layout.addWidget(in_text)

        if self._tool_output:
            out_color = "#16a34a" if self._ok else "#dc2626"
            out_label = QLabel(f'<b style="color:{out_color};">结果</b>')
            out_label.setStyleSheet("background: transparent; font-size: 11px;")
            out_text = QLabel(self._tool_output[:600])
            out_text.setWordWrap(True)
            out_text.setStyleSheet(
                f"color: {c['text_color']}; font-size: 11px; background: transparent; "
                f"font-family: Consolas, monospace;")
            out_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            d_layout.addWidget(out_label)
            d_layout.addWidget(out_text)
            self._detail_labels.append(out_label)
            self._detail_labels.append(out_text)

        self._detail.hide()
        layout.addWidget(self._detail)

    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._detail.hide()
            self._arrow.setText("▸")
        else:
            self._detail.show()
            self._arrow.setText("▾")


class CollapsibleThinking(QWidget):
    """结构化 Agent 思考过程 — 时间线 + 工具卡片"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._step_count = 0
        self._tool_count = 0
        self._start_time = time.time()
        self._max_visible_items = 6     # 超过此数量后启用内部滚动
        self._max_thought_items = 80    # 最大 thought 条目数，超过后删除最旧的
        self._last_thought_label = None  # 上一个 thought 的 QLabel，用于流式追加
        self._theme = 'dark'
        self._thought_labels = []
        # 水平方向填满父容器整个宽度，垂直方向取最小以适应内容
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self._setup_ui()

    def _setup_ui(self):
        c = self._theme_colors()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFixedHeight(32)
        self._header.setStyleSheet(f"""
            QWidget {{ background: {c['header_bg']}; border-radius: 6px; }}
            QWidget:hover {{ background: {c['header_hover']}; }}
        """)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(10, 0, 10, 0)
        h_layout.setSpacing(6)

        self._arrow_label = QLabel("▾")
        self._arrow_label.setStyleSheet(
            f"color: {c['accent']}; font-size: 12px; background: transparent;")

        self._title_label = QLabel("🧠 思考过程")
        self._title_label.setStyleSheet(
            f"color: {c['accent']}; font-size: 12px; font-weight: bold; background: transparent;")

        self._summary_label = QLabel("0 步")
        self._summary_label.setStyleSheet(
            f"color: {c['summary']}; font-size: 11px; background: transparent;")

        self._status_label = QLabel("思考中...")
        self._status_label.setStyleSheet(
            f"color: {c['summary']}; font-size: 11px; background: transparent;")

        h_layout.addWidget(self._arrow_label)
        h_layout.addWidget(self._title_label)
        h_layout.addWidget(self._summary_label)
        h_layout.addStretch()
        h_layout.addWidget(self._status_label)

        self._header.mousePressEvent = lambda e: self._toggle()
        layout.addWidget(self._header)

        # ── Content area wrapped in QScrollArea（内部滚动，不撑大外层） ──
        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"""
            QWidget {{
                background: {c['content_bg']};
                border: 1px solid {c['content_border']};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(8, 6, 8, 6)
        self._content_layout.setSpacing(4)
        self._content_layout.addStretch()

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setWidget(self._content_widget)
        self._scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #45475a;
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self._scroll_area.setMaximumHeight(16777215)
        layout.addWidget(self._scroll_area)

    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._scroll_area.hide()
            self._arrow_label.setText("▸")
            # 释放高度约束：折叠后仅保留 header(32px)，不留空白
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self._scroll_area.setMaximumHeight(16777215)
            self._scroll_area.setMinimumHeight(0)
        else:
            self._scroll_area.show()
            self._arrow_label.setText("▾")
            # 展开后重新计算内容高度
            self._update_content_height()

    def collapse(self):
        if not self._collapsed:
            self._toggle()

    def expand(self):
        if self._collapsed:
            self._toggle()

    def _theme_colors(self) -> dict:
        """思考面板主题配色（现代极简：浅色浅灰背景，深色沿用 Tokyonight 风）"""
        if self._theme == 'light':
            return {
                'header_bg': '#f3f4f6', 'header_hover': '#e9ebef',
                'accent': '#6366f1', 'summary': '#8b5cf6',
                'content_bg': '#fafbfc', 'content_border': '#e6e8ec',
                'thought': '#525866',
            }
        return {
            'header_bg': '#2a283d', 'header_hover': '#322f48',
            'accent': '#6366f1', 'summary': '#a855f7',
            'content_bg': '#1e1e2e', 'content_border': 'rgba(255,255,255,0.08)',
            'thought': '#a6adc8',
        }

    def apply_theme(self, theme: str):
        """主题切换时刷新思考面板及其内部所有卡片/条目的配色"""
        self._theme = theme if theme in ('light', 'dark') else 'dark'
        c = self._theme_colors()
        try:
            self._header.setStyleSheet(f"""
                QWidget {{ background: {c['header_bg']}; border-radius: 6px; }}
                QWidget:hover {{ background: {c['header_hover']}; }}
            """)
            self._arrow_label.setStyleSheet(
                f"color: {c['accent']}; font-size: 12px; background: transparent;")
            self._title_label.setStyleSheet(
                f"color: {c['accent']}; font-size: 12px; font-weight: bold; background: transparent;")
            self._summary_label.setStyleSheet(
                f"color: {c['summary']}; font-size: 11px; background: transparent;")
            self._status_label.setStyleSheet(
                f"color: {c['summary']}; font-size: 11px; background: transparent;")
            self._content_widget.setStyleSheet(f"""
                QWidget {{
                    background: {c['content_bg']};
                    border: 1px solid {c['content_border']};
                    border-top: none;
                    border-radius: 0 0 6px 6px;
                }}
            """)
            for lbl in self._thought_labels:
                lbl.setStyleSheet(
                    f"color: {c['thought']}; font-size: 12px; background: transparent; "
                    f"font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")
            # 内部工具卡片逐一刷新
            for i in range(self._content_layout.count()):
                item = self._content_layout.itemAt(i)
                w = item.widget() if item else None
                if w and hasattr(w, 'apply_theme'):
                    w.apply_theme(theme)
        except Exception:
            pass

    def _insert_widget(self, widget):
        """在 stretch 之前插入组件"""
        self._content_layout.insertWidget(self._content_layout.count() - 1, widget)

    def _measure_widgets_height(self, max_count: int = -1):
        """手动测量 content_layout 中 widget 的实际高度（忽略 stretch spacer），
        返回 (total_height, widget_count)。max_count=-1 表示全部。"""
        total = 0
        count = 0
        for i in range(self._content_layout.count()):
            item = self._content_layout.itemAt(i)
            w = item.widget() if item else None
            if w:
                total += w.sizeHint().height()
                count += 1
                if max_count > 0 and count >= max_count:
                    break
        if count > 0:
            total += (count - 1) * self._content_layout.spacing()
        return max(total, 0), count

    def _update_content_height(self):
        """根据条目数量动态调整：≤N 条自动撑开，>N 条锁定 N 条高度并在内部滚动"""
        # 强制重新计算布局，确保 sizeHint 准确
        self._content_widget.updateGeometry()
        self._content_layout.activate()
        self._content_widget.adjustSize()

        too_many = self._step_count > self._max_visible_items

        if too_many:
            # 测量前 _max_visible_items 条的实际像素高度
            visible_h, _ = self._measure_widgets_height(self._max_visible_items)
            # 兜底：按每条约 44px 估算，确保最少显示 N 条的高度
            if visible_h < 60:
                visible_h = self._max_visible_items * 44
            target_h = max(visible_h + 10, 200)

            self._scroll_area.setFixedHeight(target_h)
            self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self._content_widget.setMinimumHeight(0)

            total_h = 32 + target_h + 12
            self.setMinimumHeight(total_h)
            self.setMaximumHeight(total_h)
        else:
            # ≤N 条：完全撑开，不做内部滚动
            # 使用手动测量（跳过 stretch），避免 sizeHint 受视口影响
            content_h, _ = self._measure_widgets_height()
            # 兜底：使至少有合理高度
            if content_h < 40:
                content_h = max(self._content_widget.sizeHint().height(), 80)

            self._scroll_area.setMinimumHeight(0)
            self._scroll_area.setMaximumHeight(16777215)
            self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._content_widget.setMinimumHeight(max(content_h, 1))
            self._content_widget.setMaximumHeight(16777215)

            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.adjustSize()
            self.updateGeometry()

    def add_thought(self, text: str):
        """添加一条思考文本（支持流式追加：短文本追加到上一条，长文本创建新条目）"""
        import html as html_module

        # 如果文本很短（< 80字符）且上一个 thought 存在，追加到上一条
        if self._last_thought_label and len(text) < 80:
            current = self._last_thought_label._raw_text
            combined = current + text
            if len(combined) < 500:  # 单条上限 500 字符
                self._last_thought_label._raw_text = combined
                self._last_thought_label.setText(html_module.escape(combined[:500]))
                return

        # 创建新的 thought 条目
        self._step_count += 1
        self._update_summary()

        c = self._theme_colors()
        item = QWidget()
        item.setStyleSheet("background: transparent;")
        il = QHBoxLayout(item)
        il.setContentsMargins(4, 2, 4, 2)
        il.setSpacing(6)

        icon = QLabel("💭")
        icon.setStyleSheet("background: transparent; font-size: 11px;")
        text_label = QLabel(html_module.escape(text[:300]))
        text_label.setWordWrap(True)
        text_label._raw_text = text  # 存储原始文本用于追加
        text_label.setStyleSheet(
            f"color: {c['thought']}; font-size: 12px; background: transparent; "
            f"font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")

        il.addWidget(icon)
        il.addWidget(text_label, 1)
        self._insert_widget(item)
        self._last_thought_label = text_label
        self._thought_labels.append(text_label)
        self._trim_old_items()
        self._update_content_height()

    def add_tool_call(self, tool_name: str, tool_input: str,
                      tool_output: str = "", ok: bool = True):
        """添加一个工具调用卡片"""
        # 工具调用后重置 last_thought_label，下一个 thought 创建新条目
        self._last_thought_label = None
        self._step_count += 1
        self._tool_count += 1
        self._update_summary()

        card = ToolCallCard(tool_name, tool_input, tool_output, ok)
        if hasattr(card, 'apply_theme'):
            card.apply_theme(self._theme)
        self._insert_widget(card)
        self._trim_old_items()
        self._update_content_height()

    def _trim_old_items(self):
        """删除最旧的非 stretch 条目，防止 widget 无限累积导致内存泄漏"""
        # 计算非 stretch 的 widget 数量
        widget_items = []
        for i in range(self._content_layout.count() - 1):  # -1 跳过末尾 stretch
            item = self._content_layout.itemAt(i)
            if item and item.widget():
                widget_items.append(i)
        # 超过上限时删除最旧的
        while len(widget_items) > self._max_thought_items:
            oldest_idx = widget_items.pop(0)
            item = self._content_layout.itemAt(oldest_idx)
            if item and item.widget():
                w = item.widget()
                self._content_layout.removeWidget(w)
                w.deleteLater()
            # 索引偏移修正
            widget_items = [i - 1 for i in widget_items]

    def _update_summary(self):
        parts = [f"{self._step_count} 步"]
        if self._tool_count > 0:
            parts.append(f"{self._tool_count} 工具")
        self._summary_label.setText(" · ".join(parts))

    def set_status(self, status: str):
        self._status_label.setText(status)

    def set_final(self):
        """完成状态"""
        elapsed = time.time() - self._start_time
        if elapsed >= 60:
            time_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"
        else:
            time_str = f"{elapsed:.1f}s"
        self._status_label.setText(f"✅ {time_str}")
        self._status_label.setStyleSheet(
            "color: #a6e3a1; font-size: 11px; background: transparent;")
        self.collapse()


class TaskProgressWidget(QWidget):
    """任务进度可视化组件 - 显示多步骤任务的执行进度"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps = []
        self._current_step = -1
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        self._header = QWidget()
        self._header.setFixedHeight(32)
        self._header.setStyleSheet("""
            QWidget {
                background: rgba(34, 197, 94, 0.08);
                border-radius: 6px;
            }
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 10, 0)

        self._icon_label = QLabel("📋")
        self._icon_label.setStyleSheet("background: transparent; font-size: 14px;")

        self._title_label = QLabel("任务进度")
        self._title_label.setStyleSheet(
            "color: #22c55e; font-size: 12px; font-weight: bold; background: transparent;")

        self._progress_label = QLabel("0/0")
        self._progress_label.setStyleSheet(
            "color: #16a34a; font-size: 11px; background: transparent;")

        header_layout.addWidget(self._icon_label)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._progress_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(34, 197, 94, 0.15);
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #22c55e, stop:1 #16a34a);
                border-radius: 2px;
            }
        """)

        # 步骤列表
        self._steps_container = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_container)
        self._steps_layout.setContentsMargins(12, 8, 12, 8)
        self._steps_layout.setSpacing(4)

        layout.addWidget(self._header)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._steps_container)

    def set_steps(self, steps: list):
        """设置任务步骤"""
        self._steps = steps
        self._current_step = -1
        self._progress_bar.setMaximum(len(steps))
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"0/{len(steps)}")

        # 清除旧的步骤显示
        while self._steps_layout.count():
            child = self._steps_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 创建步骤项
        for i, step in enumerate(steps):
            step_widget = QWidget()
            step_layout = QHBoxLayout(step_widget)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(8)

            status_icon = QLabel("○")
            status_icon.setFixedWidth(16)
            status_icon.setStyleSheet("color: #9ca3af; font-size: 12px; background: transparent;")

            step_text = QLabel(step)
            step_text.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")

            step_layout.addWidget(status_icon)
            step_layout.addWidget(step_text)
            step_layout.addStretch()

            self._steps_layout.addWidget(step_widget)

    def update_step(self, step_index: int, status: str = "running"):
        """更新步骤状态"""
        if 0 <= step_index < len(self._steps):
            self._current_step = step_index
            self._progress_bar.setValue(step_index + 1)
            self._progress_label.setText(f"{step_index + 1}/{len(self._steps)}")

            # 更新步骤图标
            step_widget = self._steps_layout.itemAt(step_index).widget()
            if step_widget:
                icon_label = step_widget.layout().itemAt(0).widget()
                if status == "running":
                    icon_label.setText("◉")
                    icon_label.setStyleSheet("color: #3b82f6; font-size: 12px; background: transparent;")
                elif status == "success":
                    icon_label.setText("✓")
                    icon_label.setStyleSheet("color: #22c55e; font-size: 12px; background: transparent;")
                elif status == "error":
                    icon_label.setText("✗")
                    icon_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")

    def set_finished(self, success: bool = True):
        """设置任务完成状态"""
        if success:
            self._icon_label.setText("✅")
            self._title_label.setText("任务完成")
        else:
            self._icon_label.setText("❌")
            self._title_label.setText("任务失败")


class TaskPlanWidget(QWidget):
    """多任务拆分计划展示组件 — 显示任务清单及逐条执行进度"""

    task_clicked = Signal(int)  # 点击任务项时发出任务索引

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks = []  # [{"text": str, "status": "pending|running|done|error"}]
        self._collapsed = False  # 折叠状态
        self._setup_ui()

    def _setup_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#task_plan {
                background: #1e1e2e;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
        """)
        self.setObjectName("task_plan")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏（可点击折叠） ──
        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFixedHeight(34)
        self._header.setStyleSheet("""
            QWidget {
                background: rgba(255,255,255,0.08);
                border-radius: 10px 10px 0 0;
            }
            QWidget:hover { background: #3a3849; }
        """)
        self._header.mousePressEvent = lambda e: self._toggle()
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        header_layout.setSpacing(8)

        self._arrow_label = QLabel("▾")
        self._arrow_label.setStyleSheet(
            "color: #a6e3a1; font-size: 12px; background: transparent;")

        self._icon_label = QLabel("☰")
        self._icon_label.setStyleSheet(
            "color: #a6e3a1; font-size: 14px; font-weight: bold; background: transparent;")

        self._title_label = QLabel("任务清单")
        self._title_label.setStyleSheet(
            "color: #a6e3a1; font-size: 13px; font-weight: bold; background: transparent;")

        self._progress_label = QLabel("0/0")
        self._progress_label.setStyleSheet(
            "color: #a6adc8; font-size: 11px; font-weight: 500; background: transparent;")
        self._progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        header_layout.addWidget(self._arrow_label)
        header_layout.addWidget(self._icon_label)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._progress_label)

        # ── 任务列表容器（可滚动） ──
        from PySide6.QtWidgets import QScrollArea
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical { background: #45475a; border-radius: 3px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: #585b70; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(6, 6, 6, 6)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._scroll_area.setWidget(self._list_container)

        layout.addWidget(self._header)
        layout.addWidget(self._scroll_area)

    def _toggle(self):
        """切换折叠/展开状态"""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._scroll_area.hide()
            self._arrow_label.setText("▸")
        else:
            self._scroll_area.show()
            self._arrow_label.setText("▾")

    def collapse(self):
        if not self._collapsed:
            self._toggle()

    def expand(self):
        if self._collapsed:
            self._toggle()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_tasks(self, tasks: list):
        """
        设置任务列表
        tasks: list[str] 或 list[dict] — 每个任务的描述文本或 {"text": str} 字典
        """
        self._tasks = []
        for t in tasks:
            if isinstance(t, dict):
                self._tasks.append({"text": t.get("text", ""), "status": "pending",
                                    "step_id": t.get("step_id", ""),
                                    "agent_name": t.get("agent_name", ""),
                                    "result": ""})
            else:
                self._tasks.append({"text": str(t), "status": "pending",
                                    "step_id": "", "agent_name": "", "result": ""})
        self._refresh_all()
        self._update_header()

    def set_tasks_extended(self, tasks: list):
        """
        设置任务列表（扩展格式，保留已有状态）
        tasks: list[dict] - {"text": str, "status": str, "step_id": str, "agent_name": str}
        """
        self._tasks = []
        for t in tasks:
            if isinstance(t, dict):
                self._tasks.append({
                    "text": t.get("text", ""),
                    "status": t.get("status", "pending"),
                    "step_id": t.get("step_id", ""),
                    "agent_name": t.get("agent_name", ""),
                    "result": t.get("result", ""),
                })
            else:
                self._tasks.append({"text": str(t), "status": "pending",
                                    "step_id": "", "agent_name": "", "result": ""})
        self._refresh_all()
        self._update_header()

    def update_task_status_extended(self, index: int, status: str, result: str = ""):
        """更新任务状态（扩展版，含结果摘要）"""
        if 0 <= index < len(self._tasks):
            self._tasks[index]["status"] = status
            if result:
                self._tasks[index]["result"] = result
            self._update_row_extended(index, status, result)
            self._update_header()

    def _update_row_extended(self, index: int, status: str, result: str = ""):
        """更新单行（扩展版，支持 tooltip 显示结果）"""
        for i in range(self._list_layout.count()):
            item = self._list_layout.itemAt(i)
            if not item or not item.widget():
                continue
            w = item.widget()
            if getattr(w, '_task_index', None) == index:
                icon = getattr(w, '_status_icon', None)
                text_lbl = getattr(w, '_text_label', None)
                if icon:
                    colors_map = {
                        "done": ("✓", "#a6e3a1"),
                        "running": ("◉", "#6e7fe0"),
                        "error": ("✗", "#f38ba8"),
                        "incomplete": ("◐", "#f9e2af"),
                        "pending": ("○", "#585b70"),
                    }
                    sym, clr = colors_map.get(status, ("○", "#585b70"))
                    icon.setText(sym)
                    icon.setStyleSheet(f"color: {clr}; font-size: 13px; font-weight: bold; background: transparent;")
                if text_lbl:
                    if status == "done":
                        text_lbl.setStyleSheet("""
                            QLabel {
                                color: #a6e3a1; font-size: 12.5px; background: transparent;
                            }
                        """)
                    elif status == "running":
                        text_lbl.setStyleSheet("""
                            QLabel {
                                color: #6e7fe0; font-size: 12.5px; font-weight: bold;
                                background: transparent;
                            }
                        """)
                    elif status == "error":
                        text_lbl.setStyleSheet("""
                            QLabel {
                                color: #f38ba8; font-size: 12.5px; background: transparent;
                            }
                        """)
                    elif status == "incomplete":
                        text_lbl.setStyleSheet("""
                            QLabel {
                                color: #f9e2af; font-size: 12.5px; background: transparent;
                            }
                        """)
                    else:
                        text_lbl.setStyleSheet("""
                            QLabel {
                                color: #d9dae0; font-size: 12.5px; background: transparent;
                            }
                        """)
                    if result:
                        text_lbl.setToolTip(f"结果: {result[:200]}")
                break

    def _refresh_all(self):
        """重建整个任务列表 UI"""
        while self._list_layout.count() > (1 if self._list_layout.itemAt(
                self._list_layout.count() - 1) and self._list_layout.itemAt(
                self._list_layout.count() - 1).spacerItem() else 0):
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 在 stretch 前插入所有任务行
        stretch_idx = None
        for i in range(self._list_layout.count()):
            if self._list_layout.itemAt(i).spacerItem():
                stretch_idx = i
                break

        for idx, task in enumerate(self._tasks):
            row = self._make_task_row(idx, task)
            if stretch_idx is not None:
                self._list_layout.insertWidget(stretch_idx, row)
                stretch_idx += 1
            else:
                self._list_layout.addWidget(row)

    def _make_task_row(self, index: int, task: dict) -> QWidget:
        """创建单行任务项"""
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet("background: transparent; border-radius: 5px;")
        row.setFixedHeight(32)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(8)

        status = task.get("status", "pending")
        text = task.get("text", "")

        # 状态图标
        status_icon = QLabel()
        status_icon.setFixedSize(16, 16)
        status_icon.setAlignment(Qt.AlignCenter)
        if status == "done":
            status_icon.setText("✓")
            color = "#a6e3a1"
        elif status == "running":
            status_icon.setText("◉")
            color = "#6e7fe0"
        elif status == "error":
            status_icon.setText("✗")
            color = "#f38ba8"
        elif status == "incomplete":
            status_icon.setText("◐")
            color = "#f9e2af"
        else:  # pending
            status_icon.setText("○")
            color = "#585b70"
        status_icon.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;")
        self._status_icon_ref = status_icon

        # 任务文字
        text_label = QLabel(text)
        if status == "done":
            text_label.setStyleSheet("""
                QLabel {
                    color: #a6e3a1; font-size: 12.5px; background: transparent;
                }
            """)
        elif status == "running":
            text_label.setStyleSheet("""
                QLabel {
                    color: #6e7fe0; font-size: 12.5px; font-weight: bold; background: transparent;
                }
            """)
        elif status == "error":
            text_label.setStyleSheet("""
                QLabel {
                    color: #f38ba8; font-size: 12.5px; background: transparent;
                }
            """)
        elif status == "incomplete":
            text_label.setStyleSheet("""
                QLabel {
                    color: #f9e2af; font-size: 12.5px; background: transparent;
                }
            """)
        else:
            text_label.setStyleSheet("""
                QLabel {
                    color: #d9dae0; font-size: 12.5px; background: transparent;
                }
            """)
        text_label.setWordWrap(False)

        row_layout.addWidget(status_icon)
        row_layout.addWidget(text_label, 1)

        # 存储引用以便后续更新
        row._task_index = index
        row._status_icon = status_icon
        row._text_label = text_label

        return row

    def update_task_status(self, index: int, status: str):
        """更新指定任务的状态"""
        if 0 <= index < len(self._tasks):
            self._tasks[index]["status"] = status
            self._update_row(index, status)
            self._update_header()

    def _update_row(self, index: int, status: str):
        """更新单行的图标和样式"""
        for i in range(self._list_layout.count()):
            item = self._list_layout.itemAt(i)
            if not item or not item.widget():
                continue
            w = item.widget()
            if getattr(w, '_task_index', None) == index:
                icon = getattr(w, '_status_icon', None)
                text_lbl = getattr(w, '_text_label', None)
                if icon:
                    colors_map = {
                        "done": ("✓", "#a6e3a1"),
                        "running": ("◉", "#6e7fe0"),
                        "error": ("✗", "#f38ba8"),
                        "incomplete": ("◐", "#f9e2af"),
                        "pending": ("○", "#585b70"),
                    }
                    sym, clr = colors_map.get(status, ("○", "#585b70"))
                    icon.setText(sym)
                    icon.setStyleSheet(f"color: {clr}; font-size: 13px; font-weight: bold; background: transparent;")
                if text_lbl and status == "running":
                    text_lbl.setStyleSheet("""
                        QLabel {
                            color: #6e7fe0; font-size: 12.5px; font-weight: bold;
                            background: transparent;
                        }
                    """)
                elif text_lbl and status == "incomplete":
                    text_lbl.setStyleSheet("""
                        QLabel {
                            color: #f9e2af; font-size: 12.5px; background: transparent;
                        }
                    """)
                elif text_lbl:
                    text_lbl.setStyleSheet("""
                        QLabel {
                            color: #d9dae0; font-size: 12.5px; background: transparent;
                        }
                    """)
                break

    def _update_header(self):
        """更新标题栏进度"""
        total = len(self._tasks)
        done = sum(1 for t in self._tasks if t["status"] in ("done",))
        running = sum(1 for t in self._tasks if t["status"] == "running")
        error = sum(1 for t in self._tasks if t["status"] == "error")
        incomplete = sum(1 for t in self._tasks if t["status"] == "incomplete")

        if total == 0:
            self._progress_label.setText("0/0")
        elif done + error == total:
            self._progress_label.setText(f"{done}/{total} 已完成")
            self._icon_label.setText("✅")
            self._title_label.setText("任务清单")
        elif running > 0:
            self._progress_label.setText(f"{done}/{total}")
            self._icon_label.setText("⏳")
            self._title_label.setText("执行中...")
        elif incomplete > 0:
            self._progress_label.setText(f"{done}/{total}")
            self._icon_label.setText("⚠")
            self._title_label.setText("部分未完成")
        else:
            self._progress_label.setText(f"0/{total}")

    def get_task_count(self) -> int:
        return len(self._tasks)

    def is_single_task(self) -> bool:
        return len(self._tasks) <= 1


class CodeFeedbackWidget(QWidget):
    """代码验证与执行反馈组件 — 在聊天框中展示语法检查 + 执行过程"""

    _SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []  # [{"type": str, "text": str, "status": str}]
        self._pending_fix_labels = []
        self._pending_file_edits = {}  # {file_path: QWidget}
        self._is_expanded = True
        self._spinner_timer = None
        self._spinner_frame = 0
        self._setup_ui()

    def _setup_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#code_feedback {
                background: #11111b;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
            }
        """)
        self.setObjectName("code_feedback")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏 ──
        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setFixedHeight(32)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.mousePressEvent = self._on_header_click
        self._header.setStyleSheet("""
            QWidget {
                background: #181825;
                border-radius: 8px 8px 0 0;
            }
        """)

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 10, 0)
        header_layout.setSpacing(6)

        self._status_dot = QLabel("●")
        self._status_dot.setFixedSize(14, 14)
        self._status_dot.setAlignment(Qt.AlignCenter)
        self._status_dot.setStyleSheet(
            "color: #6e7fe0; font-size: 8px; font-weight: bold; background: transparent;")

        self._title_label = QLabel("🐍 代码验证")
        self._title_label.setStyleSheet(
            "color: #d9dae0; font-size: 12px; font-weight: 600; background: transparent;")

        self._arrow_label = QLabel("▼")
        self._arrow_label.setFixedSize(16, 16)
        self._arrow_label.setAlignment(Qt.AlignCenter)
        self._arrow_label.setStyleSheet("color: #585b70; font-size: 10px; background: transparent;")

        header_layout.addWidget(self._status_dot)
        header_layout.addWidget(self._title_label, 1)
        header_layout.addWidget(self._arrow_label)

        # ── 内容区 ──
        self._content_widget = QWidget()
        self._content_widget.setAttribute(Qt.WA_StyledBackground, True)
        self._content_widget.setStyleSheet("""
            QWidget {
                background: #11111b;
                border-radius: 0 0 8px 8px;
            }
        """)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(10, 2, 10, 6)
        self._content_layout.setSpacing(3)

        layout.addWidget(self._header)
        layout.addWidget(self._content_widget)

    def _on_header_click(self, event):
        self._is_expanded = not self._is_expanded
        self._content_widget.setVisible(self._is_expanded)
        self._arrow_label.setText("▼" if self._is_expanded else "▶")
        parent = self.parent()
        if parent:
            parent.updateGeometry()

    # ── 文件编辑转圈动画 ──

    def add_file_editing(self, file_path: str, tool_name: str = ""):
        """添加「正在编辑文件」的转圈指示器"""
        # 规范化路径，统一分隔符
        norm_path = os.path.normpath(file_path).replace('\\', '/')
        if norm_path in self._pending_file_edits:
            return
        fname = os.path.basename(file_path)
        icon = "✏️" if "edit" in tool_name.lower() else "📝"
        text = f"正在{icon} {fname} …"

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 1, 0, 1)
        row_layout.setSpacing(6)

        self._spinner_icon_lbl = QLabel(self._SPINNER_FRAMES[0])
        self._spinner_icon_lbl.setFixedWidth(18)
        self._spinner_icon_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._spinner_icon_lbl.setStyleSheet(
            "color: #f9e2af; font-size: 12px; font-weight: bold; background: transparent;")

        text_lbl = QLabel(text)
        text_lbl.setStyleSheet(
            "color: #f9e2af; font-size: 11px; background: transparent;")
        text_lbl.setWordWrap(True)

        row_layout.addWidget(self._spinner_icon_lbl)
        row_layout.addWidget(text_lbl, 1)

        # 存储 spinner icon 引用（每个 pending 条目有自己的 spinner label）
        row._spinner_label = self._spinner_icon_lbl

        self._content_layout.addWidget(row)
        self._pending_file_edits[norm_path] = row
        self._start_spinner()
        self._title_label.setText("🐍 代码验证 · 编辑中")

    def _resolve_file_editing(self, file_path: str):
        """语法检查完成后移除转圈指示器"""
        norm_path = os.path.normpath(file_path).replace('\\', '/')
        row = self._pending_file_edits.pop(norm_path, None)
        if row:
            self._content_layout.removeWidget(row)
            row.deleteLater()
        if not self._pending_file_edits:
            self._stop_spinner()
            self._title_label.setText("🐍 代码验证")

    def resolve_all_pending(self):
        """清除所有待处理的文件编辑转圈（API 完成时的兜底清理）"""
        for norm_path, row in list(self._pending_file_edits.items()):
            self._content_layout.removeWidget(row)
            row.deleteLater()
        self._pending_file_edits.clear()
        self._stop_spinner()
        self._title_label.setText("🐍 代码验证")

    def resolve_file_editing(self, file_path: str):
        """公开方法：移除指定文件的转圈指示器"""
        self._resolve_file_editing(file_path)

    def _start_spinner(self):
        if self._spinner_timer is None:
            self._spinner_timer = QTimer(self)
            self._spinner_timer.timeout.connect(self._tick_spinner)
            self._spinner_timer.start(150)

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer.deleteLater()
            self._spinner_timer = None

    def _tick_spinner(self):
        self._spinner_frame = (self._spinner_frame + 1) % len(self._SPINNER_FRAMES)
        frame_char = self._SPINNER_FRAMES[self._spinner_frame]
        for row in list(self._pending_file_edits.values()):
            lbl = getattr(row, '_spinner_label', None)
            if lbl:
                lbl.setText(frame_char)

    def _make_entry_line(self, icon: str, text: str, color: str, bold: bool = False) -> QWidget:
        """创建单行反馈条目"""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 1, 0, 1)
        row_layout.setSpacing(6)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(18)
        icon_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        icon_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold; background: transparent;")

        text_lbl = QLabel(text)
        weight = "font-weight: 600;" if bold else ""
        text_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; {weight} background: transparent;")
        text_lbl.setWordWrap(True)

        row_layout.addWidget(icon_lbl)
        row_layout.addWidget(text_lbl, 1)
        return row

    def clear(self):
        """清除所有条目"""
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entries.clear()
        self._pending_file_edits.clear()
        self._stop_spinner()
        self._title_label.setText("🐍 代码验证")
        self._status_dot.setStyleSheet(
            "color: #6e7fe0; font-size: 8px; font-weight: bold; background: transparent;")

    def add_syntax_check(self, file_path: str, ok: bool, detail: str = ""):
        """添加语法检查结果"""
        # 移除该文件的转圈指示器
        self._resolve_file_editing(file_path)
        fname = os.path.basename(file_path)
        if ok:
            item = self._make_entry_line("✓", f"语法检查通过 — {fname}", "#a6e3a1")
            self._clear_pending_fix_labels("✓", f"修正完成 — {fname}", "#a6e3a1")
        else:
            item = self._make_entry_line("⚠", f"语法错误 — {fname}", "#f38ba8", bold=True)
            self._content_layout.addWidget(item)
            if detail:
                err_item = self._make_entry_line("", detail, "#f38ba8")
                self._content_layout.addWidget(err_item)
            # 添加修正提示
            fix_item = self._make_entry_line("🔄", "模型正在修正...", "#f9e2af")
            self._content_layout.addWidget(fix_item)
            self._pending_fix_labels.append(fix_item)
            self._title_label.setText("🐍 代码验证 · 有误")
            self._status_dot.setStyleSheet(
                "color: #f38ba8; font-size: 8px; font-weight: bold; background: transparent;")
            return
        self._content_layout.addWidget(item)
        self._update_summary()

    def add_execution_result(self, file_path: str, ok: bool, output: str = "", duration: float = 0):
        """添加执行结果"""
        fname = os.path.basename(file_path)
        # 分隔线
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.08);")
        self._content_layout.addWidget(sep)

        # 执行状态
        if ok:
            dur_str = f" ({duration:.1f}s)" if duration > 0 else ""
            header = self._make_entry_line("▶", f"执行完成 — {fname}{dur_str}", "#6e7fe0", bold=True)
            self._content_layout.addWidget(header)
            if output:
                # 终端风格输出框
                out_widget = QWidget()
                out_widget.setStyleSheet("""
                    background: #0a0a0f;
                    border: 1px solid #252530;
                    border-radius: 4px;
                """)
                out_layout = QVBoxLayout(out_widget)
                out_layout.setContentsMargins(8, 4, 8, 4)
                out_lbl = QLabel(output[:800])
                out_lbl.setStyleSheet(
                    "color: #a6e3a1; font-size: 11px; font-family: 'Consolas', 'Courier New', monospace; "
                    "background: transparent;")
                out_lbl.setWordWrap(True)
                out_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                out_layout.addWidget(out_lbl)
                self._content_layout.addWidget(out_widget)
            ok_item = self._make_entry_line("✅", "执行成功", "#a6e3a1")
            self._content_layout.addWidget(ok_item)
            self._title_label.setText("🐍 代码验证 · 通过")
            self._status_dot.setStyleSheet(
                "color: #a6e3a1; font-size: 8px; font-weight: bold; background: transparent;")
        else:
            header = self._make_entry_line("▶", f"执行失败 — {fname}", "#f38ba8", bold=True)
            self._content_layout.addWidget(header)
            if output:
                out_widget = QWidget()
                out_widget.setStyleSheet("""
                    background: #0a0a0f;
                    border: 1px solid #3a2025;
                    border-radius: 4px;
                """)
                out_layout = QVBoxLayout(out_widget)
                out_layout.setContentsMargins(8, 4, 8, 4)
                out_lbl = QLabel(output[:800])
                out_lbl.setStyleSheet(
                    "color: #f38ba8; font-size: 11px; font-family: 'Consolas', 'Courier New', monospace; "
                    "background: transparent;")
                out_lbl.setWordWrap(True)
                out_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                out_layout.addWidget(out_lbl)
                self._content_layout.addWidget(out_widget)
            err_item = self._make_entry_line("❌", "运行出错", "#f38ba8", bold=True)
            self._content_layout.addWidget(err_item)
            fix_item = self._make_entry_line("🔄", "模型正在分析错误并修正...", "#f9e2af")
            self._content_layout.addWidget(fix_item)
            self._pending_fix_labels.append(fix_item)
            self._title_label.setText("🐍 代码验证 · 失败")
            self._status_dot.setStyleSheet(
                "color: #f38ba8; font-size: 8px; font-weight: bold; background: transparent;")

    def _clear_pending_fix_labels(self, icon: str, text: str, color: str):
        """将「正在修正」提示更新为最终状态"""
        for widget in self._pending_fix_labels:
            layout = widget.layout()
            if layout and layout.count() >= 2:
                icon_lbl = layout.itemAt(0).widget()
                text_lbl = layout.itemAt(1).widget()
                if icon_lbl:
                    icon_lbl.setText(icon)
                    icon_lbl.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
                if text_lbl:
                    text_lbl.setText(text)
                    text_lbl.setStyleSheet(
                        f"color: {color}; font-size: 12px; background: transparent;")
        self._pending_fix_labels.clear()

    def set_final_status(self, all_ok: bool):
        """设置最终状态"""
        if all_ok:
            self._title_label.setText("🐍 代码验证 · 全部通过")
            self._status_dot.setStyleSheet(
                "color: #a6e3a1; font-size: 8px; font-weight: bold; background: transparent;")
        else:
            self._title_label.setText("🐍 代码验证 · 存在问题")
            self._status_dot.setStyleSheet(
                "color: #f9e2af; font-size: 8px; font-weight: bold; background: transparent;")
            if self._pending_fix_labels:
                self._clear_pending_fix_labels("⚠", "修正未完成（已达最大重试或模型未响应）", "#f9e2af")

    def _update_summary(self):
        """更新标题摘要"""
        pass


class ToolStatusWidget(QWidget):
    """工具调用实时状态显示组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tools = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        self._header = QWidget()
        self._header.setFixedHeight(32)
        self._header.setStyleSheet("""
            QWidget {
                background: rgba(59, 130, 246, 0.08);
                border-radius: 6px;
            }
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 10, 0)

        self._icon_label = QLabel("🔧")
        self._icon_label.setStyleSheet("background: transparent; font-size: 14px;")

        self._title_label = QLabel("工具调用")
        self._title_label.setStyleSheet(
            "color: #3b82f6; font-size: 12px; font-weight: bold; background: transparent;")

        self._count_label = QLabel("0 次调用")
        self._count_label.setStyleSheet(
            "color: #2563eb; font-size: 11px; background: transparent;")

        header_layout.addWidget(self._icon_label)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._count_label)

        # 工具列表
        self._tools_container = QWidget()
        self._tools_layout = QVBoxLayout(self._tools_container)
        self._tools_layout.setContentsMargins(12, 8, 12, 8)
        self._tools_layout.setSpacing(6)

        layout.addWidget(self._header)
        layout.addWidget(self._tools_container)

    def add_tool_call(self, tool_name: str, input_summary: str = ""):
        """添加工具调用记录"""
        self._tools[tool_name] = self._tools.get(tool_name, 0) + 1
        self._count_label.setText(f"{sum(self._tools.values())} 次调用")

        # 创建工具调用项
        tool_widget = QWidget()
        tool_layout = QHBoxLayout(tool_widget)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setSpacing(8)

        # 状态指示器
        status_dot = QLabel("●")
        status_dot.setFixedWidth(12)
        status_dot.setStyleSheet("color: #3b82f6; font-size: 10px; background: transparent;")

        # 工具名称
        name_label = QLabel(tool_name)
        name_label.setStyleSheet(
            "color: #1e40af; font-size: 12px; font-weight: bold; background: transparent;")

        # 输入摘要
        input_label = QLabel(input_summary[:50] + ("..." if len(input_summary) > 50 else ""))
        input_label.setStyleSheet(
            "color: #6b7280; font-size: 11px; background: transparent;")

        tool_layout.addWidget(status_dot)
        tool_layout.addWidget(name_label)
        tool_layout.addWidget(input_label)
        tool_layout.addStretch()

        self._tools_layout.addWidget(tool_widget)

    def update_last_status(self, success: bool):
        """更新最后一个工具调用的状态"""
        if self._tools_layout.count() > 0:
            last_widget = self._tools_layout.itemAt(self._tools_layout.count() - 1).widget()
            if last_widget:
                status_dot = last_widget.layout().itemAt(0).widget()
                if success:
                    status_dot.setStyleSheet("color: #22c55e; font-size: 10px; background: transparent;")
                else:
                    status_dot.setStyleSheet("color: #ef4444; font-size: 10px; background: transparent;")

    def clear(self):
        """清除所有记录"""
        self._tools.clear()
        self._count_label.setText("0 次调用")
        while self._tools_layout.count():
            child = self._tools_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()


class TerminalWidget(QWidget):
    """终端显示组件 - 显示命令执行过程，支持拖拽调整高度"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_h = 100
        self._max_h = 600
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_h = 0
        self.setup_ui()

    def setup_ui(self):
        self.setStyleSheet("background: #1e1e2e; border-radius: 8px;")
        self.setMinimumHeight(self._min_h)
        self.setMaximumHeight(self._max_h)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏（可拖拽）
        self._header = QWidget()
        self._header.setFixedHeight(32)
        self._header.setCursor(Qt.SizeVerCursor)
        self._header.setStyleSheet("background: #3a3b43; border-radius: 8px 8px 0 0;")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        icon_label = QLabel(">")
        icon_label.setStyleSheet(
            "color: #a6adc8; font-size: 14px; font-weight: bold; font-family: Consolas, monospace; background: transparent;")

        title_label = QLabel("终端")
        title_label.setStyleSheet(
            "color: #d9dae0; font-size: 12px; font-weight: bold; background: transparent;")

        # 隐藏按钮
        hide_btn = QPushButton("-")
        hide_btn.setFixedSize(24, 24)
        hide_btn.setCursor(Qt.PointingHandCursor)
        hide_btn.setStyleSheet("""
            QPushButton { color: #a6adc8; background: transparent; border: none; border-radius: 4px; font-size: 16px; font-weight: bold; }
            QPushButton:hover { background: rgba(166,173,200,0.2); }
        """)
        hide_btn.clicked.connect(self._hide_terminal)

        header_layout.addWidget(icon_label)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(hide_btn)

        # 终端内容
        self._content = QLabel()
        self._content.setWordWrap(True)
        self._content.setTextFormat(Qt.RichText)
        self._content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._content.setStyleSheet("""
            QLabel { background: #1e1e2e; color: #d9dae0; font-family: Consolas, "Courier New", monospace;
                     font-size: 12px; padding: 12px; border: none; line-height: 1.5; }
        """)
        self._content.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._content.setMinimumHeight(80)

        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._content)
        scroll.setStyleSheet("""
            QScrollArea { background: #1e1e2e; border: none; border-radius: 0 0 8px 8px; }
            QScrollBar:vertical { background: #1e1e2e; width: 8px; }
            QScrollBar::handle:vertical { background: #45475a; min-height: 20px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #585b70; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        layout.addWidget(self._header)
        layout.addWidget(scroll)

        self._content.setText('<span style="color: #6c7086;">等待命令执行...</span>')

    def _hide_terminal(self):
        """隐藏整个终端面板"""
        self.hide()
        # 通知主窗口更新按钮文字
        parent = self.parent()
        while parent:
            if hasattr(parent, 'show_terminal_btn'):
                parent.show_terminal_btn.setText("  显示终端")
                break
            parent = parent.parent()

    def mousePressEvent(self, event):
        """检测是否点击在标题栏区域（用于拖拽）"""
        if event.position().y() <= self._header.height() and event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_y = event.globalPosition().y()
            self._drag_start_h = self.height()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = self._drag_start_y - event.globalPosition().y()
            new_h = max(self._min_h, min(self._max_h, int(self._drag_start_h + delta)))
            self.setFixedHeight(new_h)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setMinimumHeight(self._min_h)
            self.setMaximumHeight(self._max_h)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def append_command(self, command: str):
        import html as html_module
        safe_cmd = html_module.escape(command)
        current = self._content.text()
        cmd_html = (f'<div style="margin: 4px 0;">'
                    f'<span style="color: #a6e3a1;">$</span> '
                    f'<span style="color: #f5e0dc;">{safe_cmd}</span></div>')
        if "等待命令执行..." in current:
            self._content.setText(cmd_html)
        else:
            self._content.setText(current + cmd_html)
        # 记录命令到终端管理器
        try:
            from services.tools.terminal_tools import get_terminal_manager
            get_terminal_manager().append_output("", command=command, exit_code=0)
        except Exception:
            pass

    def append_output(self, output: str):
        import html as html_module
        safe_output = html_module.escape(output)
        current = self._content.text()
        output_html = f'<div style="color: #bac2de; margin: 2px 0 2px 16px;">{safe_output}</div>'
        self._content.setText(current + output_html)
        # 记录输出到终端管理器
        try:
            from services.tools.terminal_tools import get_terminal_manager
            get_terminal_manager().append_output(output)
        except Exception:
            pass

    def append_error(self, error: str):
        import html as html_module
        safe_error = html_module.escape(error)
        current = self._content.text()
        error_html = f'<div style="color: #f38ba8; margin: 2px 0 2px 16px;">[错误] {safe_error}</div>'
        self._content.setText(current + error_html)
        # 记录错误到终端管理器
        try:
            from services.tools.terminal_tools import get_terminal_manager
            get_terminal_manager().append_output(error, exit_code=1)
        except Exception:
            pass

    def clear(self):
        self._content.setText('<span style="color: #6c7086;">等待命令执行...</span>')
        # 清空终端管理器
        try:
            from services.tools.terminal_tools import get_terminal_manager
            get_terminal_manager().clear()
        except Exception:
            pass


class BackgroundWidget(QWidget):
    """支持背景图片绘制的容器组件，用作 MainWindow 的 centralWidget"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg_pixmap = None
        self._bg_opacity = 0.3
        self._bg_color = QColor(240, 242, 245)  # 默认背景色 #f0f2f5

    def set_background(self, pixmap: QPixmap, opacity: float = 0.3):
        """设置背景图片和透明度"""
        self._bg_pixmap = pixmap
        self._bg_opacity = max(0.0, min(1.0, opacity))
        self.update()

    def clear_background(self):
        """清除背景图片"""
        self._bg_pixmap = None
        self.update()

    def set_bg_color(self, color: QColor):
        """动态设置底色，用于主题切换"""
        self._bg_color = color
        self.update()

    def paintEvent(self, event):
        """绘制背景图片 + 半透明底色叠加"""
        painter = QPainter(self)
        # 先填默认底色
        painter.fillRect(self.rect(), self._bg_color)

        if self._bg_pixmap and not self._bg_pixmap.isNull():
            # 绘制背景图（全尺寸覆盖）
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.setOpacity(self._bg_opacity)
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)
            # 叠加一层半透明底色（颜色跟随主题底色），让文字可读
            overlay_alpha = int((1 - self._bg_opacity) * 255 * 0.85)
            overlay_color = QColor(
                self._bg_color.red(),
                self._bg_color.green(),
                self._bg_color.blue(),
                overlay_alpha,
            )
            painter.fillRect(self.rect(), overlay_color)

        painter.end()


class ImageGeneratorWidget(QWidget):
    """图片生成面板组件（文生图 / 图生图）"""
    generate_clicked = Signal(str, object)  # prompt, image_path_or_None
    close_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ref_image_path = None
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget { background: #ffffff; border-radius: 14px; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

        # ── 标题栏 ──
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("AI 图片生成")
        title.setStyleSheet("color: #1a1a2e; font-size: 15px; font-weight: bold; background: transparent;")

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6b7280; background: transparent; border: none;
                          border-radius: 12px; font-size: 13px; }
            QPushButton:hover { color: #ef4444; background: rgba(239,68,68,0.1); }
        """)
        close_btn.clicked.connect(self.close_clicked.emit)

        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(close_btn)
        root.addWidget(header)

        # ── 模型选择 ──
        model_label = QLabel("出图模型")
        model_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(model_label)

        self.model_combo = QComboBox()
        self._image_models = []
        try:
            from services.image_service import list_image_models
            self._image_models = list_image_models()
        except Exception:
            self._image_models = [{
                "name": "Kolors 图片生成",
                "model_id": "Kwai-Kolors/Kolors",
                "use_browser": False,
            }]
        for m in self._image_models:
            self.model_combo.addItem(m["name"], m.get("model_id", ""))
        # 默认选中 ChatGPT Image 2（若存在）
        for i, m in enumerate(self._image_models):
            if m.get("use_browser") or "image2" in str(m.get("model_id", "")).lower():
                self.model_combo.setCurrentIndex(i)
                break
        self.model_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 9px;
                padding: 6px 36px 6px 12px; font-size: 13px; color: #1a1a2e;
            }
            QComboBox:hover { border-color: #a5b4fc; background: #fafafe; }
            QComboBox:focus { border-color: #6366f1; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 28px; border-top-right-radius: 9px; border-bottom-right-radius: 9px;
                border-left: 1px solid #f0f0f3;
            }
            QComboBox::down-arrow {
                image: none; width: 10px; height: 10px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 4px; outline: none; font-size: 13px; color: #1a1a2e;
                selection-background-color: rgba(99,102,241,0.12);
                selection-color: #1a1a2e;
            }
            QComboBox QAbstractItemView::item {
                padding: 7px 12px; border-radius: 6px; min-height: 20px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: rgba(99,102,241,0.08);
            }
            QComboBox QAbstractItemView::item:selected {
                background: rgba(99,102,241,0.12); color: #6e7fe0; font-weight: bold;
            }
        """)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        root.addWidget(self.model_combo)

        # ── 提示词输入 ──
        prompt_label = QLabel("提示词 (Prompt)")
        prompt_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(prompt_label)

        self.prompt_input = QPlainTextEdit()
        self.prompt_input.setPlaceholderText("描述你想要生成的图片，英文效果更好...\n例: a cute cat, illustration style")
        self.prompt_input.setMinimumHeight(70)
        self.prompt_input.setMaximumHeight(120)
        self.prompt_input.setStyleSheet("""
            QPlainTextEdit {
                background: #f8f9fb; border: 1.5px solid #e5e7eb; border-radius: 10px;
                padding: 10px 12px; font-size: 13px; color: #1a1a2e;
                selection-background-color: rgba(99, 102, 241, 0.3);
            }
            QPlainTextEdit:focus { border-color: #6366f1; }
        """)
        root.addWidget(self.prompt_input)

        # ── 参考图片（图生图） ──
        ref_label = QLabel("参考图片（可选，留空则文生图）")
        ref_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(ref_label)

        ref_row = QWidget()
        ref_row.setStyleSheet("background: transparent;")
        ref_layout = QHBoxLayout(ref_row)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.setSpacing(8)

        self.ref_preview = QLabel("未选择图片")
        self.ref_preview.setFixedSize(80, 80)
        self.ref_preview.setAlignment(Qt.AlignCenter)
        self.ref_preview.setStyleSheet("""
            QLabel { background: #f8f9fb; border: 1.5px dashed #d1d5db; border-radius: 10px;
                     color: #9ca3af; font-size: 11px; }
        """)

        ref_btns = QWidget()
        ref_btns.setStyleSheet("background: transparent;")
        ref_btns_layout = QVBoxLayout(ref_btns)
        ref_btns_layout.setContentsMargins(0, 0, 0, 0)
        ref_btns_layout.setSpacing(6)

        add_ref_btn = QPushButton("选择参考图")
        add_ref_btn.setFixedHeight(30)
        add_ref_btn.setCursor(Qt.PointingHandCursor)
        add_ref_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #e5e7eb;
                          border-radius: 8px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        add_ref_btn.clicked.connect(self._select_ref_image)

        clear_ref_btn = QPushButton("清除")
        clear_ref_btn.setFixedHeight(30)
        clear_ref_btn.setCursor(Qt.PointingHandCursor)
        clear_ref_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #6b7280; border: 1px solid #e5e7eb;
                          border-radius: 8px; font-size: 12px; }
            QPushButton:hover { color: #ef4444; border-color: #fca5a5; }
        """)
        clear_ref_btn.clicked.connect(self._clear_ref_image)

        ref_btns_layout.addWidget(add_ref_btn)
        ref_btns_layout.addWidget(clear_ref_btn)
        ref_btns_layout.addStretch()

        ref_layout.addWidget(self.ref_preview)
        ref_layout.addWidget(ref_btns)
        ref_layout.addStretch()
        root.addWidget(ref_row)

        # ── 参数设置 ──
        params_label = QLabel("参数设置")
        params_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(params_label)

        # 尺寸选择
        size_row = QWidget()
        size_row.setStyleSheet("background: transparent;")
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(8)

        size_label = QLabel("尺寸:")
        size_label.setStyleSheet("color: #1a1a2e; font-size: 12px; background: transparent;")
        self.size_combo = QComboBox()
        self.size_combo.addItems([
            "1024x1024 正方形",
            "2048x2048 最大正方形",
            "2048x1024 横版 2:1",
            "1024x2048 竖版 1:2",
            "1920x1080 16:9 宽屏",
            "1080x1920 9:16 竖屏",
        ])
        self.size_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 8px;
                padding: 5px 32px 5px 10px; font-size: 12px; color: #1a1a2e;
            }
            QComboBox:hover { border-color: #a5b4fc; background: #fafafe; }
            QComboBox:focus { border-color: #6366f1; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 24px; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
                border-left: 1px solid #f0f0f3;
            }
            QComboBox::down-arrow { image: none; width: 8px; height: 8px; }
            QComboBox QAbstractItemView {
                background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 4px; outline: none; font-size: 12px; color: #1a1a2e;
                selection-background-color: rgba(99,102,241,0.12);
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 10px; border-radius: 6px; min-height: 18px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: rgba(99,102,241,0.08);
            }
            QComboBox QAbstractItemView::item:selected {
                background: rgba(99,102,241,0.12); color: #6e7fe0; font-weight: bold;
            }
        """)

        steps_label = QLabel("步数:")
        steps_label.setStyleSheet("color: #1a1a2e; font-size: 12px; background: transparent;")
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 50)
        self.steps_spin.setValue(20)
        self.steps_spin.setStyleSheet("""
            QSpinBox { background: #f8f9fb; border: 1px solid #e5e7eb; border-radius: 6px;
                       padding: 4px 8px; font-size: 12px; color: #1a1a2e; }
            QSpinBox:hover { border-color: #a5b4fc; }
        """)

        seed_label = QLabel("种子:")
        seed_label.setStyleSheet("color: #1a1a2e; font-size: 12px; background: transparent;")
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(-1, 999999999)
        self.seed_spin.setValue(-1)
        self.seed_spin.setSpecialValueText("随机")
        self.seed_spin.setStyleSheet("""
            QSpinBox { background: #f8f9fb; border: 1px solid #e5e7eb; border-radius: 6px;
                       padding: 4px 8px; font-size: 12px; color: #1a1a2e; }
            QSpinBox:hover { border-color: #a5b4fc; }
        """)

        size_layout.addWidget(size_label)
        size_layout.addWidget(self.size_combo)
        size_layout.addWidget(steps_label)
        size_layout.addWidget(self.steps_spin)
        size_layout.addWidget(seed_label)
        size_layout.addWidget(self.seed_spin)
        root.addWidget(size_row)

        # ── 生成按钮 ──
        self.generate_btn = QPushButton("生成图片")
        self.generate_btn.setFixedHeight(40)
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #8b5cf6);
                color: white; border-radius: 10px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6e7fe0, stop:1 #7c3aed);
            }
            QPushButton:disabled {
                background: #d1d5db; color: #9ca3af;
            }
        """)
        self.generate_btn.clicked.connect(self._on_generate)
        root.addWidget(self.generate_btn)

        # ── 状态 / 结果 ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_label)

        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setMinimumHeight(100)
        self.result_label.setStyleSheet("""
            QLabel { background: #f8f9fb; border-radius: 12px; color: #9ca3af; font-size: 13px; }
        """)
        self.result_label.setText("生成的图片将显示在这里")
        self.result_label.setScaledContents(False)
        root.addWidget(self.result_label, 1)

        # 保存按钮（默认隐藏）
        self.save_btn = QPushButton("保存图片")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #e5e7eb;
                          border-radius: 8px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        self.save_btn.clicked.connect(self._save_image)
        self.save_btn.hide()
        root.addWidget(self.save_btn)

        self._result_pixmap = None
        self._result_url = None
        self._on_model_changed()

    def _on_model_changed(self):
        """ChatGPT 浏览器出图时隐藏 Kolors 专用参数"""
        use_browser = self._is_browser_model_selected()
        self.steps_spin.setEnabled(not use_browser)
        self.seed_spin.setEnabled(not use_browser)
        self.size_combo.setEnabled(not use_browser)
        if use_browser:
            self.prompt_input.setPlaceholderText(
                "描述要生成的图片（中英文均可）...\n例: 一只在雪地里的橘猫，插画风格"
            )
        else:
            self.prompt_input.setPlaceholderText(
                "描述你想要生成的图片，英文效果更好...\n例: a cute cat, illustration style"
            )

    def _is_browser_model_selected(self) -> bool:
        idx = self.model_combo.currentIndex()
        if idx < 0 or idx >= len(self._image_models):
            return False
        return bool(self._image_models[idx].get("use_browser"))

    def get_selected_model_id(self) -> str:
        return self.model_combo.currentData() or "Kwai-Kolors/Kolors"

    def _select_ref_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图片", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._ref_image_path = path
            pix = QPixmap(path).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.ref_preview.setPixmap(pix)
            self.ref_preview.setStyleSheet(
                "QLabel { border: 1.5px solid #6366f1; border-radius: 10px; background: #f8f9fb; }")

    def _clear_ref_image(self):
        self._ref_image_path = None
        self.ref_preview.clear()
        self.ref_preview.setText("未选择图片")
        self.ref_preview.setStyleSheet("""
            QLabel { background: #f8f9fb; border: 1.5px dashed #d1d5db; border-radius: 10px;
                     color: #9ca3af; font-size: 11px; }
        """)

    def _on_generate(self):
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            self.status_label.setText("请输入提示词")
            self.status_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")
            return
        self.generate_clicked.emit(prompt, self._ref_image_path)

    def set_generating(self, generating: bool):
        """设置生成中状态"""
        self.generate_btn.setEnabled(not generating)
        if generating:
            self.generate_btn.setText("生成中...")
            self.status_label.setText("正在生成图片，请稍候...")
            self.status_label.setStyleSheet("color: #6366f1; font-size: 12px; background: transparent;")
        else:
            self.generate_btn.setText("生成图片")

    def set_result(self, image_url: str):
        """显示生成结果（支持 http URL 或本地文件路径）"""
        self._result_url = image_url
        self.status_label.setText("生成完成！")
        self.status_label.setStyleSheet("color: #22c55e; font-size: 12px; background: transparent;")

        if image_url and os.path.isfile(image_url):
            pix = QPixmap(image_url)
            if not pix.isNull():
                self._on_image_loaded(pix)
                return
            self.set_error(f"无法加载本地图片: {image_url}")
            return

        self.result_label.setText("正在加载图片...")
        from PySide6.QtCore import QThread
        self._download_thread = QThread()
        import requests as _req
        class _Downloader(QObject):
            finished = Signal(QPixmap)
            error = Signal(str)
            def __init__(self, url):
                super().__init__()
                self.url = url
            def run(self):
                try:
                    resp = _req.get(self.url, timeout=60)
                    resp.raise_for_status()
                    pix = QPixmap()
                    pix.loadFromData(resp.content)
                    self.finished.emit(pix)
                except Exception as e:
                    self.error.emit(str(e))
        self._downloader = _Downloader(image_url)
        self._downloader.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._downloader.run)
        self._downloader.finished.connect(self._on_image_loaded)
        self._downloader.error.connect(lambda msg: self.result_label.setText(f"加载失败: {msg}"))
        self._downloader.finished.connect(self._download_thread.quit)
        self._download_thread.start()

    def append_status_log(self, text: str):
        """浏览器出图时的进度日志"""
        if not text:
            return
        prev = self.status_label.text() or ""
        if prev.startswith("正在生成") or prev.startswith("ChatGPT") or "Chrome" in prev:
            self.status_label.setText(text.strip())
        else:
            self.status_label.setText((prev + "\n" + text).strip()[-200:])
        self.status_label.setStyleSheet("color: #6366f1; font-size: 12px; background: transparent;")

    def _on_image_loaded(self, pix: QPixmap):
        self._result_pixmap = pix
        scaled = pix.scaled(self.result_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.result_label.setPixmap(scaled)
        self.save_btn.show()

    def set_error(self, msg: str):
        self.status_label.setText(f"生成失败: {msg}")
        self.status_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")

    def _save_image(self):
        if not self._result_pixmap:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", "generated_image.png",
            "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self._result_pixmap.save(path)
            self.status_label.setText(f"已保存: {path}")
            self.status_label.setStyleSheet("color: #22c55e; font-size: 12px; background: transparent;")


class VideoGeneratorWidget(QWidget):
    """视频生成面板组件（支持文生视频和图生视频）"""
    generate_clicked = Signal(str, int, int, int, int, str)  # prompt, height, width, num_frames, frame_rate, image_path
    close_clicked = Signal()

    DURATION_PRESETS = {
        "约3秒":  (81,  24, "768x1280"),   # num_frames, frame_rate, 推荐分辨率
        "约5秒":  (121, 24, "1152x768"),
        "约10秒": (241, 24, "1152x768"),
        "约18秒": (441, 24, "1152x768"),
    }

    # 文生视频默认分辨率 preset
    _TEXT_RESOLUTIONS = [
        "1152x768 横屏 3:2",
        "1280x768 横屏 5:3",
        "768x1280 竖屏 3:5",
        "1024x1024 正方形",
    ]
    # 图生视频默认分辨率（通常跟随原图比例，这里也给出常见选项）
    _IMAGE_RESOLUTIONS = [
        "768x768 正方形",
        "768x1152 竖屏 2:3",
        "1152x768 横屏 3:2",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result_url = None
        self._image_path = ""       # 图生视频参考图路径
        self._is_image_mode = False
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget { background: #ffffff; border-radius: 14px; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

        # ── 标题栏 ──
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("🎬 视频生成")
        title.setStyleSheet("color: #1a1a2e; font-size: 15px; font-weight: bold; background: transparent;")

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6b7280; background: transparent; border: none;
                          border-radius: 12px; font-size: 13px; }
            QPushButton:hover { color: #ef4444; background: rgba(239,68,68,0.1); }
        """)
        close_btn.clicked.connect(self.close_clicked.emit)

        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(close_btn)
        root.addWidget(header)

        # ── 模式切换 ──
        mode_row = QWidget()
        mode_row.setStyleSheet("background: transparent;")
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)

        self._mode_group = QButtonGroup(self)
        self._text_mode_btn = QPushButton("📝 文生视频")
        self._image_mode_btn = QPushButton("🖼 图生视频")
        for btn in (self._text_mode_btn, self._image_mode_btn):
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            btn.setCursor(Qt.PointingHandCursor)
            self._mode_group.addButton(btn)

        self._text_mode_btn.setChecked(True)
        self._apply_mode_btn_style()

        self._mode_group.buttonClicked.connect(self._on_mode_changed)
        mode_layout.addWidget(self._text_mode_btn)
        mode_layout.addWidget(self._image_mode_btn)
        mode_layout.addStretch()
        root.addWidget(mode_row)

        # ── 模型选择 ──
        model_label = QLabel("视频模型")
        model_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(model_label)

        self.model_combo = QComboBox()
        self._video_models = []
        try:
            from services.video_service import list_video_models
            self._video_models = list_video_models()
        except Exception:
            self._video_models = [{
                "name": "Agnes Video v2.0",
                "model_id": "agnes-video-v2.0",
            }]
        for m in self._video_models:
            self.model_combo.addItem(m["name"], m.get("model_id", ""))
        self.model_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 9px;
                padding: 6px 36px 6px 12px; font-size: 13px; color: #1a1a2e;
            }
            QComboBox:hover { border-color: #a5b4fc; background: #fafafe; }
            QComboBox:focus { border-color: #6366f1; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 28px; border-top-right-radius: 9px; border-bottom-right-radius: 9px;
                border-left: 1px solid #f0f0f3;
            }
            QComboBox::down-arrow {
                image: none; width: 10px; height: 10px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 4px; outline: none; font-size: 13px; color: #1a1a2e;
                selection-background-color: rgba(99,102,241,0.12);
                selection-color: #1a1a2e;
            }
            QComboBox QAbstractItemView::item {
                padding: 7px 12px; border-radius: 6px; min-height: 20px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: rgba(99,102,241,0.08);
            }
            QComboBox QAbstractItemView::item:selected {
                background: rgba(99,102,241,0.12); color: #6e7fe0; font-weight: bold;
            }
        """)
        root.addWidget(self.model_combo)

        # ── 提示词输入 ──
        prompt_label = QLabel("提示词 (Prompt)")
        prompt_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(prompt_label)

        self.prompt_input = QPlainTextEdit()
        self.prompt_input.setPlaceholderText(
            "描述你想生成的视频，英文效果更好...\n"
            "例: A cinematic shot of a cat walking on the beach at sunset, "
            "soft ocean waves, warm golden lighting, realistic motion"
        )
        self.prompt_input.setMinimumHeight(70)
        self.prompt_input.setMaximumHeight(120)
        self.prompt_input.setStyleSheet("""
            QPlainTextEdit {
                background: #f8f9fb; border: 1.5px solid #e5e7eb; border-radius: 10px;
                padding: 10px 12px; font-size: 13px; color: #1a1a2e;
                selection-background-color: rgba(99, 102, 241, 0.3);
            }
            QPlainTextEdit:focus { border-color: #6366f1; }
        """)
        root.addWidget(self.prompt_input)

        # ── 图生视频：图片上传区域 ──
        self._image_area = QWidget()
        self._image_area.setStyleSheet("background: transparent;")
        image_area_layout = QVBoxLayout(self._image_area)
        image_area_layout.setContentsMargins(0, 0, 0, 0)
        image_area_layout.setSpacing(6)

        img_label = QLabel("参考图片")
        img_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        image_area_layout.addWidget(img_label)

        # 拖放 / 点击选择区域
        self._img_drop_widget = QLabel()
        self._img_drop_widget.setMinimumHeight(80)
        self._img_drop_widget.setMaximumHeight(100)
        self._img_drop_widget.setAlignment(Qt.AlignCenter)
        self._img_drop_widget.setText("🖼 点击或拖放图片到此处")
        self._img_drop_widget.setCursor(Qt.PointingHandCursor)
        self._img_drop_widget.setStyleSheet("""
            QLabel {
                background: #f8f9fb; border: 2px dashed #d1d5db; border-radius: 10px;
                color: #9ca3af; font-size: 13px;
            }
            QLabel:hover { border-color: #a5b4fc; color: #6366f1; }
        """)
        self._img_drop_widget.setAcceptDrops(True)
        self._img_drop_widget.mousePressEvent = self._on_img_drop_clicked
        self._img_drop_widget.dragEnterEvent = self._on_img_drag_enter
        self._img_drop_widget.dropEvent = self._on_img_drop

        # 预览缩略图（上传后显示）
        self._img_preview = QLabel()
        self._img_preview.setFixedSize(80, 80)
        self._img_preview.setAlignment(Qt.AlignCenter)
        self._img_preview.setStyleSheet("""
            QLabel { background: #f0f0f3; border-radius: 8px; border: 1px solid #e5e7eb; }
        """)
        self._img_preview.hide()

        # 图片路径提示
        self._img_path_label = QLabel("")
        self._img_path_label.setStyleSheet(
            "color: #6366f1; font-size: 11px; background: transparent;"
        )
        self._img_path_label.setWordWrap(True)
        self._img_path_label.hide()

        # 清除图片按钮
        self._img_clear_btn = QPushButton("✕ 清除")
        self._img_clear_btn.setFixedSize(56, 22)
        self._img_clear_btn.setCursor(Qt.PointingHandCursor)
        self._img_clear_btn.setStyleSheet("""
            QPushButton { background: #fee2e2; color: #ef4444; border: none;
                          border-radius: 6px; font-size: 11px; }
            QPushButton:hover { background: #fecaca; }
        """)
        self._img_clear_btn.clicked.connect(self._clear_image)
        self._img_clear_btn.hide()

        img_row = QWidget()
        img_row.setStyleSheet("background: transparent;")
        img_row_layout = QHBoxLayout(img_row)
        img_row_layout.setContentsMargins(0, 0, 0, 0)
        img_row_layout.setSpacing(8)
        img_row_layout.addWidget(self._img_preview)
        img_row_layout.addWidget(self._img_path_label, 1)
        img_row_layout.addWidget(self._img_clear_btn)

        image_area_layout.addWidget(self._img_drop_widget)
        image_area_layout.addWidget(img_row)
        self._image_area.hide()  # 默认文生视频，隐藏
        root.addWidget(self._image_area)

        # ── 参数设置 ──
        params_label = QLabel("参数设置")
        params_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        root.addWidget(params_label)

        # 时长预设
        duration_row = QWidget()
        duration_row.setStyleSheet("background: transparent;")
        dur_layout = QHBoxLayout(duration_row)
        dur_layout.setContentsMargins(0, 0, 0, 0)
        dur_layout.setSpacing(8)

        dur_label = QLabel("时长:")
        dur_label.setStyleSheet("color: #1a1a2e; font-size: 12px; background: transparent;")
        self.duration_combo = QComboBox()
        self.duration_combo.addItems(list(self.DURATION_PRESETS.keys()))
        self.duration_combo.setCurrentIndex(1)  # 默认 约5秒
        self.duration_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 8px;
                padding: 5px 32px 5px 10px; font-size: 12px; color: #1a1a2e;
            }
            QComboBox:hover { border-color: #a5b4fc; background: #fafafe; }
            QComboBox:focus { border-color: #6366f1; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 24px; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
                border-left: 1px solid #f0f0f3;
            }
            QComboBox::down-arrow { image: none; width: 8px; height: 8px; }
            QComboBox QAbstractItemView {
                background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 4px; outline: none; font-size: 12px; color: #1a1a2e;
                selection-background-color: rgba(99,102,241,0.12);
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 10px; border-radius: 6px; min-height: 18px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: rgba(99,102,241,0.08);
            }
            QComboBox QAbstractItemView::item:selected {
                background: rgba(99,102,241,0.12); color: #6e7fe0; font-weight: bold;
            }
        """)
        dur_layout.addWidget(dur_label)
        dur_layout.addWidget(self.duration_combo)
        dur_layout.addStretch()
        root.addWidget(duration_row)

        # 分辨率选择
        res_row = QWidget()
        res_row.setStyleSheet("background: transparent;")
        res_layout = QHBoxLayout(res_row)
        res_layout.setContentsMargins(0, 0, 0, 0)
        res_layout.setSpacing(8)

        res_label = QLabel("分辨率:")
        res_label.setStyleSheet("color: #1a1a2e; font-size: 12px; background: transparent;")
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(self._TEXT_RESOLUTIONS)
        self.resolution_combo.setCurrentIndex(0)
        self.resolution_combo.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 8px;
                padding: 5px 32px 5px 10px; font-size: 12px; color: #1a1a2e;
            }
            QComboBox:hover { border-color: #a5b4fc; background: #fafafe; }
            QComboBox:focus { border-color: #6366f1; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 24px; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
                border-left: 1px solid #f0f0f3;
            }
            QComboBox::down-arrow { image: none; width: 8px; height: 8px; }
            QComboBox QAbstractItemView {
                background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 4px; outline: none; font-size: 12px; color: #1a1a2e;
                selection-background-color: rgba(99,102,241,0.12);
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 10px; border-radius: 6px; min-height: 18px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: rgba(99,102,241,0.08);
            }
            QComboBox QAbstractItemView::item:selected {
                background: rgba(99,102,241,0.12); color: #6e7fe0; font-weight: bold;
            }
        """)
        # 时长变化时联动默认分辨率
        self.duration_combo.currentIndexChanged.connect(self._on_duration_changed)

        res_layout.addWidget(res_label)
        res_layout.addWidget(self.resolution_combo)
        res_layout.addStretch()
        root.addWidget(res_row)

        # ── 生成按钮 ──
        self.generate_btn = QPushButton("生成视频")
        self.generate_btn.setFixedHeight(40)
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f97316, stop:1 #ec4899);
                color: white; border-radius: 10px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ea580c, stop:1 #db2777);
            }
            QPushButton:disabled {
                background: #d1d5db; color: #9ca3af;
            }
        """)
        self.generate_btn.clicked.connect(self._on_generate)
        root.addWidget(self.generate_btn)

        # ── 状态 / 进度 ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        # ── 结果区域 ──
        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setMinimumHeight(60)
        self.result_label.setStyleSheet("""
            QLabel { background: #f8f9fb; border-radius: 12px; color: #9ca3af; font-size: 13px; }
        """)
        self.result_label.setText("生成的视频链接将显示在这里")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

        # 打开/下载按钮（默认隐藏）
        self.action_btn = QPushButton("打开视频")
        self.action_btn.setFixedHeight(32)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #e5e7eb;
                          border-radius: 8px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        self.action_btn.clicked.connect(self._open_video)
        self.action_btn.hide()
        root.addWidget(self.action_btn)

        root.addStretch()

    # ── 模式切换 ──
    def _apply_mode_btn_style(self):
        """刷新两个模式按钮的选中/非选中样式"""
        for btn in (self._text_mode_btn, self._image_mode_btn):
            if btn.isChecked():
                btn.setStyleSheet("""
                    QPushButton {
                        background: #6366f1; color: white; border: none;
                        border-radius: 8px; font-size: 12px; font-weight: bold;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #f3f4f6; color: #6b7280; border: none;
                        border-radius: 8px; font-size: 12px;
                    }
                    QPushButton:hover { background: #e5e7eb; }
                """)

    def _on_mode_changed(self, btn: QPushButton):
        self._apply_mode_btn_style()

        if btn == self._text_mode_btn:
            self._is_image_mode = False
            self._image_area.hide()
            self._clear_image()
            # 恢复文生视频分辨率列表
            current = self.resolution_combo.currentText()
            self.resolution_combo.clear()
            self.resolution_combo.addItems(self._TEXT_RESOLUTIONS)
            if current in self._TEXT_RESOLUTIONS:
                self.resolution_combo.setCurrentText(current)
            else:
                self.resolution_combo.setCurrentIndex(0)
            self.prompt_input.setPlaceholderText(
                "描述你想生成的视频，英文效果更好...\n"
                "例: A cinematic shot of a cat walking on the beach at sunset, "
                "soft ocean waves, warm golden lighting, realistic motion"
            )
        else:
            self._is_image_mode = True
            self._image_area.show()
            # 切为图生视频分辨率列表
            current = self.resolution_combo.currentText()
            self.resolution_combo.clear()
            self.resolution_combo.addItems(self._IMAGE_RESOLUTIONS)
            match = next(
                (r for r in self._IMAGE_RESOLUTIONS if r.startswith(current[:4])),
                None,
            )
            self.resolution_combo.setCurrentText(match or self._IMAGE_RESOLUTIONS[0])
            self.prompt_input.setPlaceholderText(
                "描述图片中内容的运动方式，英文效果更好...\n"
                "例: The woman slowly turns around and looks back at the camera, "
                "natural facial expression, cinematic camera movement"
            )

    # ── 图片选择 ──
    def _on_img_drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._img_drop_widget.setStyleSheet("""
                QLabel {
                    background: #eef2ff; border: 2px dashed #6366f1; border-radius: 10px;
                    color: #6366f1; font-size: 13px;
                }
            """)

    def _on_img_drop(self, event):
        # 恢复样式
        self._img_drop_widget.setStyleSheet("""
            QLabel {
                background: #f8f9fb; border: 2px dashed #d1d5db; border-radius: 10px;
                color: #9ca3af; font-size: 13px;
            }
            QLabel:hover { border-color: #a5b4fc; color: #6366f1; }
        """)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
                self._set_image(path)

    def _on_img_drop_clicked(self, event):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*.*)"
        )
        if path:
            self._set_image(path)

    def _set_image(self, path: str):
        self._image_path = path
        # 显示预览
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._img_preview.setPixmap(
                pixmap.scaled(78, 78, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self._img_preview.show()
        else:
            self._img_preview.setText("⚠")
            self._img_preview.show()
        # 路径文本（截断显示）
        display = os.path.basename(path)
        self._img_path_label.setText(display)
        self._img_path_label.show()
        self._img_clear_btn.show()
        # 隐藏拖放提示
        self._img_drop_widget.setText("🖼 点击更换图片")

    def _clear_image(self):
        self._image_path = ""
        self._img_preview.hide()
        self._img_preview.clear()
        self._img_path_label.hide()
        self._img_clear_btn.hide()
        self._img_drop_widget.setText("🖼 点击或拖放图片到此处")

    # ── 参数 & 生成 ──
    def _on_duration_changed(self):
        """时长变化时联动分辨率和参数

        不同时长对应的参数：
          约 3 秒  → num_frames: 81,  frame_rate: 24, 推荐 768x1280 竖屏
          约 5 秒  → num_frames: 121, frame_rate: 24, 推荐 1152x768 横屏
          约 10 秒 → num_frames: 241, frame_rate: 24, 推荐 1152x768 横屏
          约 18 秒 → num_frames: 441, frame_rate: 24, 推荐 1152x768 横屏
        """
        dur_key = self.duration_combo.currentText()
        # 根据 DURATION_PRESETS 获取推荐分辨率
        _, _, recommended_res = self.DURATION_PRESETS.get(
            dur_key, (121, 24, "1152x768")
        )
        # 在分辨率下拉列表中查找最接近的选项
        best_idx = 0
        best_score = -1
        for i in range(self.resolution_combo.count()):
            text = self.resolution_combo.itemText(i)
            res_part = text.split()[0]  # e.g. "1152x768"
            try:
                w, h = res_part.split("x")
                res_str = f"{w}x{h}"
            except Exception:
                continue
            # 精确匹配优先；否则选第一个包含相同宽高的
            if res_str == recommended_res:
                best_idx = i
                break
            # 模糊匹配（推荐分辨率的宽高在选项中出现过）
            if recommended_res.split("x")[0] in res_part or recommended_res.split("x")[1] in res_part:
                if best_score < 1:
                    best_idx = i
                    best_score = 1
        self.resolution_combo.setCurrentIndex(best_idx)

    def _get_params(self):
        """根据当前选择返回 (height, width, num_frames, frame_rate)"""
        dur_key = self.duration_combo.currentText()
        num_frames, frame_rate, _ = self.DURATION_PRESETS.get(
            dur_key, (121, 24, "1152x768")
        )
        res_text = self.resolution_combo.currentText().split()[0]  # "1152x768"
        try:
            w, h = res_text.split("x")
            width = int(w)
            height = int(h)
        except Exception:
            width, height = 1152, 768
        return height, width, num_frames, frame_rate

    def get_selected_model_id(self) -> str:
        return self.model_combo.currentData() or "agnes-video-v2.0"

    def _on_generate(self):
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            self.status_label.setText("请输入提示词")
            self.status_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")
            return
        if self._is_image_mode and not self._image_path:
            self.status_label.setText("图生视频需要选择一张参考图片")
            self.status_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")
            return
        height, width, num_frames, frame_rate = self._get_params()
        image = self._image_path if self._is_image_mode else ""
        self.generate_clicked.emit(prompt, height, width, num_frames, frame_rate, image)

    def set_generating(self, generating: bool):
        self.generate_btn.setEnabled(not generating)
        if generating:
            self.generate_btn.setText("生成中...")
            self.status_label.setText("正在创建视频任务，请稍候...")
            self.status_label.setStyleSheet("color: #f97316; font-size: 12px; background: transparent;")
        else:
            self.generate_btn.setText("生成视频")

    def append_status_log(self, text: str):
        """实时进度日志"""
        if not text:
            return
        prev = self.status_label.text() or ""
        self.status_label.setText((prev + "\n" + text).strip()[-300:])
        self.status_label.setStyleSheet("color: #6366f1; font-size: 12px; background: transparent;")

    def set_result(self, video_url: str):
        """显示生成结果"""
        self._result_url = video_url
        self.status_label.setText("视频生成完成！")
        self.status_label.setStyleSheet("color: #22c55e; font-size: 12px; background: transparent;")
        self.result_label.setText(f"<a href='{video_url}' style='color: #6366f1;'>{video_url}</a>")
        self.result_label.setOpenExternalLinks(True)
        self.action_btn.setText("打开视频")
        self.action_btn.show()

    def set_error(self, msg: str):
        self.status_label.setText(f"生成失败: {msg}")
        self.status_label.setStyleSheet("color: #ef4444; font-size: 12px; background: transparent;")

    def _open_video(self):
        """在浏览器中打开视频"""
        if self._result_url:
            import webbrowser
            webbrowser.open(self._result_url)


# ──────────────────────────────────────────
# 嵌入式视频播放器（用于聊天区渲染视频）
# ──────────────────────────────────────────
class VideoPlayerWidget(QWidget):
    """在聊天消息中嵌入的视频播放器

    优先使用 QMediaPlayer + QVideoWidget 进行内联播放，
    若 QtMultimedia 模块不可用则回退为带播放按钮的卡片式布局。
    """
    _HAS_QT_MULTIMEDIA = False

    @staticmethod
    def _check_multimedia():
        try:
            from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401
            from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: F401
            return True
        except (ImportError, ModuleNotFoundError):
            return False

    def __init__(self, video_url: str, parent=None):
        super().__init__(parent)
        self.video_url = video_url
        self._player = None
        self._play_btn = None
        self._setup_ui()

    # ── 样式常量 ──
    _PLAY_BTN_STYLE = """
        QPushButton {
            background: rgba(99, 102, 241, 0.88); color: white;
            border: none; border-radius: 8px; font-size: 13px; font-weight: bold;
        }
        QPushButton:hover {
            background: rgba(79, 70, 229, 0.95);
        }
    """

    _CONTROL_BTN_STYLE = """
        QPushButton {
            background: #f3f4f6; color: #374151; border: 1px solid #e5e7eb;
            border-radius: 7px; font-size: 12px; padding: 3px 10px;
        }
        QPushButton:hover { background: #e5e7eb; }
    """

    _CARD_STYLE = """
        QWidget#VideoCard {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #0f0f23, stop:1 #1a1a2e);
            border-radius: 14px; border: 1px solid rgba(99,102,241,0.25);
        }
    """

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        has_player = self._check_multimedia()

        if has_player:
            self._build_player_ui(layout)
        else:
            self._build_card_ui(layout)

    def _build_player_ui(self, layout: QVBoxLayout):
        """QMediaPlayer + QVideoWidget 内联播放"""
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumSize(560, 315)
        self._video_widget.setMaximumWidth(600)
        self._video_widget.setStyleSheet("background: #000; border-radius: 10px;")

        self._player = QMediaPlayer()
        self._player.setVideoOutput(self._video_widget)
        self._player.setSource(QUrl(self.video_url))
        self._player.errorOccurred.connect(self._on_media_error)

        # ── 控制栏 ──
        controls = QWidget()
        controls.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(controls)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(8)

        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.setCursor(Qt.PointingHandCursor)
        self._play_btn.setFixedSize(72, 30)
        self._play_btn.setStyleSheet(self._PLAY_BTN_STYLE)
        self._play_btn.clicked.connect(self._toggle_play)

        browser_btn = QPushButton("🌐 浏览器打开")
        browser_btn.setCursor(Qt.PointingHandCursor)
        browser_btn.setStyleSheet(self._CONTROL_BTN_STYLE)
        browser_btn.clicked.connect(self._open_browser)

        copy_btn = QPushButton("📋 复制链接")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(self._CONTROL_BTN_STYLE)
        copy_btn.clicked.connect(self._copy_link)

        ctrl_layout.addWidget(self._play_btn)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(copy_btn)
        ctrl_layout.addWidget(browser_btn)

        layout.addWidget(self._video_widget)
        layout.addWidget(controls)

    def _build_card_ui(self, layout: QVBoxLayout):
        """回退方案：带播放图标的卡片"""
        card = QWidget()
        card.setObjectName("VideoCard")
        card.setFixedSize(560, 315)
        card.setStyleSheet(self._CARD_STYLE)

        overlay = QVBoxLayout(card)
        overlay.setAlignment(Qt.AlignCenter)
        overlay.setSpacing(10)

        icon = QLabel("🎬")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 48px; background: transparent;")

        title = QLabel("视频已生成")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: #e0e0f0; font-size: 15px; font-weight: bold; background: transparent;"
        )

        play_btn = QPushButton("▶ 在浏览器中播放")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setFixedSize(160, 36)
        play_btn.setStyleSheet(self._PLAY_BTN_STYLE)
        play_btn.clicked.connect(self._open_browser)

        link = QLabel(f"<a href='{self.video_url}' style='color: #818cf8; "
                       "font-size: 11px; text-decoration: none;'>打开链接</a>")
        link.setAlignment(Qt.AlignCenter)
        link.setOpenExternalLinks(True)
        link.setStyleSheet("background: transparent;")

        overlay.addWidget(icon)
        overlay.addWidget(title)
        overlay.addWidget(play_btn)
        overlay.addWidget(link)

        layout.addWidget(card)

    def _toggle_play(self):
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        state = self._player.playbackState()
        if state == QMediaPlayer.PlayingState:
            self._player.pause()
            self._play_btn.setText("▶ 播放")
        else:
            self._player.play()
            self._play_btn.setText("⏸ 暂停")

    def _on_media_error(self, error, error_string: str):
        """播放出错时回退到卡片（隐藏播放器，追加卡片）"""
        if self._video_widget:
            self._video_widget.hide()
        if self._play_btn:
            self._play_btn.hide()
        # 追加回退卡片
        self._build_card_ui(self.layout())

    def _open_browser(self):
        import webbrowser
        webbrowser.open(self.video_url)

    def _copy_link(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.video_url)


# ──────────────────────────────────────────
# 代码语法高亮器（Catppuccin Mocha 深色主题）
# ──────────────────────────────────────────
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QFont


class CodeHighlighter(QSyntaxHighlighter):
    """通用代码语法高亮器，支持 Python / JS / C / Java 等"""

    # Catppuccin Mocha（深色）配色
    DARK_COLORS = {
        'keyword':   '#cba6f7',  # 紫色
        'builtin':   '#f9e2af',  # 黄色
        'string':    '#a6e3a1',  # 绿色
        'comment':   '#6c7086',  # 灰色
        'number':    '#fab387',  # 橙色
        'decorator': '#f38ba8',  # 粉色
        'function':  '#6e7fe0',  # 蓝色
        'class':     '#f9e2af',  # 黄色
        'operator':  '#89dceb',  # 青色
    }
    # 亮色主题配色（在白底上保持良好对比度）
    LIGHT_COLORS = {
        'keyword':   '#8957e5',  # 紫色
        'builtin':   '#b08800',  # 黄色
        'string':    '#1a7f37',  # 绿色
        'comment':   '#8a8f98',  # 灰色
        'number':    '#b35900',  # 橙色
        'decorator': '#cf222e',  # 粉色
        'function':  '#4f46e5',  # 蓝色
        'class':     '#b08800',  # 黄色
        'operator':  '#0a6fb0',  # 青色
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.COLORS = self.DARK_COLORS.copy()
        self._rules = []
        self._build_rules()

    def apply_theme(self, theme: str):
        """切换语法高亮配色（亮色/深色主题）"""
        self.COLORS = (self.LIGHT_COLORS if theme == 'light'
                       else self.DARK_COLORS).copy()
        self._build_rules()
        self.rehighlight()

    def _fmt(self, color_key, bold=False):
        f = QTextCharFormat()
        f.setForeground(QColor(self.COLORS[color_key]))
        if bold:
            f.setFontWeight(QFont.Bold)
        return f

    def _build_rules(self):
        import re
        kw = self._fmt('keyword', bold=True)
        builtin_f = self._fmt('builtin')
        string_f = self._fmt('string')
        comment_f = self._fmt('comment')
        number_f = self._fmt('number')
        decorator_f = self._fmt('decorator')
        function_f = self._fmt('function')

        # Python / JS / C / Java 通用关键字
        keywords = (
            'False|None|True|and|as|assert|async|await|break|class|continue|'
            'def|del|elif|else|except|finally|for|from|global|if|import|in|'
            'is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield|'
            'var|let|const|function|new|this|typeof|instanceof|switch|case|'
            'default|do|void|delete|throw|catch|typeof|interface|extends|'
            'implements|package|protected|public|private|abstract|static|'
            'final|native|synchronized|transient|volatile|enum|super|'
            'int|float|double|char|bool|long|short|byte|unsigned|signed|'
            'struct|union|typedef|extern|register|auto|goto|sizeof|'
            'fn|pub|mod|use|impl|trait|match|loop|move|ref|mut|async|await'
        )
        self._rules.append((re.compile(rf'\b({keywords})\b'), kw))

        # 内置函数 / 类型
        builtins = (
            'print|len|range|str|int|float|list|dict|set|tuple|bool|'
            'type|isinstance|issubclass|hasattr|getattr|setattr|delattr|'
            'abs|all|any|bin|chr|dir|divmod|enumerate|eval|exec|filter|'
            'format|hash|hex|id|input|iter|map|max|min|next|oct|open|'
            'ord|pow|property|repr|reversed|round|slice|sorted|sum|'
            'super|vars|zip|__init__|__str__|__repr__|__name__|__main__|'
            'console|log|warn|error|document|window|Math|JSON|Promise|'
            'Array|Object|String|Number|Boolean|Map|Set|Symbol|'
            'self|cls|NoneType|Exception|ValueError|TypeError|KeyError|'
            'IndexError|RuntimeError|StopIteration|ImportError'
        )
        self._rules.append((re.compile(rf'\b({builtins})\b'), builtin_f))

        # 装饰器
        self._rules.append((re.compile(r'@\w+'), decorator_f))

        # 函数定义
        self._rules.append((re.compile(r'\b(def|function|fn)\s+(\w+)'), function_f))

        # 数字
        self._rules.append((re.compile(r'\b\d+\.?\d*([eE][+-]?\d+)?\b'), number_f))
        self._rules.append((re.compile(r'\b0[xX][0-9a-fA-F]+\b'), number_f))
        self._rules.append((re.compile(r'\b0[bB][01]+\b'), number_f))

        # 运算符
        ops = r'[+\-*/%=<>!&|^~]+'
        self._rules.append((re.compile(ops), self._fmt('operator')))

        # 字符串（单引号、双引号、三引号）
        self._triple_double = re.compile(r'"""')
        self._triple_single = re.compile(r"'''")
        self._single_line_dq = re.compile(r'"[^"\n]*"')
        self._single_line_sq = re.compile(r"'[^'\n]*'")
        self._string_fmt = string_f

        # 单行注释
        self._rules.append((re.compile(r'#[^\n]*'), comment_f))
        self._rules.append((re.compile(r'//[^\n]*'), comment_f))

        # 多行注释状态
        self._comment_fmt = comment_f
        self._multi_comment_start = re.compile(r'/\*')
        self._multi_comment_end = re.compile(r'\*/')

    def highlightBlock(self, text):
        # 三引号字符串（Python 多行）
        self._highlight_multiline_quotes(text)

        # 单行规则
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

        # 单行字符串
        for m in self._single_line_dq.finditer(text):
            s, e = m.start(), m.end()
            if self.format(s) != self._comment_fmt:
                self.setFormat(s, e - s, self._string_fmt)
        for m in self._single_line_sq.finditer(text):
            s, e = m.start(), m.end()
            if self.format(s) != self._comment_fmt:
                self.setFormat(s, e - s, self._string_fmt)

        # 多行注释 /* */
        self._highlight_multiline_comment(text)

    def _highlight_multiline_quotes(self, text):
        """处理三引号多行字符串"""
        state = self.previousBlockState()
        start = 0
        if state == 1:
            # 继续上一个三引号字符串
            end = self._find_triple_end(text, 0, '"""')
            if end >= 0:
                self.setFormat(0, end + 3, self._string_fmt)
                self.setCurrentBlockState(0)
                start = end + 3
            else:
                self.setFormat(0, len(text), self._string_fmt)
                self.setCurrentBlockState(1)
                return

        while start < len(text):
            dq = self._triple_double.search(text, start)
            sq = self._triple_single.search(text, start)
            if dq and (not sq or dq.start() < sq.start()):
                pos = dq.start()
                end = self._find_triple_end(text, pos + 3, '"""')
            elif sq:
                pos = sq.start()
                end = self._find_triple_end(text, pos + 3, "'''")
            else:
                break

            if end >= 0:
                self.setFormat(pos, end + 3 - pos, self._string_fmt)
                start = end + 3
            else:
                self.setFormat(pos, len(text) - pos, self._string_fmt)
                self.setCurrentBlockState(1)
                break

    def _find_triple_end(self, text, start, quote):
        idx = text.find(quote, start)
        return idx

    def _highlight_multiline_comment(self, text):
        """处理 /* */ 多行注释"""
        state = self.previousBlockState()
        start = 0
        if state == 2:
            end = self._multi_comment_end.search(text, 0)
            if end:
                self.setFormat(0, end.end(), self._comment_fmt)
                self.setCurrentBlockState(0)
                start = end.end()
            else:
                self.setFormat(0, len(text), self._comment_fmt)
                self.setCurrentBlockState(2)
                return

        while start < len(text):
            m = self._multi_comment_start.search(text, start)
            if not m:
                break
            end = self._multi_comment_end.search(text, m.end())
            if end:
                self.setFormat(m.start(), end.end() - m.start(), self._comment_fmt)
                start = end.end()
            else:
                self.setFormat(m.start(), len(text) - m.start(), self._comment_fmt)
                self.setCurrentBlockState(2)
                break


# ──────────────────────────────────────────
# 内联 Diff 视图组件（红绿 diff + Accept/Reject）
# ──────────────────────────────────────────

class DiffViewWidget(QWidget):
    """在聊天框中显示文件修改的 diff 视图，支持 Accept / Reject。

    用法：
        widget = DiffViewWidget(file_path, old_content, new_content)
        widget.accepted.connect(callback_on_accept)
        widget.rejected.connect(callback_on_reject)
    """

    accepted = Signal(str)   # file_path
    rejected = Signal(str)   # file_path

    def __init__(self, file_path: str, old_content: str, new_content: str,
                 applied: bool = True, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._old_content = old_content
        self._new_content = new_content
        self._applied = applied  # 是否已自动写入文件
        self._accepted = False
        self._rejected = False
        self._setup_ui()

    def _setup_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#diff_view {
                background: #1e1e2e;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
        """)
        self.setObjectName("diff_view")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏 ──
        header = QWidget()
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setFixedHeight(36)
        header.setStyleSheet("""
            QWidget {
                background: rgba(255,255,255,0.08);
                border-radius: 10px 10px 0 0;
            }
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)
        h_layout.setSpacing(8)

        import os as _os
        fname = _os.path.basename(self._file_path)

        file_icon = QLabel("📝")
        file_icon.setStyleSheet("background: transparent; font-size: 14px;")

        title_label = QLabel(f"编辑 — {fname}")
        title_label.setStyleSheet(
            "color: #d9dae0; font-size: 13px; font-weight: bold; background: transparent;")
        title_label.setToolTip(self._file_path)

        # diff 统计
        import difflib as _difflib
        old_lines = self._old_content.splitlines(keepends=True)
        new_lines = self._new_content.splitlines(keepends=True)
        diff_stats = list(_difflib.unified_diff(old_lines, new_lines, lineterm=''))
        added = sum(1 for l in diff_stats if l.startswith('+') and not l.startswith('+++'))
        removed = sum(1 for l in diff_stats if l.startswith('-') and not l.startswith('---'))

        stats_label = QLabel(f"+{added}  -{removed}")
        stats_label.setStyleSheet(
            "color: #a6adc8; font-size: 11px; background: transparent; font-weight: 600;")

        h_layout.addWidget(file_icon)
        h_layout.addWidget(title_label)
        h_layout.addWidget(stats_label)
        h_layout.addStretch()

        # Accept / Reject 按钮
        self._accept_btn = QPushButton("✓ 采纳")
        self._accept_btn.setFixedHeight(26)
        self._accept_btn.setCursor(Qt.PointingHandCursor)
        self._accept_btn.setStyleSheet("""
            QPushButton {
                background: rgba(166, 227, 161, 0.15);
                color: #a6e3a1;
                border: 1px solid rgba(166, 227, 161, 0.3);
                border-radius: 13px;
                font-size: 11px;
                font-weight: bold;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: rgba(166, 227, 161, 0.25);
                border-color: #a6e3a1;
            }
        """)
        self._accept_btn.clicked.connect(self._on_accept)

        self._reject_btn = QPushButton("✗ 拒绝")
        self._reject_btn.setFixedHeight(26)
        self._reject_btn.setCursor(Qt.PointingHandCursor)
        self._reject_btn.setStyleSheet("""
            QPushButton {
                background: rgba(243, 139, 168, 0.15);
                color: #f38ba8;
                border: 1px solid rgba(243, 139, 168, 0.3);
                border-radius: 13px;
                font-size: 11px;
                font-weight: bold;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: rgba(243, 139, 168, 0.25);
                border-color: #f38ba8;
            }
        """)
        self._reject_btn.clicked.connect(self._on_reject)

        # 如果已经自动应用，标记按钮状态
        if self._applied:
            self._accept_btn.setText("✓ 已采纳")
            self._accept_btn.setEnabled(False)
            self._accept_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(166, 227, 161, 0.2);
                    color: #a6e3a1;
                    border: 1px solid rgba(166, 227, 161, 0.4);
                    border-radius: 13px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 0 12px;
                }
            """)

        h_layout.addWidget(self._accept_btn)
        h_layout.addWidget(self._reject_btn)

        layout.addWidget(header)

        # ── Diff 内容区 ──
        diff_html = self._build_diff_html(old_lines, new_lines)
        diff_label = QLabel(diff_html)
        diff_label.setTextFormat(Qt.RichText)
        diff_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diff_label.setWordWrap(False)
        diff_label.setStyleSheet("""
            QLabel {
                background: #11111b;
                color: #d9dae0;
                font-family: 'Consolas', 'Courier New', 'Source Code Pro', monospace;
                font-size: 12px;
                padding: 8px 0;
                border: none;
                border-radius: 0 0 10px 10px;
            }
        """)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(diff_label)
        scroll.setStyleSheet("""
            QScrollArea {
                background: #11111b;
                border: none;
                border-radius: 0 0 10px 10px;
            }
            QScrollBar:vertical {
                background: #11111b; width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #45475a; min-height: 20px; border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover { background: #585b70; }
            QScrollBar:horizontal {
                background: #11111b; height: 8px;
            }
            QScrollBar::handle:horizontal {
                background: #45475a; min-width: 20px; border-radius: 4px;
            }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        """)
        # 限制最大高度
        scroll.setMaximumHeight(400)

        layout.addWidget(scroll)

    def _build_diff_html(self, old_lines: list, new_lines: list) -> str:
        """构建带行号的 diff HTML"""
        import difflib as _difflib
        import html as _html

        diff = _difflib.ndiff(old_lines, new_lines)
        rows = []
        old_line_no = 0
        new_line_no = 0

        for line in diff:
            prefix = line[:2]
            content = line[2:]

            if prefix == '  ':
                # 上下文行
                old_line_no += 1
                new_line_no += 1
                rows.append(self._make_diff_row(
                    str(old_line_no), str(new_line_no),
                    _html.escape(content.rstrip()), 'context'))
            elif prefix == '- ':
                # 删除行
                old_line_no += 1
                rows.append(self._make_diff_row(
                    str(old_line_no), '',
                    _html.escape(content.rstrip()), 'removed'))
            elif prefix == '+ ':
                # 新增行
                new_line_no += 1
                rows.append(self._make_diff_row(
                    '', str(new_line_no),
                    _html.escape(content.rstrip()), 'added'))
            elif prefix == '? ':
                # ndiff 的变化标记行，跳过
                continue

        if not rows:
            return '<div style="padding:8px 12px;color:#6c7086;">（无变化）</div>'

        return (
            '<table cellspacing="0" cellpadding="0" style="width:100%;">'
            f'{"".join(rows)}'
            '</table>'
        )

    def _make_diff_row(self, old_no: str, new_no: str,
                       content: str, line_type: str) -> str:
        """构建单行 diff HTML"""
        styles = {
            'context': {
                'bg': '#11111b',
                'color': '#d9dae0',
                'prefix': ' ',
                'no_color': '#45475a',
            },
            'added': {
                'bg': 'rgba(166, 227, 161, 0.1)',
                'color': '#a6e3a1',
                'prefix': '+',
                'no_color': '#a6e3a1',
            },
            'removed': {
                'bg': 'rgba(243, 139, 168, 0.1)',
                'color': '#f38ba8',
                'prefix': '-',
                'no_color': '#f38ba8',
            },
        }
        s = styles.get(line_type, styles['context'])

        return (
            f'<tr style="background:{s["bg"]};">'
            f'<td style="width:36px;text-align:right;padding:0 6px;'
            f'color:{s["no_color"]};font-size:11px;user-select:none;">{old_no}</td>'
            f'<td style="width:36px;text-align:right;padding:0 6px;'
            f'color:{s["no_color"]};font-size:11px;user-select:none;">{new_no}</td>'
            f'<td style="width:16px;text-align:center;padding:0 2px;'
            f'color:{s["color"]};font-weight:bold;user-select:none;">{s["prefix"]}</td>'
            f'<td style="padding:0 8px;color:{s["color"]};'
            f'white-space:pre;font-family:Consolas,monospace;">{content}</td>'
            f'</tr>'
        )

    def _on_accept(self):
        """采纳修改"""
        if self._rejected:
            return
        self._accepted = True
        self._accept_btn.setText("✓ 已采纳")
        self._accept_btn.setEnabled(False)
        self._reject_btn.setEnabled(False)
        self.accepted.emit(self._file_path)

    def _on_reject(self):
        """拒绝修改，恢复原文件"""
        if self._accepted:
            return
        self._rejected = True
        try:
            import os as _os
            _os.makedirs(_os.path.dirname(self._file_path), exist_ok=True)
            with open(self._file_path, 'w', encoding='utf-8') as f:
                f.write(self._old_content)
        except Exception:
            pass
        self._reject_btn.setText("✗ 已拒绝")
        self._reject_btn.setEnabled(False)
        self._accept_btn.setEnabled(False)
        self.rejected.emit(self._file_path)

    def is_resolved(self) -> bool:
        """是否已处理（accept 或 reject）"""
        return self._accepted or self._rejected


# ──────────────────────────────────────────
# 多文件 Diff 合并视图
# ──────────────────────────────────────────

class DiffGroupWidget(QWidget):
    """多文件 Diff 合并视图 — 汇总标题 + 全局 Accept/Reject + 各文件 Diff"""

    all_accepted = Signal()
    all_rejected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._diffs = []  # [DiffViewWidget]
        self._collapsed = False
        self._setup_ui()

    def _setup_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("diff_group")
        self.setStyleSheet("""
            QWidget#diff_group {
                background: #2d2e34;
                border: 1px solid #3a3a3a;
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 汇总标题栏 ──
        self._header = QWidget()
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setFixedHeight(38)
        self._header.setStyleSheet("""
            QWidget { background: rgba(255,255,255,0.08); border-radius: 10px 10px 0 0; }
        """)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(12, 0, 8, 0)
        h_layout.setSpacing(8)

        icon = QLabel("📝")
        icon.setStyleSheet("background: transparent; font-size: 14px;")

        self._title = QLabel("文件变更 (0)")
        self._title.setStyleSheet(
            "color: #d9dae0; font-size: 13px; font-weight: bold; background: transparent;")

        self._stats = QLabel("+0  -0")
        self._stats.setStyleSheet(
            "color: #a6adc8; font-size: 11px; background: transparent; font-weight: 600;")

        h_layout.addWidget(icon)
        h_layout.addWidget(self._title)
        h_layout.addWidget(self._stats)
        h_layout.addStretch()

        # 全局按钮
        self._accept_all_btn = QPushButton("✓ 全部采纳")
        self._accept_all_btn.setFixedHeight(26)
        self._accept_all_btn.setCursor(Qt.PointingHandCursor)
        self._accept_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(166,227,161,0.15); color: #a6e3a1;
                border: 1px solid rgba(166,227,161,0.3); border-radius: 13px;
                font-size: 11px; font-weight: bold; padding: 0 12px;
            }
            QPushButton:hover { background: rgba(166,227,161,0.25); }
        """)
        self._accept_all_btn.clicked.connect(self._accept_all)

        self._reject_all_btn = QPushButton("✗ 全部拒绝")
        self._reject_all_btn.setFixedHeight(26)
        self._reject_all_btn.setCursor(Qt.PointingHandCursor)
        self._reject_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(243,139,168,0.15); color: #f38ba8;
                border: 1px solid rgba(243,139,168,0.3); border-radius: 13px;
                font-size: 11px; font-weight: bold; padding: 0 12px;
            }
            QPushButton:hover { background: rgba(243,139,168,0.25); }
        """)
        self._reject_all_btn.clicked.connect(self._reject_all)

        h_layout.addWidget(self._accept_all_btn)
        h_layout.addWidget(self._reject_all_btn)

        layout.addWidget(self._header)

        # ── Diff 列表区 ──
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(4)
        layout.addWidget(self._body)

    def add_diff(self, diff_widget: 'DiffViewWidget'):
        """添加一个 DiffViewWidget"""
        self._diffs.append(diff_widget)
        self._body_layout.addWidget(diff_widget)
        self._update_summary()

    def _update_summary(self):
        total_added = 0
        total_removed = 0
        for dw in self._diffs:
            stats_text = ""
            for child in dw.findChildren(QLabel):
                if child.text().startswith("+") and "-" in child.text():
                    stats_text = child.text()
                    break
            import re as _re
            added_match = _re.search(r'\+(\d+)', stats_text)
            removed_match = _re.search(r'-(\d+)', stats_text)
            if added_match:
                total_added += int(added_match.group(1))
            if removed_match:
                total_removed += int(removed_match.group(1))

        self._title.setText(f"文件变更 ({len(self._diffs)})")
        self._stats.setText(f"+{total_added}  -{total_removed}")

    def _accept_all(self):
        for dw in self._diffs:
            if not dw.is_resolved():
                dw._on_accept()
        self.all_accepted.emit()

    def _reject_all(self):
        for dw in self._diffs:
            if not dw.is_resolved():
                dw._on_reject()
        self.all_rejected.emit()


# ──────────────────────────────────────────
# AI 消息操作栏（Copy / Retry）
# ──────────────────────────────────────────

class MessageActionBar(QWidget):
    """AI 消息底部的操作按钮栏 — Copy / Retry"""

    copy_clicked = Signal()
    retry_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = 'light'
        self._buttons = []
        self._setup_ui()

    def _btn_style(self, theme: str) -> str:
        """根据主题生成按钮样式，深色主题使用亮色文字"""
        if theme == 'dark':
            return """
                QPushButton {
                    background: rgba(77,107,254,0.10); color: #9a9ca6;
                    border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
                    font-size: 11px; padding: 2px 10px;
                }
                QPushButton:hover {
                    background: rgba(77,107,254,0.20); color: #6e7fe0;
                    border-color: rgba(77,107,254,0.3);
                }
            """
        return """
            QPushButton {
                background: rgba(99,102,241,0.06); color: #6b7280;
                border: 1px solid rgba(0,0,0,0.06); border-radius: 10px;
                font-size: 11px; padding: 2px 10px;
            }
            QPushButton:hover {
                background: rgba(99,102,241,0.12); color: #6e7fe0;
                border-color: rgba(99,102,241,0.2);
            }
        """

    def apply_theme(self, theme: str):
        """主题切换时更新按钮文字/边框颜色"""
        self._theme = theme
        style = self._btn_style(theme)
        for btn in self._buttons:
            btn.setStyleSheet(style)

    def _setup_ui(self):
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        btn_style = self._btn_style(self._theme)

        copy_btn = QPushButton("📋 复制")
        copy_btn.setFixedHeight(22)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(btn_style)
        copy_btn.clicked.connect(self.copy_clicked.emit)
        layout.addWidget(copy_btn)
        self._buttons.append(copy_btn)

        retry_btn = QPushButton("🔄 重试")
        retry_btn.setFixedHeight(22)
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setStyleSheet(btn_style)
        retry_btn.clicked.connect(self.retry_clicked.emit)
        layout.addWidget(retry_btn)
        self._buttons.append(retry_btn)

        layout.addStretch()


# ══════════════════════════════════════════════════════════════
#  代码编辑器面板 + 实时 Linter（Cursor 风格）
# ══════════════════════════════════════════════════════════════

class LineNumberArea(QWidget):
    """行号区域"""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self.setFixedWidth(48)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QFont
        editor = self._editor
        bg = getattr(editor, '_line_no_bg', "#181825")
        cur = getattr(editor, '_line_no_cur', "#6e7fe0")
        normal = getattr(editor, '_line_no_normal', "#45475a")
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(bg))

        block = editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(editor.blockBoundingGeometry(block).translated(
            editor.contentOffset()).top())
        bottom = top + round(editor.blockBoundingRect(block).height())

        painter.setFont(QFont("Consolas", 10))
        current_line = editor.textCursor().blockNumber()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                # 当前行号高亮
                if block_number == current_line:
                    painter.setPen(QColor(cur))
                else:
                    painter.setPen(QColor(normal))
                painter.drawText(0, top, self.width() - 8, 20,
                                 Qt.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + round(editor.blockBoundingRect(block).height())
            block_number += 1


class RealTimeLinter(QObject):
    """实时 Linter — 在用户停止输入后执行语法检查

    Signals:
        errors_found: (file_path, errors list) — 每项为 {line, col, message, severity}
        errors_cleared: (file_path,) — 无错误
    """

    errors_found = Signal(str, list)   # file_path, errors
    errors_cleared = Signal(str)       # file_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_check)
        self._delay_ms = 500           # 防抖延迟
        self._file_path = ""
        self._last_errors = []
        self._enabled = True

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled:
            self._timer.stop()

    def schedule_check(self, file_path: str, content: str):
        """调度一次延迟检查（防抖）"""
        if not self._enabled:
            return
        self._file_path = file_path
        self._pending_content = content
        self._timer.start(self._delay_ms)

    def check_now(self, file_path: str, content: str):
        """立即执行检查"""
        self._file_path = file_path
        self._pending_content = content
        self._run_check()

    def _run_check(self):
        content = getattr(self, "_pending_content", "")
        if not content or not self._file_path:
            return

        errors = []
        ext = os.path.splitext(self._file_path)[1].lower()

        if ext == ".py":
            errors = self._check_python(content)
        elif ext in (".js", ".jsx", ".ts", ".tsx"):
            errors = self._check_basic_syntax(content, "//", "/*", "*/")
        elif ext in (".html", ".xml"):
            errors = self._check_basic_syntax(content, "<!--", "-->")
        else:
            # 其他文件不做语法检查
            pass

        if errors:
            self._last_errors = errors
            self.errors_found.emit(self._file_path, errors)
        else:
            if self._last_errors:
                self._last_errors = []
            self.errors_cleared.emit(self._file_path)

    def _check_python(self, content: str) -> list:
        """Python 语法检查：ast.parse + py_compile"""
        import ast as _ast
        errors = []
        try:
            _ast.parse(content, filename=self._file_path)
        except SyntaxError as e:
            errors.append({
                "line": e.lineno or 1,
                "col": e.offset or 0,
                "message": e.msg or "语法错误",
                "severity": "error",
            })
            return errors
        except Exception as e:
            errors.append({
                "line": 1, "col": 0,
                "message": f"解析异常: {e}",
                "severity": "error",
            })
            return errors

        # 更严格的编译检查（仅当 ast 通过时）
        import tempfile, subprocess
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            result = subprocess.run(
                ["python", "-m", "py_compile", tmp_path],
                capture_output=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            os.unlink(tmp_path)
            if result.returncode != 0:
                stderr = result.stderr or ""
                lines = stderr.strip().split("\n")
                for line in lines:
                    if "SyntaxError" in line or "IndentationError" in line or "TabError" in line:
                        import re as _re
                        m = _re.search(r'line\s+(\d+)', line)
                        ln = int(m.group(1)) if m else 1
                        msg = line.split(":", 1)[-1].strip() if ":" in line else line
                        errors.append({
                            "line": ln, "col": 0,
                            "message": msg,
                            "severity": "error",
                        })
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            pass
        except Exception:
            pass

        return errors

    def _check_basic_syntax(self, content: str,
                            line_comment: str,
                            block_comment_start: str,
                            block_comment_end: str) -> list:
        """基础语法检查：括号匹配"""
        errors = []
        stack = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = set(pairs.values())
        opening = set(pairs.keys())
        line_num = 1
        in_string = False
        string_char = None
        in_block_comment = False

        for i, char in enumerate(content):
            if char == "\n":
                line_num += 1
                continue
            if in_block_comment:
                # 检查是否到达块注释结束
                if content[i:i + len(block_comment_end)] == block_comment_end:
                    in_block_comment = False
                continue
            if in_string:
                if char == string_char:
                    in_string = False
                continue
            if char in ('"', "'"):
                in_string = True
                string_char = char
                continue
            if char in opening:
                stack.append((char, line_num))
            elif char in closing:
                if not stack:
                    errors.append({
                        "line": line_num, "col": 0,
                        "message": f"多余的闭合括号 '{char}'",
                        "severity": "warning",
                    })
                else:
                    last_open, last_line = stack.pop()
                    expected_close = pairs[last_open]
                    if char != expected_close:
                        errors.append({
                            "line": line_num, "col": 0,
                            "message": f"括号不匹配：期望 '{expected_close}'，得到 '{char}'",
                            "severity": "error",
                        })

        for unclosed, line in stack:
            errors.append({
                "line": line, "col": 0,
                "message": f"未闭合的括号 '{unclosed}'",
                "severity": "error",
            })
        return errors

    @property
    def last_errors(self):
        return self._last_errors


class CodeEditorPanel(QWidget):
    """代码编辑器面板 — Cursor 风格的可编辑代码区域

    Features:
        - 行号显示 + 当前行高亮
        - 语法高亮（CodeHighlighter）
        - 实时 Linter 集成（Python / JS / HTML 等）
        - 错误行标记（红色下划线背景）
        - Ctrl+S 保存
        - 文件修改指示器
        - 保存/格式化按钮
        - 自动修复按钮（当检测到错误时）

    Signals:
        file_saved: (file_path, content) — 文件已保存
        request_fix: (file_path, errors, content) — 请求 Agent 自动修复
        file_opened: (file_path) — 文件已打开
    """

    # 信号
    file_saved = Signal(str, str)           # file_path, content
    request_fix = Signal(str, list, str)    # file_path, errors, content
    file_opened = Signal(str)               # file_path
    file_closed = Signal()                  # 文件被关闭（X 按钮）
    preview_url_requested = Signal(str)     # 请求预览 URL（用于自动启动后端场景）

    # 支持编辑的文件类型
    EDITABLE_EXTS = {
        '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss',
        '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
        '.md', '.txt', '.rst', '.csv', '.log', '.env', '.sh',
        '.bat', '.ps1', '.cmd', '.sql', '.c', '.cpp', '.h', '.hpp',
        '.java', '.go', '.rs', '.rb', '.php', '.swift', '.kt', 'r',
        '.vue', '.svelte', '.gitignore', '.dockerfile',
    }

    # 图片文件类型 — 在预览面板中显示
    IMAGE_EXTS = {'.png', '.gif', '.jpg', '.jpeg', '.bmp', '.svg', '.webp', '.ico'}

    # HTML 文件类型 — 可在 Web 预览中渲染
    WEB_PREVIEW_EXTS = {'.html', '.htm'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._is_modified = False
        self._orig_content = ""
        self._linter = RealTimeLinter(self)
        # 多文件 Tab 管理：{file_path: {content, orig_content, scroll_pos, modified, type}}
        self._open_files = {}
        self._theme = 'dark'
        self._setup_ui()
        self._connect_signals()
        self._apply_editor_theme(self._theme)

    def _setup_ui(self):
        self.setStyleSheet("background: #1e1e2e;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 文件 Tab 栏 ──
        self._tab_bar_widget = QWidget()
        self._tab_bar_widget.setFixedHeight(34)
        self._tab_bar_widget.setStyleSheet("""
            QWidget { background: #181825; border-bottom: 1px solid rgba(255,255,255,0.08); }
        """)
        self._tab_bar_layout = QHBoxLayout(self._tab_bar_widget)
        self._tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._tab_bar_layout.setSpacing(0)
        self._tab_bar_widget.hide()  # 无文件时隐藏
        layout.addWidget(self._tab_bar_widget)

        # ── 顶部工具栏 ──
        self._toolbar = QWidget()
        self._toolbar.setFixedHeight(36)
        self._toolbar.setStyleSheet("""
            QWidget { background: #181825; border-bottom: 1px solid rgba(255,255,255,0.08); }
        """)
        tb_layout = QHBoxLayout(self._toolbar)
        tb_layout.setContentsMargins(12, 0, 8, 0)
        tb_layout.setSpacing(8)

        self._file_label = QLabel("未打开文件")
        self._file_label.setStyleSheet(
            "color: #d9dae0; font-size: 12px; font-weight: bold; background: transparent;")
        self._file_label.setMinimumWidth(100)

        self._modified_indicator = QLabel("")
        self._modified_indicator.setStyleSheet("color: #f9e2af; font-size: 14px; background: transparent;")

        tb_layout.addWidget(self._file_label)
        tb_layout.addWidget(self._modified_indicator)
        tb_layout.addStretch()

        # Linter 状态标签
        self._lint_status = QLabel("")
        self._lint_status.setStyleSheet("color: #6c7086; font-size: 11px; background: transparent;")
        tb_layout.addWidget(self._lint_status)

        # 自动修复按钮（有错误时显示）
        self._fix_btn = QPushButton("🔧 自动修复")
        self._fix_btn.setFixedHeight(24)
        self._fix_btn.setCursor(Qt.PointingHandCursor)
        self._fix_btn.setStyleSheet("""
            QPushButton {
                background: rgba(137,180,250,0.15); color: #6e7fe0;
                border: 1px solid rgba(137,180,250,0.3); border-radius: 12px;
                font-size: 11px; font-weight: bold; padding: 0 10px;
            }
            QPushButton:hover { background: rgba(137,180,250,0.25); }
        """)
        self._fix_btn.clicked.connect(self._on_request_fix)
        self._fix_btn.hide()
        tb_layout.addWidget(self._fix_btn)

        # 保存按钮
        self._save_btn = QPushButton("💾 保存")
        self._save_btn.setFixedHeight(24)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: rgba(166,227,161,0.15); color: #a6e3a1;
                border: 1px solid rgba(166,227,161,0.3); border-radius: 12px;
                font-size: 11px; font-weight: bold; padding: 0 10px;
            }
            QPushButton:hover { background: rgba(166,227,161,0.25); }
            QPushButton:disabled { color: #45475a; background: transparent; border-color: rgba(255,255,255,0.08); }
        """)
        self._save_btn.clicked.connect(self.save_file)
        self._save_btn.setEnabled(False)
        tb_layout.addWidget(self._save_btn)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6c7086; background: transparent; border: none;
                          border-radius: 11px; font-size: 11px; }
            QPushButton:hover { color: #f38ba8; background: rgba(243,139,168,0.15); }
        """)
        close_btn.clicked.connect(self._on_close_button)
        tb_layout.addWidget(close_btn)

        layout.addWidget(self._toolbar)

        # ── StackedWidget: 编辑器 / 图片预览 / Web预览 ──
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: #1e1e2e; border: none;")

        # Page 0: 代码编辑器页面
        self._editor_page = QWidget()
        self._editor_page.setStyleSheet("background: #1e1e2e;")
        editor_layout = QVBoxLayout(self._editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        # ── 代码编辑区 ──
        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet("""
            QPlainTextEdit {
                background: #1e1e2e; color: #d9dae0;
                font-family: Consolas, "Courier New", "Source Code Pro", monospace;
                font-size: 13px; border: none; padding: 4px 8px;
                selection-background-color: rgba(137, 180, 250, 0.3);
            }
        """)
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._editor.blockCountChanged.connect(self._update_line_number_width)
        self._editor.updateRequest.connect(self._update_line_number_area)
        self._editor.cursorPositionChanged.connect(self._highlight_current_line)

        # 行号区域
        self._line_number_area = LineNumberArea(self._editor)

        # 语法高亮器
        self._highlighter = CodeHighlighter(self._editor.document())

        editor_layout.addWidget(self._editor, 1)

        # ── 底部错误面板 ──
        self._error_panel = QWidget()
        self._error_panel.setStyleSheet("background: #181825; border-top: 1px solid rgba(255,255,255,0.08);")
        self._error_panel.setFixedHeight(0)   # 默认隐藏
        err_layout = QVBoxLayout(self._error_panel)
        err_layout.setContentsMargins(8, 4, 8, 4)
        err_layout.setSpacing(2)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #f38ba8; font-size: 11px; background: transparent;")
        self._error_label.setWordWrap(True)
        err_layout.addWidget(self._error_label)

        editor_layout.addWidget(self._error_panel)

        self._stack.addWidget(self._editor_page)  # index 0

        # Page 1: 图片预览
        self._image_preview = ImagePreviewWidget()
        self._image_preview.file_closed.connect(self._on_close_button)
        self._stack.addWidget(self._image_preview)  # index 1

        # Page 2: Web 预览
        self._html_preview = HtmlPreviewWidget()
        self._html_preview.file_closed.connect(self._on_close_button)
        self._stack.addWidget(self._html_preview)  # index 2

        self._stack.setCurrentIndex(0)  # 默认显示编辑器
        layout.addWidget(self._stack, 1)

        # 快捷键
        from PySide6.QtGui import QShortcut, QKeySequence
        save_sc = QShortcut(QKeySequence("Ctrl+S"), self._editor)
        save_sc.activated.connect(self.save_file)

        self._highlight_current_line()

    def _connect_signals(self):
        self._editor.textChanged.connect(self._on_text_changed)
        self._linter.errors_found.connect(self._on_errors_found)
        self._linter.errors_cleared.connect(self._on_errors_cleared)

    # ── 主题 ──

    def _apply_editor_theme(self, theme: str):
        """根据主题应用代码编辑器的背景/文字/行号/工具栏配色"""
        self._theme = theme
        dark = (theme == 'dark')
        # 编辑器配色（深色沿用 Catppuccin 风格，亮色用浅底深字）
        if dark:
            bg = '#27282e'            # 与主题深灰一致，避免纯黑看不清
            panel_bg = '#2d2e34'
            toolbar_bg = '#2d2e34'
            line_no_bg = '#2b2c32'
            text_color = '#d9dae0'
            title_color = '#d9dae0'
            lint_color = '#8a8f98'
            line_no_normal = '#45475a'
            line_no_cur = '#6e7fe0'
            cur_line_bg = 'rgba(255,255,255,0.08)'
        else:
            bg = '#ffffff'
            panel_bg = '#f6f7f9'
            toolbar_bg = '#f0f1f5'
            line_no_bg = '#f0f1f5'
            text_color = '#1a1a2e'
            title_color = '#1d1d1f'
            lint_color = '#8a8f98'
            line_no_normal = '#b0b4be'
            line_no_cur = '#4f46e5'
            cur_line_bg = 'rgba(0,0,0,0.05)'

        self.setStyleSheet(f"background: {bg};")
        self._stack.setStyleSheet(f"background: {bg}; border: none;")
        self._editor_page.setStyleSheet(f"background: {bg};")
        self._tab_bar_widget.setStyleSheet(
            f"QWidget {{ background: {toolbar_bg}; "
            f"border-bottom: 1px solid rgba(255,255,255,0.08); }}")
        self._toolbar.setStyleSheet(
            f"QWidget {{ background: {toolbar_bg}; "
            f"border-bottom: 1px solid rgba(255,255,255,0.08); }}")
        self._error_panel.setStyleSheet(
            f"background: {panel_bg}; border-top: 1px solid rgba(255,255,255,0.08);")
        self._editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {bg}; color: {text_color};
                font-family: Consolas, "Courier New", "Source Code Pro", monospace;
                font-size: 13px; border: none; padding: 4px 8px;
                selection-background-color: {'rgba(137,180,250,0.3)' if dark else 'rgba(79,70,229,0.2)'};
            }}
        """)
        self._file_label.setStyleSheet(
            f"color: {title_color}; font-size: 12px; font-weight: bold; background: transparent;")
        self._lint_status.setStyleSheet(
            f"color: {lint_color}; font-size: 11px; background: transparent;")

        # 行号区域 / 当前行高亮配色（供 LineNumberArea.paintEvent 与 _highlight_current_line 使用）
        self._editor_bg = bg
        self._line_no_bg = line_no_bg
        self._line_no_normal = line_no_normal
        self._line_no_cur = line_no_cur
        self._cur_line_bg = cur_line_bg

        # 重新应用当前行高亮与行号
        self._highlight_current_line()
        if hasattr(self, '_line_number_area'):
            self._line_number_area.update()

        # 语法高亮配色跟随主题
        if hasattr(self, '_highlighter') and hasattr(self._highlighter, 'apply_theme'):
            try:
                self._highlighter.apply_theme(theme)
            except Exception:
                pass

    def apply_theme(self, theme: str):
        """主题切换入口（由主窗口 _reapply_all_widget_styles 调用）"""
        self._apply_editor_theme(theme if theme in ('light', 'dark') else 'dark')

    # ── 行号区域 ──

    def _update_line_number_width(self, count):
        digits = max(1, len(str(count)))
        self._line_number_area.setFixedWidth(16 + digits * 10)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self._editor.viewport().rect()):
            self._editor.setViewportMargins(
                self._line_number_area.width(), 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self._editor.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(), cr.top(),
            self._line_number_area.width(),
            cr.height() if self._error_panel.height() == 0 else cr.height())
        self._editor.setViewportMargins(
            self._line_number_area.width(), 0, 0, 0)

    # ── 当前行高亮 ──

    def _highlight_current_line(self):
        from PySide6.QtWidgets import QTextEdit
        selection = QTextEdit.ExtraSelection()
        line_color = QColor(getattr(self, '_cur_line_bg', 'rgba(255,255,255,0.08)'))
        line_color.setAlpha(120)
        selection.format.setBackground(line_color)
        selection.format.setProperty(QTextCharFormat.FullWidthSelection, True)
        selection.cursor = self._editor.textCursor()
        selection.cursor.clearSelection()
        self._editor.setExtraSelections([selection] + self._error_selections)

    # ── 文件操作 ──

    def open_file(self, file_path: str):
        """打开文件到编辑器或预览面板（支持多文件 Tab）"""
        ext = os.path.splitext(file_path)[1].lower()

        # 判断文件类型
        if ext in self.IMAGE_EXTS:
            file_type = 'image'
        elif ext in self.WEB_PREVIEW_EXTS:
            # HTML 文件既可以在编辑器中查看源码，也可以在 Web 预览中渲染
            # 默认在 Web 预览中显示，可以通过 "查看源码" 按钮切换
            file_type = 'html'
        elif ext and ext not in self.EDITABLE_EXTS:
            self._file_label.setText(f"不支持编辑: {os.path.basename(file_path)}")
            self._editor.setPlainText(f"不支持编辑此文件类型: {ext}")
            self._editor.setReadOnly(True)
            self._stack.setCurrentIndex(0)  # 显示编辑器（显示错误信息）
            return
        else:
            file_type = 'code'

        # 如果文件已经在 Tab 中，直接切换
        if file_path in self._open_files:
            self._switch_to_file(file_path)
            return

        # 保存当前文件状态
        self._save_current_state()

        # 设置新文件
        self._file_path = file_path
        self._is_modified = False

        # 根据文件类型加载
        if file_type == 'image':
            self._image_preview.load_file(file_path)
            self._stack.setCurrentIndex(1)  # 图片预览
        elif file_type == 'html':
            self._html_preview.load_file(file_path)
            self._stack.setCurrentIndex(2)  # Web 预览
        else:  # code
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(500000)
            except Exception as e:
                self._file_label.setText(f"读取失败: {os.path.basename(file_path)}")
                self._editor.setPlainText(f"读取文件失败: {e}")
                self._editor.setReadOnly(True)
                self._stack.setCurrentIndex(0)
                return

            self._orig_content = content
            self._editor.setReadOnly(False)
            self._editor.setPlainText(content)
            self._linter.check_now(file_path, content)
            self._highlight_current_line()
            self._stack.setCurrentIndex(0)  # 编辑器

        # 更新 UI
        self._file_label.setText(os.path.basename(file_path))
        self._file_label.setToolTip(file_path)
        self._modified_indicator.setText("")
        self._save_btn.setEnabled(False)
        self._fix_btn.hide()
        self._lint_status.setText("")
        self._error_panel.setFixedHeight(0)

        # 根据文件类型显示/隐藏工具栏按钮
        if file_type == 'code':
            self._save_btn.show()
        else:
            self._save_btn.hide()

        # 添加到已打开文件列表
        self._open_files[file_path] = {
            'content': content if file_type == 'code' else '',
            'orig_content': content if file_type == 'code' else '',
            'modified': False,
            'type': file_type,
        }
        self._rebuild_tab_bar()
        self.file_opened.emit(file_path)

    def close_file(self):
        """关闭当前文件"""
        # 从已打开列表中移除
        if self._file_path in self._open_files:
            del self._open_files[self._file_path]

        remaining = list(self._open_files.keys())
        if remaining:
            # 切换到下一个文件
            self._switch_to_file(remaining[-1])
        else:
            # 没有文件了
            self._file_path = ""
            self._orig_content = ""
            self._is_modified = False
            self._editor.clear()
            self._editor.setReadOnly(False)
            self._file_label.setText("未打开文件")
            self._modified_indicator.setText("")
            self._save_btn.setEnabled(False)
            self._fix_btn.hide()
            self._lint_status.setText("")
            self._error_panel.setFixedHeight(0)
            self._error_selections = []
            self._highlight_current_line()
            self._tab_bar_widget.hide()

        self._rebuild_tab_bar()

    def save_file(self):
        """保存当前文件"""
        if not self._file_path:
            return
        content = self._editor.toPlainText()
        try:
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self._orig_content = content
            self._is_modified = False
            self._modified_indicator.setText("")
            self._save_btn.setEnabled(False)
            # 保存后重新检查
            self._linter.check_now(self._file_path, content)
            self.file_saved.emit(self._file_path, content)
        except Exception as e:
            self._lint_status.setText(f"保存失败: {e}")
            self._lint_status.setStyleSheet("color: #f38ba8; font-size: 11px; background: transparent;")

    def get_content(self) -> str:
        return self._editor.toPlainText()

    def get_file_path(self) -> str:
        return self._file_path

    def is_modified(self) -> bool:
        return self._is_modified

    def has_file(self) -> bool:
        return bool(self._file_path)

    # ── 文本变更 ──

    _error_selections = []

    def _on_text_changed(self):
        self._is_modified = self._editor.toPlainText() != self._orig_content
        if self._is_modified:
            self._modified_indicator.setText("●")
            self._save_btn.setEnabled(True)
        else:
            self._modified_indicator.setText("")
            self._save_btn.setEnabled(False)
        # 防抖调度 Linter
        if self._file_path:
            self._linter.schedule_check(self._file_path, self._editor.toPlainText())

    # ── Linter 回调 ──

    def _on_errors_found(self, file_path: str, errors: list):
        if file_path != self._file_path:
            return

        # 标记错误行
        self._error_selections = []
        for err in errors:
            sel = QTextEdit.ExtraSelection()
            err_color = QColor("#f38ba8")
            err_color.setAlpha(30)
            sel.format.setBackground(err_color)
            sel.format.setProperty(QTextCharFormat.FullWidthSelection, True)
            cursor = self._editor.textCursor()
            block = self._editor.document().findBlockByNumber(err["line"] - 1)
            if block.isValid():
                cursor.setPosition(block.position())
                cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                sel.cursor = cursor
                self._error_selections.append(sel)

        self._highlight_current_line()

        # 更新 UI
        error_count = len(errors)
        self._lint_status.setText(f"⚠ {error_count} 个错误")
        self._lint_status.setStyleSheet("color: #f38ba8; font-size: 11px; background: transparent;")

        # 显示错误详情
        first_err = errors[0]
        self._error_label.setText(f"行 {first_err['line']}: {first_err['message']}")
        self._error_panel.setFixedHeight(36)

        # 显示自动修复按钮
        if file_path.lower().endswith(".py"):
            self._fix_btn.show()

    def _on_errors_cleared(self, file_path: str):
        if file_path != self._file_path:
            return
        self._error_selections = []
        self._highlight_current_line()
        self._lint_status.setText("✓ 无错误")
        self._lint_status.setStyleSheet("color: #a6e3a1; font-size: 11px; background: transparent;")
        self._error_panel.setFixedHeight(0)
        self._fix_btn.hide()

    # ── 多文件 Tab 管理 ──

    def _save_current_state(self):
        """保存当前文件编辑状态到 _open_files"""
        if self._file_path and self._file_path in self._open_files:
            info = self._open_files[self._file_path]
            if info.get('type') == 'code':
                self._open_files[self._file_path]['content'] = self._editor.toPlainText()
            self._open_files[self._file_path]['modified'] = self._is_modified

    def _switch_to_file(self, file_path: str):
        """切换到已打开的文件"""
        if file_path not in self._open_files:
            return
        # 保存当前文件状态
        self._save_current_state()

        info = self._open_files[file_path]
        file_type = info.get('type', 'code')
        self._file_path = file_path
        self._is_modified = info.get('modified', False)

        # 根据文件类型切换显示
        if file_type == 'image':
            self._image_preview.load_file(file_path)
            self._stack.setCurrentIndex(1)
        elif file_type == 'html':
            self._html_preview.load_file(file_path)
            self._stack.setCurrentIndex(2)
        else:  # code
            self._orig_content = info['orig_content']
            self._editor.setReadOnly(False)
            self._editor.setPlainText(info['content'])
            self._linter.check_now(file_path, info['content'])
            self._highlight_current_line()
            self._stack.setCurrentIndex(0)

        # 更新 UI
        self._file_label.setText(os.path.basename(file_path))
        self._file_label.setToolTip(file_path)
        self._modified_indicator.setText("●" if self._is_modified else "")
        self._save_btn.setEnabled(self._is_modified)
        self._save_btn.show() if file_type == 'code' else self._save_btn.hide()
        self._fix_btn.hide()
        self._lint_status.setText("")
        self._error_panel.setFixedHeight(0)

        self._rebuild_tab_bar()
        self.file_opened.emit(file_path)

    def open_url(self, url: str):
        """打开 Web URL 预览（用于自动启动后端场景）"""
        # 保存当前文件状态
        self._save_current_state()

        self._file_path = url
        self._is_modified = False
        self._file_label.setText(url)
        self._file_label.setToolTip(url)
        self._modified_indicator.setText("")
        self._save_btn.setEnabled(False)
        self._save_btn.hide()
        self._fix_btn.hide()
        self._lint_status.setText("")
        self._error_panel.setFixedHeight(0)

        # 加载 URL 到 Web 预览
        self._html_preview.load_url(url)
        self._stack.setCurrentIndex(2)  # Web 预览

        # 添加到已打开文件列表
        self._open_files[url] = {
            'content': '',
            'orig_content': '',
            'modified': False,
            'type': 'web',
        }
        self._rebuild_tab_bar()
        self.file_opened.emit(url)

    def _rebuild_tab_bar(self):
        """重建文件 Tab 栏"""
        # 清空现有 tabs
        while self._tab_bar_layout.count():
            item = self._tab_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._open_files:
            self._tab_bar_widget.hide()
            return

        self._tab_bar_widget.show()

        for fpath in self._open_files:
            tab = self._make_tab_widget(fpath)
            self._tab_bar_layout.addWidget(tab)

    def _make_tab_widget(self, file_path: str) -> QWidget:
        """创建单个文件 Tab"""
        is_active = (file_path == self._file_path)
        is_modified = self._open_files[file_path].get('modified', False)
        fname = os.path.basename(file_path)

        tab = QWidget()
        tab.setFixedHeight(32)
        tab.setStyleSheet(f"""
            QWidget {{
                background: {'#1e1e2e' if is_active else 'transparent'};
                border-right: 1px solid rgba(255,255,255,0.08);
            }}
        """)

        layout = QHBoxLayout(tab)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(4)

        name_label = QLabel(fname)
        name_label.setStyleSheet(f"""
            color: {'#d9dae0' if is_active else '#6c7086'};
            font-size: 12px;
            {'font-weight: bold;' if is_active else ''}
            background: transparent;
        """)
        if is_modified:
            name_label.setText("● " + fname)
            name_label.setStyleSheet(name_label.styleSheet().replace("color: #d9dae0", "color: #f9e2af").replace("color: #6c7086", "color: #f9e2af"))

        layout.addWidget(name_label)

        # 小关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6c7086; background: transparent; border: none;
                          border-radius: 8px; font-size: 9px; }
            QPushButton:hover { color: #f38ba8; background: rgba(243,139,168,0.15); }
        """)
        close_btn.clicked.connect(lambda checked, fp=file_path: self._close_tab(fp))
        layout.addWidget(close_btn)

        # 点击 Tab 切换文件
        tab.mousePressEvent = lambda event, fp=file_path: self._switch_to_file(fp)

        return tab

    def _close_tab(self, file_path: str):
        """通过 Tab 关闭指定文件"""
        if file_path not in self._open_files:
            return

        del self._open_files[file_path]

        if file_path == self._file_path:
            remaining = list(self._open_files.keys())
            if remaining:
                self._switch_to_file(remaining[-1])
            else:
                self._file_path = ""
                self._orig_content = ""
                self._is_modified = False
                self._editor.clear()
                self._file_label.setText("未打开文件")
                self._modified_indicator.setText("")
                self._save_btn.setEnabled(False)
                self._fix_btn.hide()
                self._lint_status.setText("")
                self._error_panel.setFixedHeight(0)
                self._error_selections = []
                self._highlight_current_line()
                self._tab_bar_widget.hide()
                self.file_closed.emit()

        self._rebuild_tab_bar()

    # ── 自动修复 ──

    def _on_close_button(self):
        """X 按钮点击 — 关闭文件并发射信号"""
        had_file = bool(self._file_path)
        self.close_file()
        # 如果没有文件了，发射关闭信号
        if not self._open_files and had_file:
            self.file_closed.emit()

    def _on_request_fix(self):
        """请求 Agent 自动修复当前文件的错误"""
        if not self._file_path:
            return
        content = self._editor.toPlainText()
        errors = self._linter.last_errors
        self.request_fix.emit(self._file_path, errors, content)

    def reload_from_disk(self):
        """从磁盘重新加载文件（Agent 修改后同步）"""
        if not self._file_path:
            return
        try:
            with open(self._file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(500000)
            self._orig_content = content
            self._is_modified = False
            self._editor.setPlainText(content)
            self._modified_indicator.setText("")
            self._save_btn.setEnabled(False)
            self._linter.check_now(self._file_path, content)
        except Exception:
            pass


# ── 后台任务面板 ──────────────────────────────────────────────

class BackgroundTaskPanel(QWidget):
    """后台任务面板 — 显示后台 Agent 任务列表和状态

    Signals:
        task_clicked: (task_id) — 点击任务时触发
    """

    task_clicked = Signal(str)
    _refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks = []
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)  # 每 2 秒刷新

    def _setup_ui(self):
        self.setStyleSheet("background: #1e1e2e; border-radius: 8px;")
        self.setFixedHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        header = QWidget()
        header.setFixedHeight(32)
        header.setStyleSheet("background: #3a3b43; border-radius: 8px 8px 0 0;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        icon_label = QLabel("⚙")
        icon_label.setStyleSheet("color: #6e7fe0; font-size: 14px; background: transparent;")

        title_label = QLabel("后台任务")
        title_label.setStyleSheet("color: #d9dae0; font-size: 12px; font-weight: bold; background: transparent;")

        self._count_label = QLabel("0")
        self._count_label.setStyleSheet("color: #6c7086; font-size: 11px; background: transparent;")

        clear_btn = QPushButton("清除已完成")
        clear_btn.setFixedSize(80, 22)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet("""
            QPushButton { color: #a6adc8; background: transparent; border: 1px solid #45475a;
                          border-radius: 4px; font-size: 10px; }
            QPushButton:hover { background: rgba(166,173,200,0.15); }
        """)
        clear_btn.clicked.connect(self._clear_completed)

        header_layout.addWidget(icon_label)
        header_layout.addWidget(title_label)
        header_layout.addWidget(self._count_label)
        header_layout.addStretch()
        header_layout.addWidget(clear_btn)

        # 任务列表
        from PySide6.QtWidgets import QScrollArea
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_widget)
        scroll.setStyleSheet("""
            QScrollArea { background: #1e1e2e; border: none; border-radius: 0 0 8px 8px; }
            QScrollBar:vertical { background: #1e1e2e; width: 8px; }
            QScrollBar::handle:vertical { background: #45475a; min-height: 20px; border-radius: 4px; }
        """)

        # 空状态
        self._empty_label = QLabel("无后台任务")
        self._empty_label.setStyleSheet("color: #6c7086; font-size: 11px; padding: 20px; background: transparent;")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._list_layout.insertWidget(0, self._empty_label)

        layout.addWidget(header)
        layout.addWidget(scroll)

    def refresh(self):
        """从 BackgroundAgentManager 刷新任务列表"""
        try:
            from services.core.background_agent import get_background_manager
            mgr = get_background_manager()
            self._tasks = mgr.get_all_tasks()
        except Exception:
            self._tasks = []
        self._update_ui()

    def _update_ui(self):
        """更新 UI 显示"""
        # 清空现有项
        while self._list_layout.count() > 1:  # 保留 stretch
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        active_count = sum(1 for t in self._tasks if t["status"] in ("pending", "running"))
        self._count_label.setText(f"({len(self._tasks)})")

        if not self._tasks:
            self._empty_label = QLabel("无后台任务")
            self._empty_label.setStyleSheet("color: #6c7086; font-size: 11px; padding: 20px; background: transparent;")
            self._empty_label.setAlignment(Qt.AlignCenter)
            self._list_layout.insertWidget(0, self._empty_label)
            return

        for task in self._tasks[:20]:  # 最多显示 20 个
            item_widget = self._make_task_item(task)
            self._list_layout.insertWidget(self._list_layout.count() - 1, item_widget)

    def _make_task_item(self, task: dict) -> QWidget:
        """创建单个任务条目"""
        item = QWidget()
        item.setStyleSheet("""
            QWidget { background: #181825; border-radius: 6px; border: 1px solid rgba(255,255,255,0.08); }
            QWidget:hover { border-color: #45475a; }
        """)
        item.setCursor(Qt.PointingHandCursor)
        item.mousePressEvent = lambda e, tid=task["task_id"]: self.task_clicked.emit(tid)

        layout = QVBoxLayout(item)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        # 第一行：状态图标 + 名称 + 状态文字
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        status_icon = {
            "pending": "⏳", "running": "🔄", "completed": "✅",
            "failed": "❌", "cancelled": "🚫"
        }.get(task["status"], "❓")

        icon_label = QLabel(status_icon)
        icon_label.setStyleSheet("font-size: 13px; background: transparent;")

        name_label = QLabel(task["name"])
        name_label.setStyleSheet("color: #d9dae0; font-size: 11px; font-weight: bold; background: transparent;")

        status_colors = {
            "pending": "#f9e2af", "running": "#6e7fe0", "completed": "#a6e3a1",
            "failed": "#f38ba8", "cancelled": "#6c7086"
        }
        status_label = QLabel(task["status"])
        status_label.setStyleSheet(
            f"color: {status_colors.get(task['status'], '#6c7086')}; font-size: 10px; background: transparent;")
        status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row1.addWidget(icon_label)
        row1.addWidget(name_label)
        row1.addStretch()
        row1.addWidget(status_label)
        layout.addLayout(row1)

        # 第二行：进度条
        if task["status"] == "running" or task["progress"] > 0:
            progress_bar = QWidget()
            progress_bar.setFixedHeight(4)
            progress_bar.setStyleSheet("background: rgba(255,255,255,0.08); border-radius: 2px;")
            progress_layout = QHBoxLayout(progress_bar)
            progress_layout.setContentsMargins(0, 0, 0, 0)
            progress_layout.setSpacing(0)

            fill = QWidget()
            fill.setFixedHeight(4)
            fill.setStyleSheet("background: #6e7fe0; border-radius: 2px;")
            fill.setMaximumWidth(int(progress_bar.width() * task["progress"]) if progress_bar.width() > 0 else 0)

            # 使用定时器更新宽度
            def update_fill():
                pct = task["progress"]
                fill.setFixedWidth(max(2, int(progress_bar.width() * pct)))

            QTimer.singleShot(50, update_fill)
            progress_layout.addWidget(fill)
            progress_layout.addStretch()
            layout.addWidget(progress_bar)

            # 进度文字
            if task.get("progress_message"):
                msg_label = QLabel(task["progress_message"])
                msg_label.setStyleSheet("color: #6c7086; font-size: 9px; background: transparent;")
                layout.addWidget(msg_label)

        # 第三行：耗时
        if task.get("duration", 0) > 0:
            dur_label = QLabel(f"耗时: {task['duration']:.1f}s")
            dur_label.setStyleSheet("color: #6c7086; font-size: 9px; background: transparent;")
            layout.addWidget(dur_label)

        return item

    def _clear_completed(self):
        """清除已完成的任务"""
        try:
            from services.core.background_agent import get_background_manager
            mgr = get_background_manager()
            count = mgr.clear_completed()
            self.refresh()
        except Exception:
            pass


# ── 文件预览组件 ──────────────────────────────────────────────────

class ImagePreviewWidget(QWidget):
    """图片预览组件 — 支持 png/gif/jpg/jpeg/bmp/svg/webp/ico"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._pixmap = QPixmap()
        self._scale = 1.0
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background: #1e1e2e;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet("QWidget { background: #181825; border-bottom: 1px solid rgba(255,255,255,0.08); }")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 0, 8, 0)
        tb_layout.setSpacing(8)

        self._label = QLabel("图片预览")
        self._label.setStyleSheet("color: #d9dae0; font-size: 13px; font-weight: bold; background: transparent;")
        tb_layout.addWidget(self._label)
        tb_layout.addStretch()

        # 缩放按钮
        self._zoom_in_btn = QPushButton("🔍+")
        self._zoom_in_btn.setFixedSize(28, 24)
        self._zoom_in_btn.setCursor(Qt.PointingHandCursor)
        self._zoom_in_btn.setStyleSheet(self._btn_style())
        self._zoom_in_btn.clicked.connect(lambda: self._set_scale(self._scale * 1.25))
        tb_layout.addWidget(self._zoom_in_btn)

        self._zoom_out_btn = QPushButton("🔍-")
        self._zoom_out_btn.setFixedSize(28, 24)
        self._zoom_out_btn.setCursor(Qt.PointingHandCursor)
        self._zoom_out_btn.setStyleSheet(self._btn_style())
        self._zoom_out_btn.clicked.connect(lambda: self._set_scale(self._scale / 1.25))
        tb_layout.addWidget(self._zoom_out_btn)

        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedSize(36, 24)
        self._fit_btn.setCursor(Qt.PointingHandCursor)
        self._fit_btn.setStyleSheet(self._btn_style())
        self._fit_btn.clicked.connect(self._fit_to_view)
        tb_layout.addWidget(self._fit_btn)

        self._orig_btn = QPushButton("1:1")
        self._orig_btn.setFixedSize(36, 24)
        self._orig_btn.setCursor(Qt.PointingHandCursor)
        self._orig_btn.setStyleSheet(self._btn_style())
        self._orig_btn.clicked.connect(lambda: self._set_scale(1.0))
        tb_layout.addWidget(self._orig_btn)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6c7086; background: transparent; border: none;
                          border-radius: 11px; font-size: 11px; }
            QPushButton:hover { color: #f38ba8; background: rgba(243,139,168,0.15); }
        """)
        close_btn.clicked.connect(self._on_close)
        tb_layout.addWidget(close_btn)

        layout.addWidget(toolbar)

        # 图片显示区 — 用 QScrollArea 支持滚动
        self._scroll = QScrollArea()
        self._scroll.setStyleSheet("""
            QScrollArea { background: #1e1e2e; border: none; }
            QScrollBar:vertical { background: #181825; width: 8px; }
            QScrollBar::handle:vertical { background: #45475a; border-radius: 4px; min-height: 30px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignCenter)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: transparent;")
        self._scroll.setWidget(self._image_label)

        layout.addWidget(self._scroll, 1)

        # 底部信息栏
        self._info_label = QLabel("")
        self._info_label.setFixedHeight(24)
        self._info_label.setStyleSheet("color: #6c7086; font-size: 11px; background: #181825; border-top: 1px solid rgba(255,255,255,0.08); padding-left: 12px;")
        layout.addWidget(self._info_label)

    def _btn_style(self):
        return """
            QPushButton { color: #d9dae0; background: rgba(255,255,255,0.08); border: none;
                          border-radius: 4px; font-size: 11px; }
            QPushButton:hover { background: #45475a; }
        """

    def load_file(self, file_path: str):
        """加载图片文件"""
        self._file_path = file_path
        self._pixmap = QPixmap(file_path)
        if self._pixmap.isNull():
            self._image_label.setText(f"无法加载图片: {os.path.basename(file_path)}")
            self._image_label.setStyleSheet("color: #f38ba8; font-size: 14px; background: transparent;")
            self._info_label.setText("")
            return

        self._label.setText(f"🖼 {os.path.basename(file_path)}")
        size = os.path.getsize(file_path)
        size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        self._info_label.setText(
            f"  {os.path.basename(file_path)}  |  {self._pixmap.width()} × {self._pixmap.height()} px  |  {size_str}  |  缩放: {self._scale * 100:.0f}%")
        self._fit_to_view()

    def _set_scale(self, scale: float):
        self._scale = max(0.1, min(10.0, scale))
        if self._pixmap.isNull():
            return
        scaled = self._pixmap.scaled(
            int(self._pixmap.width() * self._scale),
            int(self._pixmap.height() * self._scale),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self._image_label.setFixedSize(scaled.size())
        self._info_label.setText(
            f"  {os.path.basename(self._file_path)}  |  {self._pixmap.width()} × {self._pixmap.height()} px  |  缩放: {self._scale * 100:.0f}%")

    def _fit_to_view(self):
        if self._pixmap.isNull():
            return
        vw = self._scroll.viewport().width() - 20
        vh = self._scroll.viewport().height() - 20
        if vw <= 0 or vh <= 0:
            vw, vh = 800, 600
        sx = vw / self._pixmap.width()
        sy = vh / self._pixmap.height()
        self._set_scale(min(sx, sy, 1.0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 自动适应时不重新缩放，避免频繁重算

    file_closed = Signal()

    def _on_close(self):
        self.file_closed.emit()


class HtmlPreviewWidget(QWidget):
    """HTML/Web 预览组件 — 使用 QWebEngineView 渲染"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background: #1e1e2e;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet("QWidget { background: #181825; border-bottom: 1px solid rgba(255,255,255,0.08); }")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 0, 8, 0)
        tb_layout.setSpacing(8)

        self._label = QLabel("Web 预览")
        self._label.setStyleSheet("color: #d9dae0; font-size: 13px; font-weight: bold; background: transparent;")
        tb_layout.addWidget(self._label)
        tb_layout.addStretch()

        # 地址栏
        self._url_input = QLineEdit()
        self._url_input.setFixedHeight(24)
        self._url_input.setMinimumWidth(200)
        self._url_input.setStyleSheet("""
            QLineEdit { background: rgba(255,255,255,0.08); color: #d9dae0; border: 1px solid #45475a;
                        border-radius: 4px; padding: 0 8px; font-size: 11px; }
            QLineEdit:focus { border-color: #6e7fe0; }
        """)
        self._url_input.returnPressed.connect(self._navigate_to_url)
        tb_layout.addWidget(self._url_input, 1)

        # 刷新按钮
        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setFixedSize(28, 24)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet("""
            QPushButton { color: #d9dae0; background: rgba(255,255,255,0.08); border: none;
                          border-radius: 4px; font-size: 14px; }
            QPushButton:hover { background: #45475a; }
        """)
        self._refresh_btn.clicked.connect(self._refresh)
        tb_layout.addWidget(self._refresh_btn)

        # 外部浏览器按钮
        self._ext_btn = QPushButton("🔗")
        self._ext_btn.setFixedSize(28, 24)
        self._ext_btn.setCursor(Qt.PointingHandCursor)
        self._ext_btn.setStyleSheet("""
            QPushButton { color: #d9dae0; background: rgba(255,255,255,0.08); border: none;
                          border-radius: 4px; font-size: 14px; }
            QPushButton:hover { background: #45475a; }
        """)
        self._ext_btn.clicked.connect(self._open_external)
        tb_layout.addWidget(self._ext_btn)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { color: #6c7086; background: transparent; border: none;
                          border-radius: 11px; font-size: 11px; }
            QPushButton:hover { color: #f38ba8; background: rgba(243,139,168,0.15); }
        """)
        close_btn.clicked.connect(self._on_close)
        tb_layout.addWidget(close_btn)

        layout.addWidget(toolbar)

        # Web 引擎视图
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEngineSettings
            self._web_view = QWebEngineView()
            # 允许本地文件访问
            settings = self._web_view.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            layout.addWidget(self._web_view, 1)
        except ImportError:
            label = QLabel("⚠ QWebEngineView 不可用，请安装 PySide6-WebEngine")
            label.setStyleSheet("color: #f38ba8; font-size: 14px; padding: 20px;")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label, 1)
            self._web_view = None

    file_closed = Signal()

    def load_file(self, file_path: str):
        """加载本地 HTML 文件"""
        self._file_path = file_path
        self._label.setText(f"🌐 {os.path.basename(file_path)}")
        url = QUrl.fromLocalFile(file_path)
        self._url_input.setText(url.toString())
        if self._web_view:
            self._web_view.load(url)

    def load_url(self, url_str: str):
        """加载 URL"""
        self._file_path = url_str
        self._label.setText(f"🌐 {url_str}")
        self._url_input.setText(url_str)
        if self._web_view:
            self._web_view.load(QUrl(url_str))

    def _navigate_to_url(self):
        url = self._url_input.text().strip()
        if url:
            if not url.startswith(("http://", "https://", "file://")):
                url = "http://" + url
            self.load_url(url)

    def _refresh(self):
        if self._web_view:
            self._web_view.reload()

    def _open_external(self):
        import webbrowser
        url = self._url_input.text().strip()
        if url:
            webbrowser.open(url)

    def _on_close(self):
        self.file_closed.emit()

