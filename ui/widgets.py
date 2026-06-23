"""
自定义组件模块
包含所有可复用的UI组件
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QFrame, QListWidgetItem, QLineEdit,
                               QGraphicsDropShadowEffect, QApplication, QSizePolicy,
                               QPlainTextEdit, QComboBox, QSpinBox, QFileDialog)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject
from PySide6.QtGui import QColor, QPixmap, QPainter

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.styles import get_style


class GlassEffect(QGraphicsDropShadowEffect):
    """毛玻璃效果"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBlurRadius(22)
        self.setColor(QColor(0, 0, 0, 16))
        self.setOffset(0, 1)


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


class ChatMessageWidget(QWidget):
    """单条消息组件（带头像）"""
    stop_generation = Signal()

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
            message_container.setMaximumWidth(550)
            message_container.setStyleSheet(get_style('user_message'))
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
            avatar = self._make_avatar_label(False)
            self._raw_text = self.text
            self._is_html_mode = False
            self.message_container = QWidget()
            self.message_container.setMaximumWidth(680)
            self.message_container.setStyleSheet(get_style('ai_message'))

            # 使用轻量级阴影替代 GlassEffect，减少渲染开销
            shadow = QGraphicsDropShadowEffect(self.message_container)
            shadow.setBlurRadius(12)
            shadow.setColor(QColor(0, 0, 0, 20))
            shadow.setOffset(0, 2)
            self.message_container.setGraphicsEffect(shadow)

            message_layout = QVBoxLayout(self.message_container)

            # 耗时标签
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
            self.message_label.setStyleSheet("""
                QLabel {
                    color: #1d1d1f;
                    font-size: 14px;
                    background: transparent;
                }
            """)

            # 初始渲染
            if self.text:
                self._render_html(self.text)
            else:
                self.message_label.setText("")

            self.stop_container = QWidget()
            stop_layout = QHBoxLayout(self.stop_container)
            stop_layout.setContentsMargins(0, 8, 0, 0)

            self.stop_btn = QPushButton("停止生成")
            self.stop_btn.setFixedSize(72, 26)
            self.stop_btn.setStyleSheet(get_style('stop_btn'))
            self.stop_btn.clicked.connect(self.on_stop_clicked)

            stop_layout.addStretch()
            stop_layout.addWidget(self.stop_btn)

            self.cursor_label = QLabel("▌")
            self.cursor_label.setStyleSheet(get_style('cursor'))
            self.cursor_label.hide()

            self.cursor_timer = QTimer()
            self.cursor_timer.timeout.connect(self.toggle_cursor)

            message_layout.addWidget(self.message_label)
            message_layout.addWidget(self.cursor_label)
            message_layout.addWidget(self.stop_container)

            layout.addWidget(avatar)
            layout.addWidget(self.message_container)
            layout.addStretch()

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

    def update_text(self, text: str):
        """流式输出时更新文本（纯文本模式，保证性能）"""
        self._raw_text = text
        self.text = text
        # 流式阶段用纯文本，避免频繁 HTML 渲染卡顿
        self.message_label.setTextFormat(Qt.PlainText)
        self.message_label.setText(text)
        self._is_html_mode = False

    def _render_html(self, markdown_text: str):
        """将 markdown 渲染为带内联样式的 HTML 并显示"""
        try:
            from ui.markdown_renderer import render_markdown
            result = render_markdown(markdown_text)
            self._code_blocks = result.code_blocks
            self.message_label.setTextFormat(Qt.RichText)
            self.message_label.setText(result.html)
            self._is_html_mode = True
            # 连接复制链接点击
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    self.message_label.linkActivated.disconnect()
                except Exception:
                    pass
            self.message_label.linkActivated.connect(self._on_link_clicked)
        except Exception:
            self.message_label.setTextFormat(Qt.PlainText)
            self.message_label.setText(markdown_text)

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

    def on_streaming_finished(self):
        """流式输出完成 — 将纯文本转为富文本 HTML 渲染"""
        self.cursor_timer.stop()
        self.cursor_label.hide()
        self.stop_container.hide()
        # 最终渲染为带样式的 HTML
        if self._raw_text:
            self._render_html(self._raw_text)
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


class SessionItemWidget(QWidget):
    """会话列表项"""
    delete_clicked = Signal()

    def __init__(self, title: str, last_message_time: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.last_message_time = last_message_time
        self.title_label = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)

        # 顶部行：标题 + 删除按钮
        top_row = QWidget()
        top_row.setStyleSheet("background: transparent;")
        top_row.setFixedHeight(22)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)

        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet("""
            color: #1d1d1f;
            font-size: 13px;
            font-weight: bold;
            background: transparent;
        """)
        self.title_label.setWordWrap(False)
        self.title_label.setMaximumHeight(20)

        delete_btn = QPushButton("✕")
        delete_btn.setFixedSize(20, 20)
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setStyleSheet("""
            QPushButton {
                color: #86868b;
                background: transparent;
                border: none;
                border-radius: 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #ff3b30;
                background: rgba(255, 59, 48, 0.1);
            }
        """)
        delete_btn.clicked.connect(self.delete_clicked.emit)

        top_layout.addWidget(self.title_label)
        top_layout.addWidget(delete_btn)

        time_label = QLabel(self.last_message_time)
        time_label.setStyleSheet("color: #86868b; font-size: 11px; background: transparent;")

        layout.addWidget(top_row)
        layout.addWidget(time_label)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("background: rgba(0, 0, 0, 0.06);")
        layout.addWidget(separator)

        self.setAttribute(Qt.WA_Hover, True)
        self.setStyleSheet(get_style('session_item'))

    def set_title(self, title: str):
        """更新标题"""
        self.title = title
        if self.title_label:
            self.title_label.setText(title)

    def set_time(self, time_str: str):
        """更新时间"""
        self.last_message_time = time_str
        # 找到 time_label 并更新
        for child in self.findChildren(QLabel):
            if child != self.title_label and child.styleSheet().find("font-size: 11px") >= 0:
                child.setText(time_str)
                break


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
    """现代风格下拉选择器（美化版）"""
    currentChanged = Signal(str)

    def __init__(self, items: list = None, parent=None):
        super().__init__(parent)
        self._items = items or []
        self._current_index = 0
        self._popup = None
        self._setup_ui()

    def _setup_ui(self):
        self.setFixedHeight(38)
        self.setMinimumWidth(160)
        self.setCursor(Qt.PointingHandCursor)

        self._btn = QPushButton(self)
        self._btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                color: #1a1a2e;
                border: 1.5px solid #e5e7eb;
                border-radius: 10px;
                padding: 0 32px 0 14px;
                font-size: 13px;
                font-weight: bold;
                text-align: left;
            }
            QPushButton:hover {
                border-color: #a5b4fc;
                background: #f8f9fb;
            }
        """)
        self._btn.clicked.connect(self._toggle_popup)

        self._arrow = QLabel("▾", self._btn)
        self._arrow.setStyleSheet("""
            color: #6b7280;
            background: transparent;
            font-size: 12px;
        """)
        self._arrow.setAlignment(Qt.AlignCenter)

        self._update_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._btn.setGeometry(0, 0, self.width(), self.height())
        self._arrow.setGeometry(self.width() - 28, 0, 22, self.height())

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

        self._popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint)
        self._popup.setAttribute(Qt.WA_TranslucentBackground)

        container = QWidget(self._popup)
        container.setStyleSheet("""
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
        """)
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(1)

        for i, item in enumerate(self._items):
            is_sel = (i == self._current_index)
            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            row.setFixedHeight(36)

            if is_sel:
                row.setStyleSheet("""
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #6366f1, stop:1 #8b5cf6);
                    border-radius: 6px;
                """)
            else:
                row.setStyleSheet("background: transparent; border-radius: 6px;")

            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 0, 10, 0)
            row_layout.setSpacing(8)

            name_label = QLabel(item)
            if is_sel:
                name_label.setStyleSheet(
                    "color: white; font-size: 13px; font-weight: bold; background: transparent;")
            else:
                name_label.setStyleSheet(
                    "color: #1a1a2e; font-size: 13px; background: transparent;")

            row_layout.addWidget(name_label, 1)

            if is_sel:
                check = QLabel("✓")
                check.setStyleSheet(
                    "color: white; font-size: 14px; font-weight: bold; background: transparent;")
                row_layout.addWidget(check)

            idx = i
            row.mousePressEvent = lambda e, _idx=idx: self._on_item_clicked(_idx)

            if not is_sel:
                def _enter(e, w=row):
                    w.setStyleSheet("background: #f3f4f6; border-radius: 6px;")
                def _leave(e, w=row):
                    w.setStyleSheet("background: transparent; border-radius: 6px;")
                row.enterEvent = _enter
                row.leaveEvent = _leave

            layout.addWidget(row)

        # 尺寸
        popup_w = max(self.width() + 30, 200)
        popup_h = len(self._items) * 37 + 12
        container.setGeometry(0, 0, popup_w, popup_h)
        self._popup.setFixedSize(popup_w, popup_h)

        # 向上展开：定位到按钮上方
        btn_pos = self._btn.mapToGlobal(self._btn.rect().topLeft())
        self._popup.move(btn_pos.x() - 8, btn_pos.y() - popup_h - 4)

        self._popup.show()

    def _on_item_clicked(self, index: int):
        self._current_index = index
        self._update_text()
        if self._popup:
            self._popup.close()
        self.currentChanged.emit(self.currentText())


class CollapsibleThinking(QWidget):
    """可折叠的 Agent 思考过程展示组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏（可点击折叠/展开）
        self._header = QWidget()
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFixedHeight(32)
        self._header.setStyleSheet("""
            QWidget {
                background: rgba(99, 102, 241, 0.08);
                border-radius: 6px;
            }
            QWidget:hover {
                background: rgba(99, 102, 241, 0.14);
            }
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(10, 0, 10, 0)

        self._arrow_label = QLabel("▾")
        self._arrow_label.setStyleSheet(
            "color: #6366f1; font-size: 12px; background: transparent;")

        self._title_label = QLabel("🧠 思考过程")
        self._title_label.setStyleSheet(
            "color: #6366f1; font-size: 12px; font-weight: bold; background: transparent;")

        self._status_label = QLabel("思考中...")
        self._status_label.setStyleSheet(
            "color: #a855f7; font-size: 11px; background: transparent;")

        header_layout.addWidget(self._arrow_label)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._status_label)

        self._header.mousePressEvent = lambda e: self._toggle()

        # 内容区
        self._content = QLabel()
        self._content.setWordWrap(True)
        self._content.setTextFormat(Qt.RichText)
        self._content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._content.setStyleSheet("""
            QLabel {
                background: #1e1e2e;
                border: 1px solid #313244;
                border-top: none;
                border-radius: 0 0 6px 6px;
                padding: 10px 12px;
                color: #cdd6f4;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        layout.addWidget(self._header)
        layout.addWidget(self._content)

    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._content.hide()
            self._arrow_label.setText("▸")
        else:
            self._content.show()
            self._arrow_label.setText("▾")

    def collapse(self):
        """自动折叠"""
        if not self._collapsed:
            self._toggle()

    def expand(self):
        """自动展开"""
        if self._collapsed:
            self._toggle()

    def append_thinking(self, text: str):
        """追加思考内容（流式更新）"""
        import html as html_module
        if not hasattr(self, '_thinking_buffer'):
            self._thinking_buffer = []
        self._thinking_buffer.append(text)
        # 限制缓冲区大小，防止内存无限增长
        if len(self._thinking_buffer) > 100:
            self._thinking_buffer = self._thinking_buffer[-80:]
        # 批量渲染
        safe_texts = [html_module.escape(t).replace("\n", "<br>") for t in self._thinking_buffer]
        formatted = '<br><br>'.join(
            f'<span style="color: #cdd6f4; font-family: Consolas, monospace; font-size: 12px;">{t}</span>'
            for t in safe_texts
        )
        self._content.setText(formatted)

    def set_status(self, status: str):
        """设置状态文字"""
        self._status_label.setText(status)

    def set_final(self):
        """设置为完成状态"""
        self.set_status("✅ 完成")
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
        self._header.setStyleSheet("background: #2a2a3d; border-radius: 8px 8px 0 0;")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        icon_label = QLabel(">")
        icon_label.setStyleSheet(
            "color: #a6adc8; font-size: 14px; font-weight: bold; font-family: Consolas, monospace; background: transparent;")

        title_label = QLabel("终端")
        title_label.setStyleSheet(
            "color: #cdd6f4; font-size: 12px; font-weight: bold; background: transparent;")

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
            QLabel { background: #1e1e2e; color: #cdd6f4; font-family: Consolas, "Courier New", monospace;
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

    def append_output(self, output: str):
        import html as html_module
        safe_output = html_module.escape(output)
        current = self._content.text()
        output_html = f'<div style="color: #bac2de; margin: 2px 0 2px 16px;">{safe_output}</div>'
        self._content.setText(current + output_html)

    def append_error(self, error: str):
        import html as html_module
        safe_error = html_module.escape(error)
        current = self._content.text()
        error_html = f'<div style="color: #f38ba8; margin: 2px 0 2px 16px;">[错误] {safe_error}</div>'
        self._content.setText(current + error_html)

    def clear(self):
        self._content.setText('<span style="color: #6c7086;">等待命令执行...</span>')


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
            # 叠加一层半透明白色，让文字可读
            painter.fillRect(self.rect(), QColor(240, 242, 245, int((1 - self._bg_opacity) * 255 * 0.85)))

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
            QComboBox { background: #f8f9fb; border: 1px solid #e5e7eb; border-radius: 6px;
                        padding: 4px 8px; font-size: 12px; color: #1a1a2e; }
            QComboBox:hover { border-color: #a5b4fc; }
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
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
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
        """显示生成结果"""
        self._result_url = image_url
        self.status_label.setText("生成完成！")
        self.status_label.setStyleSheet("color: #22c55e; font-size: 12px; background: transparent;")
        self.result_label.setText("正在加载图片...")
        # 异步下载图片
        from PySide6.QtCore import QThread
        self._download_thread = QThread()
        from services.image_service import ImageWorker
        # 直接用 requests 下载
        import requests as _req
        class _Downloader(QObject):
            finished = Signal(QPixmap)
            error = Signal(str)
            def __init__(self, url):
                super().__init__()
                self.url = url
            def run(self):
                try:
                    resp = _req.get(self.url, timeout=30)
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


# ──────────────────────────────────────────
# 代码语法高亮器（Catppuccin Mocha 深色主题）
# ──────────────────────────────────────────
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QFont


class CodeHighlighter(QSyntaxHighlighter):
    """通用代码语法高亮器，支持 Python / JS / C / Java 等"""

    # Catppuccin Mocha 配色
    COLORS = {
        'keyword':   '#cba6f7',  # 紫色
        'builtin':   '#f9e2af',  # 黄色
        'string':    '#a6e3a1',  # 绿色
        'comment':   '#6c7086',  # 灰色
        'number':    '#fab387',  # 橙色
        'decorator': '#f38ba8',  # 粉色
        'function':  '#89b4fa',  # 蓝色
        'class':     '#f9e2af',  # 黄色
        'operator':  '#89dceb',  # 青色
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = []
        self._build_rules()

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

