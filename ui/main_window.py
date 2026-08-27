"""
主窗口模块
包含MainWindow类及其所有UI布局和交互逻辑
"""

import os
import time
import threading
from datetime import datetime

from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QSplitter, QLabel, QPushButton, QLineEdit,
                               QScrollArea, QFrame, QSlider, QComboBox,
                               QListWidget, QListWidgetItem, QSizePolicy,
                               QMessageBox, QStyleFactory, QStyledItemDelegate,
                               QFileDialog, QApplication, QProgressBar,
                               QTreeView, QFileSystemModel, QSplitter,
                               QPlainTextEdit)
from PySide6.QtCore import (Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve,
                             QThread, Signal, QObject, QModelIndex, QEvent)
from PySide6.QtGui import QIcon, QGuiApplication, QColor, QPalette, QPixmap, QImage

import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.styles import get_style


# Windows 无边框窗口：WM_GETMINMAXINFO / WM_NCHITTEST 所需结构（仅 win32 生效）
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Pt(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _Rct(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rct),
                    ("rcWork", _Rct), ("dwFlags", ctypes.c_ulong)]

    class _MinMaxInfo(ctypes.Structure):
        _fields_ = [("ptReserved", _Pt), ("ptMaxSize", _Pt),
                    ("ptMaxPosition", _Pt), ("ptMinTrackSize", _Pt),
                    ("ptMaxTrackSize", _Pt)]
from ui.widgets import (GlassEffect, ChatMessageWidget, SessionItemWidget,
                         ModelCardWidget, ToastWidget, ModernDropdown, TerminalWidget,
                         BackgroundWidget, ImageGeneratorWidget, VideoGeneratorWidget,
                         VideoPlayerWidget, CodeHighlighter, TaskProgressWidget, ToolStatusWidget, FileChangesPanel,
                         CollapsibleThinking, TaskPlanWidget, CodeFeedbackWidget,
                         DiffViewWidget, CodeEditorPanel, ToolCallCard,
                         DiffGroupWidget, MessageActionBar, BackgroundTaskPanel,
                         TitleBarButton)
from ui.settings_dialog import SettingsDialog
from services.core.api_service import get_model_display_names, find_model_by_display, is_chatgpt_model
from services.core.chat_service import ChatService
from services.core.storage_service import StorageService
from services.image_service import ImageWorker, upload_image_to_base64
from services.utils.rollback_service import RollbackManager
from services.tools import set_rollback_manager, set_diff_callback, set_preview_url_callback


class FileOperationSignal(QObject):
    """文件操作信号（用于跨线程更新UI）"""
    operation = Signal(str, str, int, int)  # operation_type, file_path, added, removed


class ChatGPTLoginWorker(QObject):
    finished = Signal()
    error = Signal(str)
    status_log = Signal(str)

    def run(self):
        try:
            from services.providers.chatgpt_service import open_browser_for_manual_login
            open_browser_for_manual_login(status_callback=lambda msg: self.status_log.emit(msg + "\n"))
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class ApiWorker(QObject):
    """API 调用工作线程（支持普通对话和 Agent 模式）"""
    chunk_ready = Signal(str)
    chunk_clear = Signal()  # 清除已流式输出的内容（浏览器模型工具调用场景）
    finished = Signal()
    error = Signal(str)
    agent_step = Signal(str)
    agent_thinking = Signal(str)
    agent_done = Signal()
    status_log = Signal(str)
    code_event = Signal(str, str, bool, str)  # event_type, file_path, ok, detail
    # 结构化工具调用信号: tool_name, tool_input, tool_output, ok
    tool_call = Signal(str, str, str, bool)
    # 思考文本信号
    thought = Signal(str)
    # 计划更新信号: action, steps_data, step_index
    plan_update = Signal(str, dict, int)
    # 工具开始执行信号: tool_name, file_path (用于显示转圈)
    tool_start = Signal(str, str)
    # Agent 状态信号: tool_name, action (用于状态行显示)
    agent_status = Signal(str, str)

    def __init__(self, chat_service, session_id, user_message,
                 model_display, temperature, max_tokens, max_steps=10,
                 workspace_path=None, parent=None):
        super().__init__(parent)
        self.chat_service = chat_service
        self.session_id = session_id
        self.user_message = user_message
        self.model_display = model_display
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.workspace_path = workspace_path
        self._stop = False
        self._stop_event = threading.Event()

    def request_stop(self):
        self._stop = True
        self._stop_event.set()

    def _status_callback(self, message: str):
        if not message:
            return
        self.status_log.emit(message + "\n")

    def run(self):
        status_callback = self._status_callback
        try:
            if self.chat_service.agent_mode:
                for event in self.chat_service.send_agent_message_stream(
                    session_id=self.session_id,
                    user_message=self.user_message,
                    model_display=self.model_display,
                    max_steps=self.max_steps,
                    status_callback=status_callback,
                    workspace_path=self.workspace_path,
                    stop_event=self._stop_event,
                ):
                    if self._stop:
                        self.agent_done.emit()
                        self.chunk_ready.emit("\n\n⏹ *已停止*")
                        break
                    if event["type"] == "thinking":
                        self.agent_thinking.emit(event["output"])
                    elif event["type"] == "thought":
                        # 使用结构化 thought 信号
                        self.thought.emit(event['output'])
                        # 同时保留旧行为以兼容
                        self.agent_step.emit(f"💭 {event['output']}")
                    elif event["type"] == "step":
                        # 结构化工具调用信号
                        tool_name = event.get('tool', 'unknown')
                        tool_input = str(event.get('input', ''))
                        tool_output = str(event.get('output', ''))[:500]
                        self.tool_call.emit(tool_name, tool_input, tool_output, True)
                        # 兼容旧行为
                        step_text = (
                            f"🔧 **{event['tool']}**\n"
                            f"输入: `{event['input']}`\n"
                            f"结果: {event['output'][:200]}"
                        )
                        self.agent_step.emit(step_text)
                    elif event["type"] == "plan":
                        # RePlan 模式：计划事件
                        self.plan_update.emit(
                            event.get("action", ""),
                            event,
                            event.get("step_index", -1),
                        )
                    elif event["type"] == "code_event":
                        detail = event.get("detail", "") or event.get("output", "") or event.get("result", "")
                        self.code_event.emit(
                            event.get("event", "syntax_check"),
                            event.get("file", ""),
                            event.get("ok", False),
                            detail,
                        )
                    elif event["type"] == "tool_start":
                        # 工具开始执行 — 通知 UI 显示转圈
                        self.tool_start.emit(
                            event.get("tool", ""),
                            event.get("file_path", ""),
                        )
                    elif event["type"] == "agent_status":
                        # Agent 状态更新 — 通知 UI 状态行
                        self.agent_status.emit(
                            event.get("tool", ""),
                            event.get("action", ""),
                        )
                    elif event["type"] == "result_chunk":
                        # 浏览器模型流式输出：逐块发送到 UI
                        self.chunk_ready.emit(event["output"])
                    elif event["type"] == "result_clear":
                        # 浏览器模型：清除已流式输出的内容（工具调用场景）
                        self.chunk_clear.emit()
                    elif event["type"] == "result_done":
                        # 浏览器模型：流式输出已完成，内容已在 UI 中
                        self.agent_done.emit()
                    elif event["type"] == "result":
                        self.agent_done.emit()
                        self.chunk_ready.emit(event["output"])
                    elif event["type"] == "error":
                        self.agent_done.emit()
                        self.error.emit(event["output"])
                if not self._stop:
                    self.agent_done.emit()
            else:
                for chunk in self.chat_service.send_message_stream(
                    session_id=self.session_id,
                    user_message=self.user_message,
                    model_display=self.model_display,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    status_callback=status_callback,
                ):
                    if self._stop:
                        break
                    self.chunk_ready.emit(chunk)
            self.finished.emit()
        except Exception as e:
            if not self._stop:
                self.error.emit(str(e))
            self.finished.emit()


class MainWindow(QMainWindow):
    """主窗口类"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sky Code")
        self.setMinimumSize(900, 600)
        # 自绘无边框标题栏：移除系统标题栏，由应用内顶部栏接管拖拽/最小化/最大化/关闭
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self._frameless = True
        self._edge_resize = 6  # 边缘缩放触发宽度(px)
        # 对话服务（LangChain 记忆管理）
        self.chat_service = ChatService(window_size=20)
        # 持久化存储服务（SQLite）
        self.storage = StorageService()
        # 会话存储：{session_id: {title, time, messages, list_item}}
        self.sessions: dict = {}
        self.current_session_id: str = None
        self._session_counter = 0
        # 背景相关属性
        self.background_image = ""
        self.background_opacity = 0.3
        self.current_theme = 'light'  # 'light' or 'dark'
        # 文件操作信号（用于跨线程更新UI）
        self._file_op_signal = FileOperationSignal()
        self._file_op_signal.operation.connect(self._on_file_operation)
        # 回滚管理器
        self._rollback_mgr = RollbackManager()
        self._rollback_mgr.set_file_operation_callback(self._emit_file_operation)
        set_rollback_manager(self._rollback_mgr)
        # 代码操作反馈回调（写入 tools 模块供语法检查/执行时回调）
        from services.tools.file_tools import set_code_feedback_callback
        set_code_feedback_callback(self._on_code_feedback_from_tool)
        # Diff 回调（edit_file 工具修改文件时，在聊天框中显示 diff 视图）
        set_diff_callback(self._on_diff_from_tool)
        # Preview URL 回调（preview_in_browser 工具调用）
        set_preview_url_callback(lambda url: self.code_editor_panel.preview_url_requested.emit(url))
        # 当前轮次 turn_id（用于回滚按钮）
        self._current_turn_id = 0
        self._last_user_msg_wrapper = None
        self._last_user_turn_id = 0
        self._api_message_widget = None
        self._current_file_panel = None
        # 布局模式: 'split' = 中间代码编辑器 + 右侧聊天, 'full_chat' = 全聊天
        self._layout_mode = 'full_chat'
        # 加载配置
        self._load_config()
        self.setup_ui()
        self.setup_style()
        # 初始化完成后应用主题样式（确保所有组件使用正确的主题颜色）
        self._reapply_all_widget_styles()
        self.center_on_screen()
        # 应用背景
        self._apply_background()
        # 加载保存的头像
        self._load_saved_avatar()
        # UI 创建完成后再加载历史会话
        self._load_sessions_from_db()


    def _apply_theme(self, theme: str):
        """Apply theme to the entire application"""
        self.current_theme = theme
        # 标记正在切换主题，防止 _apply_accent_color 重复刷新
        self._theme_applying = True
        # 应用强调色（如果有）— 设置 styles 模块中的颜色
        self._apply_accent_color(getattr(self, 'accent_color', '#6e7fe0'))
        # 重新应用所有样式
        self.setup_style()
        self._reapply_all_widget_styles()
        # 切换系统原生标题栏深浅（最上方含关闭按钮的那条栏）
        self._apply_window_frame_theme(theme)
        del self._theme_applying
        # 保存偏好
        try:
            import json
            config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
            os.makedirs(config_dir, exist_ok=True)
            config_path = os.path.join(config_dir, "ui_config.json")
            ui_config = {"theme": theme, "background_image": self.background_image,
                         "background_opacity": self.background_opacity,
                         "accent_color": getattr(self, 'accent_color', '#6e7fe0')}
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(ui_config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _apply_window_frame_theme(self, theme: str):
        """刷新自绘标题栏窗口控制按钮的颜色（替代系统标题栏 DWM 方案）。

        该环境 PySide6 6.9.1 已移除 QtWin / QWindowsWindowFunctions，且此前
        ctypes 调用 DwmSetWindowAttribute 在部分系统上无效，故改为自绘无边框
        标题栏：由应用内顶部栏负责拖拽与窗口控制，颜色完全随主题切换。
        """
        for btn in getattr(self, '_titlebar_buttons', []):
            try:
                btn.set_theme(theme)
            except Exception:
                pass

    def _install_titlebar_events(self, top_bar):
        """为自绘标题栏（顶部导航栏）安装鼠标事件过滤器，实现拖拽移动/双击最大化。

        事件过滤器统一安装到标题栏及其所有子控件；QPushButton 等可点击控件
        不参与拖拽（事件原样返回），其余区域（品牌、状态、空白）可拖动窗口。
        """
        top_bar.installEventFilter(self)
        for w in top_bar.findChildren(QWidget):
            try:
                w.installEventFilter(self)
            except Exception:
                pass

    def _toggle_maximize(self):
        """切换最大化 / 还原"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def nativeEvent(self, eventType, message):
        """Windows 原生消息：无边框窗口的边缘缩放 + 最大化限制在工作区"""
        if sys.platform == "win32" and getattr(self, '_frameless', False):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST — 边缘缩放
                    lp = int(msg.lParam)
                    sx = lp & 0xFFFF
                    if sx >= 0x8000:
                        sx -= 0x10000
                    sy = (lp >> 16) & 0xFFFF
                    if sy >= 0x8000:
                        sy -= 0x10000
                    if not self.isMaximized():
                        # lParam 为物理像素屏幕坐标，必须用 GetWindowRect（物理像素）
                        # 比较，否则 DPI 缩放下窗口内部会被误判为边缘
                        rc = wintypes.RECT()
                        ctypes.windll.user32.GetWindowRect(
                            int(self.winId()), ctypes.byref(rc))
                        b = self._edge_resize * self.devicePixelRatioF()
                        left, top = sx - rc.left, sy - rc.top
                        right, bottom = rc.right - sx, rc.bottom - sy
                        if left <= b and top <= b:
                            return True, 13   # HTTOPLEFT
                        if right <= b and top <= b:
                            return True, 14   # HTTOPRIGHT
                        if left <= b and bottom <= b:
                            return True, 16   # HTBOTTOMLEFT
                        if right <= b and bottom <= b:
                            return True, 17   # HTBOTTOMRIGHT
                        if top <= b:
                            return True, 12   # HTTOP
                        if bottom <= b:
                            return True, 15   # HTBOTTOM
                        if left <= b:
                            return True, 10   # HTLEFT
                        if right <= b:
                            return True, 11   # HTRIGHT
                    return True, 1            # HTCLIENT
                if msg.message == 0x0024:  # WM_GETMINMAXINFO — 最大化不盖任务栏
                    mmi = _MinMaxInfo.from_address(int(msg.lParam))
                    hwnd = int(self.winId())
                    monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
                    mi = _MonitorInfo()
                    mi.cbSize = ctypes.sizeof(_MonitorInfo)
                    ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi))
                    work = mi.rcWork
                    mmi.ptMaxPosition.x = work.left
                    mmi.ptMaxPosition.y = work.top
                    mmi.ptMaxSize.x = work.right - work.left
                    mmi.ptMaxSize.y = work.bottom - work.top
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def changeEvent(self, event):
        """窗口状态变化：同步最大化/还原按钮图标"""
        if event.type() == QEvent.Type.WindowStateChange:
            maximized = self.isMaximized()
            for btn in getattr(self, '_titlebar_buttons', []):
                if getattr(btn, 'kind', '') == 'max':
                    try:
                        btn.set_maximized_state(maximized)
                    except Exception:
                        pass
        super().changeEvent(event)

    def _apply_accent_color(self, color: str):
        """应用强调色到全局样式"""
        self.accent_color = color
        # 更新 styles 模块中的强调色
        from ui import styles as _styles
        _styles.set_accent_color(color, self.current_theme)
        # 如果主题没变只刷新样式
        if not hasattr(self, '_theme_applying'):
            self.setup_style()
            self._reapply_all_widget_styles()
            # 保存 accent_color 到 ui_config.json
            try:
                import json
                config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
                config_path = os.path.join(config_dir, "ui_config.json")
                existing = {}
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8-sig") as f:
                        existing = json.load(f)
                existing['accent_color'] = color
                existing['theme'] = self.current_theme
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def _reapply_all_widget_styles(self):
        """重新应用所有子组件的样式（主题切换时调用）"""
        from ui.styles import get_style as _gs
        def gs(name): return _gs(name, self.current_theme)

        # 主窗口
        self.setStyleSheet(gs('main_window'))

        # 顶部导航栏（背景 + 子控件颜色统一随主题刷新）
        if hasattr(self, '_apply_top_nav_theme'):
            try:
                self._apply_top_nav_theme(self.current_theme)
            except Exception:
                pass
        elif hasattr(self, '_top_bar'):
            self._top_bar.setStyleSheet(gs('top_nav'))

        # 左侧面板
        if hasattr(self, '_left_panel'):
            self._left_panel.setStyleSheet(gs('left_panel'))

        # 左侧 Tab 栏
        if hasattr(self, '_left_tab_bar'):
            self._left_tab_bar.setStyleSheet(gs('left_tab_bar'))

        # 搜索栏
        if hasattr(self, 'search_bar'):
            self.search_bar.setStyleSheet(gs('search_bar'))

        # 会话列表
        if hasattr(self, 'session_list'):
            self.session_list.setStyleSheet(gs('session_list'))
            self.session_list.verticalScrollBar().setStyleSheet(gs('scrollbar_light'))
            # 刷新每个会话项的文字颜色，避免主题切换后仍为旧色（深背景下看不见）
            for i in range(self.session_list.count()):
                item = self.session_list.item(i)
                w = self.session_list.itemWidget(item) if item else None
                if w is not None and hasattr(w, 'apply_theme'):
                    try:
                        w.apply_theme(self.current_theme)
                    except Exception:
                        pass

        # 中间对话区
        if hasattr(self, '_middle_widget'):
            self._middle_widget.setStyleSheet(gs('middle'))

        # 聊天滚动区
        if hasattr(self, 'chat_scroll'):
            self.chat_scroll.setStyleSheet(gs('chat_scroll'))
            self.chat_scroll.verticalScrollBar().setStyleSheet(gs('scrollbar_chat'))
            # 显式设置 viewport 背景，否则 QScrollArea viewport 会保持亮色
            if self.current_theme == 'dark':
                self.chat_scroll.viewport().setStyleSheet("background: #27282e;")
            else:
                self.chat_scroll.viewport().setStyleSheet("background: #f4f5f7;")

        # 聊天消息容器
        if hasattr(self, 'chat_container'):
            if self.current_theme == 'dark':
                self.chat_container.setStyleSheet("background: #27282e;")
            else:
                self.chat_container.setStyleSheet("background: #f0f2f5;")

        # 输入框
        if hasattr(self, 'message_input'):
            self.message_input.setStyleSheet(gs('message_input'))

        # 输入容器背景
        if hasattr(self, '_input_container'):
            if self.current_theme == 'dark':
                self._input_container.setStyleSheet(
                    "background: #27282e; border-top: 1px solid rgba(255,255,255,0.06);")
            else:
                self._input_container.setStyleSheet(
                    "background: #f4f5f7; border-top: 1px solid rgba(0, 0, 0, 0.05);")

        # 新建会话按钮
        if hasattr(self, 'new_session_btn'):
            self.new_session_btn.setStyleSheet(gs('new_session_btn'))

        # 模型选择下拉框（按主题刷新深浅配色）
        if hasattr(self, 'model_combo') and hasattr(self.model_combo, 'apply_theme'):
            try:
                self.model_combo.apply_theme(self.current_theme)
            except Exception:
                pass

        # 上传按钮
        if hasattr(self, 'upload_btn'):
            self.upload_btn.setStyleSheet(gs('voice_btn'))

        # 发送按钮（仅在非生成状态时更新，避免覆盖停止按钮的红色样式）
        if hasattr(self, 'send_btn') and not getattr(self, '_is_generating', False):
            self.send_btn.setStyleSheet(gs('send_btn'))

        # 已有聊天消息 — 统一刷新主题（文字颜色 + markdown 重新渲染）
        if hasattr(self, 'chat_layout'):
            user_msg_style = gs('user_message')
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                if not (item and item.widget()):
                    continue
                w = item.widget()
                # 用户消息被包在 wrapper 里，需要先找到内部的 ChatMessageWidget
                msg_widget = w
                if not hasattr(w, 'is_user') and hasattr(w, 'layout'):
                    for j in range(w.layout().count()):
                        sub = w.layout().itemAt(j)
                        if sub and sub.widget() and hasattr(sub.widget(), 'is_user'):
                            msg_widget = sub.widget()
                            break
                if not hasattr(msg_widget, 'is_user'):
                    continue
                # 刷新名称、正文颜色、markdown、耗时标签、状态块
                if hasattr(msg_widget, 'apply_theme'):
                    msg_widget.apply_theme(self.current_theme)
                # 用户消息气泡背景（渐变）
                if msg_widget.is_user and hasattr(msg_widget, 'layout'):
                    for j in range(msg_widget.layout().count()):
                        sub = msg_widget.layout().itemAt(j)
                        if sub and sub.widget() and hasattr(sub.widget(), 'setStyleSheet'):
                            child = sub.widget()
                            # 排除头像（QLabel 有 text 和 pixmap）和时间标签
                            if not (hasattr(child, 'text') and hasattr(child, 'pixmap')):
                                try:
                                    child.setStyleSheet(user_msg_style)
                                except Exception:
                                    pass

        # 思考面板（独立挂在 chat_layout 上的 CollapsibleThinking）
        if hasattr(self, '_thinking_widget') and hasattr(self._thinking_widget, 'apply_theme'):
            try:
                self._thinking_widget.apply_theme(self.current_theme)
            except Exception:
                pass

        # 文件树
        if hasattr(self, 'file_tree'):
            self.file_tree.setStyleSheet(gs('file_tree_enhanced'))
            self.file_tree.viewport().setStyleSheet("background: transparent;")
            self.file_tree.verticalScrollBar().setStyleSheet(
                gs('scrollbar_dark') if self.current_theme == 'dark' else gs('scrollbar_light'))

        # 文件浏览器面板（代码编辑器）—— 背景跟随主题，深色用深灰而非纯黑
        if hasattr(self, 'file_viewer_panel') and hasattr(self.file_viewer_panel, 'apply_theme'):
            try:
                self.file_viewer_panel.apply_theme(self.current_theme)
            except Exception:
                pass
        elif hasattr(self, 'file_viewer_panel'):
            self.file_viewer_panel.setStyleSheet(gs('right_panel') if self.current_theme == 'dark' else
                "background: #ffffff; border: 1px solid rgba(0, 0, 0, 0.05); border-radius: 10px;")

        # 如果有背景图，需要重新应用透明度
        self._apply_background()
        # 启动时就让系统原生标题栏跟随当前主题
        self._apply_window_frame_theme(self.current_theme)

    def _get_style(self, style_name: str) -> str:
        """Get style for current theme"""
        from ui.styles import get_style
        return get_style(style_name, self.current_theme)

    def setup_ui(self):
        """设置UI布局"""
        central_widget = BackgroundWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 顶部导航栏
        self.setup_top_nav_bar(main_layout)

        # 主内容区域
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(0)

        self.setup_left_session_panel(splitter)
        self.setup_middle_chat_area(splitter)
        self.setup_right_control_panel()

        splitter.setSizes([220, 780])
        splitter.setStretchFactor(0, 0)  # left panel: fixed
        splitter.setStretchFactor(1, 1)  # chat area: stretch

        content_layout.addWidget(splitter)
        main_layout.addWidget(content_widget)
        # 底部状态栏
        self.status_bar = QWidget()
        self.status_bar.setFixedHeight(28)
        if self.current_theme == 'dark':
            self.status_bar.setStyleSheet("background: #27282e; border-top: 1px solid rgba(255,255,255,0.06);")
        else:
            self.status_bar.setStyleSheet("background: rgba(0,0,0,0.03); border-top: 1px solid rgba(0,0,0,0.05);")
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(16, 0, 16, 0)
        self.status_label = QLabel("Ready")
        if self.current_theme == 'dark':
            self.status_label.setStyleSheet("color: #6f7178; font-size: 11px;")
        else:
            self.status_label.setStyleSheet("color: #86868b; font-size: 11px;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        main_layout.addWidget(self.status_bar)


    def setup_top_nav_bar(self, parent_layout):
        """设置顶部导航栏 (DeepSeek风格 - 随主题切换深浅)"""
        top_bar = QWidget()
        self._top_bar = top_bar  # 保存引用以便主题切换时刷新
        top_bar.setFixedHeight(48)

        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)
        top_layout.setSpacing(12)

        # ── 左侧：品牌 + 状态 ──
        brand_container = QWidget()
        brand_container.setStyleSheet("background: transparent;")
        brand_layout = QHBoxLayout(brand_container)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(10)

        brand_icon = QLabel("✦")
        self._brand_icon = brand_icon
        brand_label = QLabel("Sky Code")
        self._brand_label = brand_label

        status_container = QWidget()
        self._status_container = status_container
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(8, 3, 10, 3)
        status_layout.setSpacing(5)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label = QLabel("在线")
        self._status_label = self.status_label
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)

        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand_label)
        brand_layout.addWidget(status_container)

        # ── 右侧：功能按钮（精简） ──
        btn_container = QWidget()
        if self.current_theme == 'dark':
            btn_container.setStyleSheet("background: transparent;")
        else:
            btn_container.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        # 深色主题按钮样式
        clear_btn = QPushButton("清空")
        clear_btn.setFixedSize(60, 30)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self.new_chat)

        open_folder_btn = QPushButton("文件")
        open_folder_btn.setFixedSize(60, 30)
        open_folder_btn.setCursor(Qt.PointingHandCursor)
        open_folder_btn.clicked.connect(self._open_folder)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(32, 32)
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setToolTip("设置")
        settings_btn.clicked.connect(self.show_settings)

        # 头像按钮（显示自定义头像或默认图标）
        self.avatar_btn = QPushButton()
        self.avatar_btn.setFixedSize(36, 36)
        self.avatar_btn.setCursor(Qt.PointingHandCursor)
        self.avatar_btn.setToolTip("上传头像")
        self._update_avatar_btn_icon()
        self.avatar_btn.clicked.connect(self._upload_avatar)

        btn_layout.addWidget(clear_btn)
        btn_layout.addWidget(open_folder_btn)
        # AI 画图按钮
        img_gen_btn = QPushButton("画图")
        img_gen_btn.setFixedSize(60, 30)
        img_gen_btn.setCursor(Qt.PointingHandCursor)
        img_gen_btn.clicked.connect(self._toggle_image_panel)
        btn_layout.addWidget(img_gen_btn)

        # AI 视频按钮
        video_gen_btn = QPushButton("视频")
        video_gen_btn.setFixedSize(60, 30)
        video_gen_btn.setCursor(Qt.PointingHandCursor)
        video_gen_btn.clicked.connect(self._toggle_video_panel)
        btn_layout.addWidget(video_gen_btn)
        btn_layout.addWidget(settings_btn)
        btn_layout.addWidget(self.avatar_btn)

        # ── 窗口控制按钮（自绘无边框标题栏：最小化 / 最大化 / 关闭） ──
        win_btn_container = QWidget()
        win_btn_container.setStyleSheet("background: transparent;")
        win_btn_layout = QHBoxLayout(win_btn_container)
        win_btn_layout.setContentsMargins(6, 0, 0, 0)
        win_btn_layout.setSpacing(0)

        self._min_btn = TitleBarButton('min')
        self._max_btn = TitleBarButton('max')
        self._close_btn = TitleBarButton('close')
        self._titlebar_buttons = [self._min_btn, self._max_btn, self._close_btn]

        self._min_btn.setToolTip("最小化")
        self._max_btn.setToolTip("最大化")
        self._close_btn.setToolTip("关闭")

        self._min_btn.clicked.connect(self.showMinimized)
        self._max_btn.clicked.connect(self._toggle_maximize)
        self._close_btn.clicked.connect(self.close)

        win_btn_layout.addWidget(self._min_btn)
        win_btn_layout.addWidget(self._max_btn)
        win_btn_layout.addWidget(self._close_btn)
        btn_layout.addWidget(win_btn_container)

        top_layout.addWidget(brand_container)
        top_layout.addStretch()
        top_layout.addWidget(btn_container)

        parent_layout.addWidget(top_bar)

        # 顶部栏所有按钮（统一随主题刷新）
        self._top_nav_buttons = [
            clear_btn, open_folder_btn, img_gen_btn, video_gen_btn, settings_btn]
        # 无边框标题栏：为顶部栏及其子控件安装拖拽事件
        self._install_titlebar_events(top_bar)
        # 统一应用主题（背景 + 子控件颜色）
        self._apply_top_nav_theme(self.current_theme)

    def _apply_top_nav_theme(self, theme: str):
        """统一刷新顶部导航栏背景与子控件颜色（随主题切换）"""
        dark = (theme == 'dark')
        from ui.styles import get_style as _gs
        gs = lambda n: _gs(n, theme)

        # 顶部栏背景
        if hasattr(self, '_top_bar'):
            self._top_bar.setStyleSheet(gs('top_nav'))

        # 品牌图标 / 文字
        if hasattr(self, '_brand_icon'):
            self._brand_icon.setStyleSheet(
                f"color: {'#d9dae0' if dark else '#1d1d1f'}; "
                f"font-size: 20px; background: transparent;")
        if hasattr(self, '_brand_label'):
            self._brand_label.setStyleSheet(
                f"color: {'#d9dae0' if dark else '#1d1d1f'}; "
                f"font-size: 17px; font-weight: bold; background: transparent; "
                f"letter-spacing: 1px;")

        # 状态胶囊 + 文字
        if hasattr(self, '_status_container'):
            self._status_container.setStyleSheet(
                f"background: {'rgba(255,255,255,0.08)' if dark else 'rgba(0,0,0,0.05)'}; "
                f"border-radius: 10px;")
        if hasattr(self, '_status_label'):
            self._status_label.setStyleSheet(
                f"color: {'#9a9ca6' if dark else '#6b7280'}; "
                f"font-size: 11px; background: transparent;")

        # 右侧功能按钮
        dark_btn_style = """
            QPushButton {
                background: rgba(255,255,255,0.08);
                color: #d9dae0;
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.1);
                font-size: 12px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.15);
                border-color: rgba(255,255,255,0.2);
            }
            QPushButton:pressed {
                background: rgba(255,255,255,0.05);
            }
        """
        btn_style = dark_btn_style if dark else gs('top_nav_btn')
        for btn in getattr(self, '_top_nav_buttons', []):
            try:
                btn.setStyleSheet(btn_style)
            except Exception:
                pass

        # 自绘标题栏窗口控制按钮颜色随主题切换
        for btn in getattr(self, '_titlebar_buttons', []):
            try:
                btn.set_theme(theme)
            except Exception:
                pass

    def setup_left_session_panel(self, splitter):
        """设置左侧导航栏：Tab式（历史记录 | 文件）+ 搜索 + 增强右键菜单"""
        self._left_panel = QWidget()
        self._left_panel.setFixedWidth(280)
        if self.current_theme == 'dark':
            self._left_panel.setStyleSheet("""
                background: #27282e;
                border-right: 1px solid rgba(255, 255, 255, 0.06);
            """)
        else:
            self._left_panel.setStyleSheet(get_style('left_panel'))

        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # ========== Tab 栏 ==========
        tab_bar = QWidget()
        self._left_tab_bar = tab_bar  # 保存引用以便主题切换时刷新
        tab_bar.setObjectName("left_tab_bar")
        tab_bar.setFixedHeight(42)
        tab_bar.setStyleSheet(get_style('left_tab_bar'))
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(6, 5, 6, 5)
        tab_layout.setSpacing(4)

        self._tab_sessions_btn = QPushButton("💬 历史记录")
        self._tab_sessions_btn.setObjectName("left_tab_btn")
        self._tab_sessions_btn.setCheckable(True)
        self._tab_sessions_btn.setChecked(True)
        self._tab_sessions_btn.setCursor(Qt.PointingHandCursor)
        self._tab_sessions_btn.clicked.connect(lambda: self._switch_left_tab("sessions"))

        self._tab_files_btn = QPushButton("📁 文件")
        self._tab_files_btn.setObjectName("left_tab_btn")
        self._tab_files_btn.setCheckable(True)
        self._tab_files_btn.setCursor(Qt.PointingHandCursor)
        self._tab_files_btn.clicked.connect(lambda: self._switch_left_tab("files"))

        tab_layout.addWidget(self._tab_sessions_btn)
        tab_layout.addWidget(self._tab_files_btn)
        tab_layout.addStretch()

        left_layout.addWidget(tab_bar)

        # ========== 历史记录页 ==========
        self._sessions_page = QWidget()
        sessions_page_layout = QVBoxLayout(self._sessions_page)
        sessions_page_layout.setContentsMargins(0, 0, 0, 0)
        sessions_page_layout.setSpacing(0)

        # 搜索栏
        search_wrapper = QWidget()
        search_wrapper.setStyleSheet("background: transparent; padding: 8px 10px 4px 10px;")
        sw_layout = QHBoxLayout(search_wrapper)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(0)

        self._session_search = QLineEdit()
        self._session_search.setPlaceholderText("🔍 搜索会话...")
        self._session_search.setStyleSheet(get_style('search_bar'))
        self._session_search.setFixedHeight(30)
        self._session_search.textChanged.connect(self._on_session_search)
        sw_layout.addWidget(self._session_search)

        # 会话列表
        self.session_list = QListWidget()
        self.session_list.setStyleSheet(get_style('session_list'))
        self.session_list.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_list.itemClicked.connect(self._on_session_clicked)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._on_session_list_context_menu)

        # 会话底部：新建按钮
        sess_bottom = QWidget()
        sess_bottom.setObjectName("bottom_bar")
        sess_bottom.setStyleSheet(get_style('bottom_bar'))
        sbl = QHBoxLayout(sess_bottom)
        sbl.setContentsMargins(10, 8, 10, 8)
        new_session_btn = QPushButton("＋ 新建会话")
        new_session_btn.setFixedHeight(34)
        new_session_btn.setCursor(Qt.PointingHandCursor)
        new_session_btn.setStyleSheet(get_style('new_session_btn'))
        new_session_btn.clicked.connect(self.new_session)
        self.new_session_btn = new_session_btn  # 保存引用以便主题切换时刷新
        sbl.addWidget(new_session_btn)

        sessions_page_layout.addWidget(search_wrapper)
        sessions_page_layout.addWidget(self.session_list)
        sessions_page_layout.addWidget(sess_bottom)

        # ========== 文件页 ==========
        self._files_page = QWidget()
        self._files_page.hide()
        files_page_layout = QVBoxLayout(self._files_page)
        files_page_layout.setContentsMargins(0, 0, 0, 0)
        files_page_layout.setSpacing(0)

        # 面包屑导航
        self._breadcrumb_bar = QWidget()
        self._breadcrumb_bar.setObjectName("breadcrumb_bar")
        self._breadcrumb_bar.setStyleSheet(get_style('breadcrumb'))
        self._breadcrumb_bar.setFixedHeight(32)
        self._breadcrumb_layout = QHBoxLayout(self._breadcrumb_bar)
        self._breadcrumb_layout.setContentsMargins(8, 0, 4, 0)
        self._breadcrumb_layout.setSpacing(0)
        self._breadcrumb_layout.addStretch()

        # 文件工具栏
        self._file_toolbar = QWidget()
        self._file_toolbar.setFixedHeight(30)
        self._file_toolbar.setStyleSheet("background: transparent;")
        ft_layout = QHBoxLayout(self._file_toolbar)
        ft_layout.setContentsMargins(6, 0, 6, 0)
        ft_layout.setSpacing(2)

        self._file_title_label = QLabel("文件浏览器")
        if self.current_theme == 'dark':
            self._file_title_label.setStyleSheet(
                "color: #d9dae0; font-size: 13px; font-weight: 600; background: transparent;")
        else:
            self._file_title_label.setStyleSheet(
                "color: #18181b; font-size: 13px; font-weight: 600; background: transparent;")
        self._file_title_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self._file_title_label.customContextMenuRequested.connect(self._on_root_path_context_menu)

        for icon, tip, slot in [
            ("🔄", "刷新", self._refresh_file_tree),
            ("📂", "打开文件夹", self._open_folder),
            ("📄", "新建文件", lambda: self._create_new_item_in_root(is_dir=False)),
            ("📁", "新建目录", lambda: self._create_new_item_in_root(is_dir=True)),
            ("📌", "折叠全部", lambda: self.file_tree.collapseAll()),
        ]:
            btn = QPushButton(icon)
            btn.setFixedSize(26, 24)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(get_style('file_toolbar_btn'))
            btn.clicked.connect(slot)
            ft_layout.addWidget(btn)

        ft_layout.insertWidget(0, self._file_title_label)
        ft_layout.insertSpacing(1, 4)

        # 文件树
        self.file_tree = QTreeView()
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")
        self.file_model.setNameFilterDisables(False)
        self.file_tree.setModel(self.file_model)
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAnimated(True)
        self.file_tree.setIndentation(16)
        self.file_tree.setColumnHidden(1, True)
        self.file_tree.setColumnHidden(2, True)
        self.file_tree.setColumnHidden(3, True)
        self.file_tree.setStyleSheet(get_style('file_tree_enhanced'))
        self.file_tree.viewport().setStyleSheet("background: transparent;")
        self.file_tree.verticalScrollBar().setStyleSheet(
            get_style('scrollbar_dark') if self.current_theme == 'dark'
            else get_style('scrollbar_light'))
        self.file_tree.doubleClicked.connect(self._on_file_double_click)
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._on_file_context_menu)

        # 文件底部：终端按钮
        self._file_bottom = QWidget()
        self._file_bottom.setObjectName("bottom_bar")
        self._file_bottom.setStyleSheet(get_style('bottom_bar'))
        fbl = QHBoxLayout(self._file_bottom)
        fbl.setContentsMargins(10, 8, 10, 8)
        self.show_terminal_btn = QPushButton("  显示终端")
        self.show_terminal_btn.setFixedHeight(34)
        self.show_terminal_btn.setCursor(Qt.PointingHandCursor)
        self.show_terminal_btn.setStyleSheet("""
            QPushButton {
                background: #32333a;
                color: #d9dae0;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
                padding-left: 12px;
            }
            QPushButton:hover {
                background: #3a3b43;
                border-color: rgba(110,127,224,0.5);
            }
        """)
        self.show_terminal_btn.clicked.connect(self._toggle_terminal_panel)
        fbl.addWidget(self.show_terminal_btn)

        # 后台任务按钮
        self.show_bg_task_btn = QPushButton("  后台任务")
        self.show_bg_task_btn.setFixedHeight(34)
        self.show_bg_task_btn.setCursor(Qt.PointingHandCursor)
        self.show_bg_task_btn.setStyleSheet("""
            QPushButton {
                background: #32333a;
                color: #d9dae0;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
                padding-left: 12px;
            }
            QPushButton:hover {
                background: #3a3b43;
                border-color: rgba(110,127,224,0.5);
            }
        """)
        self.show_bg_task_btn.clicked.connect(self._toggle_background_task_panel)
        fbl.addWidget(self.show_bg_task_btn)

        files_page_layout.addWidget(self._breadcrumb_bar)
        files_page_layout.addWidget(self._file_toolbar)
        self.file_tree.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        files_page_layout.addWidget(self.file_tree)
        files_page_layout.addWidget(self._file_bottom)

        # 空状态：无工作区时显示大 "+" 按钮
        self._file_empty_widget = QWidget()
        self._file_empty_widget.setObjectName("file_empty")
        if self.current_theme == 'dark':
            self._file_empty_widget.setStyleSheet("background: #27282e;")
        else:
            self._file_empty_widget.setStyleSheet("background: #fafbfc;")
        empty_layout = QVBoxLayout(self._file_empty_widget)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(12)

        add_ws_btn = QPushButton("＋")
        add_ws_btn.setFlat(True)
        add_ws_btn.setCursor(Qt.PointingHandCursor)
        add_ws_btn.setStyleSheet("""
            QPushButton {
                font-size: 52px; color: #6f7178; background: transparent;
                border: 2px dashed rgba(255,255,255,0.14); border-radius: 18px;
                padding: 16px 36px;
            }
            QPushButton:hover {
                color: #6e7fe0; border-color: #6e7fe0;
                background: rgba(110, 127, 224, 0.08);
            }
        """)
        add_ws_btn.clicked.connect(self._open_folder)

        add_ws_label = QLabel("点击添加工作区")
        add_ws_label.setAlignment(Qt.AlignCenter)
        add_ws_label.setStyleSheet(
            "color: #9ca3af; font-size: 13px; background: transparent;")

        add_ws_hint = QLabel("选择一个文件夹以启用文件浏览器")
        add_ws_hint.setAlignment(Qt.AlignCenter)
        add_ws_hint.setStyleSheet(
            "color: #6f7178; font-size: 11px; background: transparent;")

        empty_layout.addStretch(2)
        empty_layout.addWidget(add_ws_btn, 0, Qt.AlignCenter)
        empty_layout.addWidget(add_ws_label, 0, Qt.AlignCenter)
        empty_layout.addWidget(add_ws_hint, 0, Qt.AlignCenter)
        empty_layout.addStretch(3)
        self._file_empty_widget.hide()
        files_page_layout.addWidget(self._file_empty_widget)

        # 添加页面到布局
        left_layout.addWidget(self._sessions_page)
        left_layout.addWidget(self._files_page)

        # 初始化：默认显示历史记录
        self._current_left_tab = "sessions"
        self._file_collapsed_legacy = False  # 已不再使用折叠模式

        splitter.addWidget(self._left_panel)

    def _switch_left_tab(self, tab: str):
        """切换左侧 Tab"""
        self._current_left_tab = tab
        if tab == "sessions":
            self._tab_sessions_btn.setChecked(True)
            self._tab_files_btn.setChecked(False)
            self._sessions_page.show()
            self._files_page.hide()
        else:
            self._tab_sessions_btn.setChecked(False)
            self._tab_files_btn.setChecked(True)
            self._sessions_page.hide()
            self._files_page.show()

    def _on_session_search(self, text: str):
        """搜索/过滤会话列表"""
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            widget = self.session_list.itemWidget(item)
            if widget and hasattr(widget, 'title'):
                match = text.lower() in widget.title.lower()
                item.setHidden(not match)

    def _refresh_file_tree(self):
        """刷新文件树"""
        root = self.file_model.rootPath()
        if root:
            self.file_model.setRootPath("")
            self.file_model.setRootPath(root)
            idx = self.file_model.index(root)
            self.file_tree.setRootIndex(idx)
            self.show_toast("文件树已刷新")

    def _create_new_item_in_root(self, is_dir: bool):
        """在文件树根目录创建新文件/目录"""
        root = self.file_model.rootPath()
        if not root:
            self.show_toast("请先打开文件夹")
            return
        self._create_new_item(root, is_dir)

    def setup_middle_chat_area(self, splitter):
        """设置中间对话区"""
        self._middle_widget = QWidget()
        if self.current_theme == 'dark':
            self._middle_widget.setStyleSheet("background: #27282e; border: none;")
        else:
            self._middle_widget.setStyleSheet(get_style('middle'))

        middle_layout = QVBoxLayout(self._middle_widget)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        # 对话消息区域
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if self.current_theme == 'dark':
            self.chat_scroll.setStyleSheet("background: #27282e; border: none;")
        else:
            self.chat_scroll.setStyleSheet(get_style('chat_scroll'))
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.verticalScrollBar().setStyleSheet(get_style('scrollbar_chat'))

        # 消息容器
        self.chat_container = QWidget()
        if self.current_theme == 'dark':
            self.chat_container.setStyleSheet("background: #27282e;")
        else:
            self.chat_container.setStyleSheet("background: #f4f5f7;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(16, 12, 16, 12)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch()

        self.add_welcome_message()

        self.chat_scroll.setWidget(self.chat_container)

        # ── 自动滚动控制：用户手动上滚时暂停自动滚动 ──
        self._auto_scroll_enabled = True
        self._chat_scrollbar = self.chat_scroll.verticalScrollBar()
        self._chat_scrollbar.valueChanged.connect(self._on_scroll_value_changed)

        middle_layout.addWidget(self.chat_scroll, 1)  # stretch=1, takes remaining space

        # 输入区域
        self._input_container = QWidget()
        self._input_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # 根据主题设置输入容器背景
        if self.current_theme == 'dark':
            self._input_container.setStyleSheet("""
                background: #27282e;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            """)
        else:
            self._input_container.setStyleSheet("""
                background: #f0f2f5;
                border-top: 1px solid rgba(0, 0, 0, 0.05);
            """)
        input_outer = QVBoxLayout(self._input_container)
        input_outer.setContentsMargins(20, 8, 20, 12)
        input_outer.setSpacing(6)

        # 图片预览区（默认隐藏）
        self._attached_images = []  # [(base64_str, QPixmap), ...]
        self.image_preview_area = QWidget()
        self.image_preview_layout = QHBoxLayout(self.image_preview_area)
        self.image_preview_layout.setContentsMargins(0, 0, 0, 0)
        self.image_preview_layout.setSpacing(6)
        self.image_preview_area.hide()

        input_outer.addWidget(self.image_preview_area)

        # 上下文使用率
        context_bar = QWidget()
        context_bar.setStyleSheet("background: transparent;")
        context_layout = QHBoxLayout(context_bar)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(6)

        self.context_label = QLabel("上下文: 0 token")
        if self.current_theme == 'dark':
            self.context_label.setStyleSheet(
                "color: #6f7178; font-size: 11px; background: transparent;")
        else:
            self.context_label.setStyleSheet(
                "color: #86868b; font-size: 11px; background: transparent;")

        self.context_progress = QProgressBar()
        self.context_progress.setFixedHeight(6)
        self.context_progress.setRange(0, 100)
        self.context_progress.setValue(0)
        self.context_progress.setTextVisible(False)
        if self.current_theme == 'dark':
            self.context_progress.setStyleSheet("""
                QProgressBar {
                    background: rgba(255,255,255,0.08);
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background: #6e7fe0;
                    border-radius: 3px;
                }
            """)
        else:
            self.context_progress.setStyleSheet("""
                QProgressBar {
                    background: #e5e5ea;
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background: #6e7fe0;
                    border-radius: 3px;
                }
            """)

        self.context_percent = QLabel("0%")
        self.context_percent.setFixedWidth(36)
        if self.current_theme == 'dark':
            self.context_percent.setStyleSheet(
                "color: #666666; font-size: 11px; background: transparent;")
        else:
            self.context_percent.setStyleSheet(
                "color: #86868b; font-size: 11px; background: transparent;")

        context_layout.addWidget(self.context_label)
        context_layout.addWidget(self.context_progress, 1)
        context_layout.addWidget(self.context_percent)

        input_outer.addWidget(context_bar)

        # 输入行
        input_row = QWidget()
        input_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)

        # 上传附件按钮
        upload_btn = QPushButton("📎")
        upload_btn.setFixedSize(44, 44)
        upload_btn.setCursor(Qt.PointingHandCursor)
        upload_btn.setToolTip("上传图片")
        upload_btn.setStyleSheet(get_style('voice_btn'))
        upload_btn.clicked.connect(self._upload_image)
        self.upload_btn = upload_btn  # 保存引用以便主题切换时刷新

        self.message_input = QPlainTextEdit()
        if self.current_theme == 'dark':
            self.message_input.setPlaceholderText("给智能体发消息")
        else:
            self.message_input.setPlaceholderText("输入消息... (Enter 发送，Ctrl+Enter 换行，支持粘贴图片)")
        self.message_input.setStyleSheet(get_style('message_input'))
        self.message_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.message_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.message_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.message_input.textChanged.connect(self._auto_resize_input)
        self.message_input.installEventFilter(self)
        self._auto_resize_input()

        # 发送/停止按钮
        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(44, 44)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("发送消息 (Enter)")
        self.send_btn.setStyleSheet(get_style('send_btn'))
        self.send_btn.clicked.connect(self._on_send_btn_clicked)
        self._is_generating = False
        self._api_worker = None
        self._api_thread = None
        self._api_worker_id = None

        # 模型选择器（输入框右侧）
        model_names = get_model_display_names()
        if not model_names:
            model_names = ["mimo-v2.5-pro"]
        self.model_combo = ModernDropdown(model_names, theme=getattr(self, 'current_theme', 'light'))
        self.model_combo.setFixedHeight(38)
        self.model_combo.setMinimumWidth(150)
        last_model = getattr(self, "_last_model_display", "")
        if last_model:
            model_index = self.model_combo.findText(last_model)
            if model_index >= 0:
                self.model_combo.setCurrentIndex(model_index)
        self.model_combo.currentChanged.connect(self._on_model_combo_changed)

        self.chatgpt_login_btn = QPushButton("启动 ChatGPT Chrome")
        self.chatgpt_login_btn.setFixedHeight(38)
        self.chatgpt_login_btn.setCursor(Qt.PointingHandCursor)
        self.chatgpt_login_btn.setToolTip(
            "启动 ChatGPT 专用 Chrome（独立配置，可与桌面 Chrome 同时开）。"
            "支持 Agent 模式（与 DeepSeek/MiniMax 浏览器接入相同）。"
            "看到「调试端口已就绪」后再发消息。"
        )
        self.chatgpt_login_btn.setStyleSheet("""
            QPushButton {
                background: #f0f0f3; border: 1.5px solid #d1d1d6;
                border-radius: 19px; font-size: 12px; font-weight: bold;
                color: #6b7280; padding: 0 12px;
            }
            QPushButton:hover { border-color: #10a37f; color: #10a37f; }
        """)
        self.chatgpt_login_btn.clicked.connect(self._open_chatgpt_login)
        self._update_chatgpt_login_btn_visibility(self.model_combo.currentText())

        input_layout.addWidget(upload_btn)
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.chatgpt_login_btn)
        input_layout.addWidget(self.model_combo)

        # Agent/Chat 模式切换按钮
        self.agent_btn = QPushButton("Chat")
        self.agent_btn.setFixedSize(56, 38)
        self.agent_btn.setCursor(Qt.PointingHandCursor)
        self.agent_btn.setToolTip("切换 Chat/Agent 模式")
        self.agent_btn.setStyleSheet("""
            QPushButton {
                background: #f0f0f3; border: 1.5px solid #d1d1d6;
                border-radius: 19px; font-size: 12px; font-weight: bold;
                color: #6b7280;
            }
            QPushButton:hover { border-color: #6e7fe0; color: #6e7fe0; }
            QPushButton:checked {
                background: #6e7fe0;
                border-color: #6e7fe0; color: white;
            }
        """)
        self.agent_btn.setCheckable(True)
        self.agent_btn.toggled.connect(self._toggle_agent_mode)
        self.agent_btn.setChecked(True)  # 默认为 Agent 模式
        input_layout.addWidget(self.agent_btn)

        # 多Agent协作开关
        self.multi_agent_btn = QPushButton("Team")
        self.multi_agent_btn.setFixedSize(56, 38)
        self.multi_agent_btn.setCursor(Qt.PointingHandCursor)
        self.multi_agent_btn.setToolTip("多Agent协作：自动分解任务并分配专业Agent")
        self.multi_agent_btn.setStyleSheet("""
            QPushButton {
                background: #f0f0f3; border: 1.5px solid #d1d1d6;
                border-radius: 19px; font-size: 12px; font-weight: bold;
                color: #6b7280;
            }
            QPushButton:hover { border-color: #f59e0b; color: #f59e0b; }
            QPushButton:checked {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #f59e0b, stop:1 #ef4444);
                border-color: #f59e0b; color: white;
            }
        """)
        self.multi_agent_btn.setCheckable(True)
        self.multi_agent_btn.toggled.connect(self._toggle_multi_agent)
        self.multi_agent_btn.setChecked(self.chat_service.multi_agent_enabled)
        if not self.chat_service.multi_agent_enabled:
            self.multi_agent_btn.setEnabled(False)
            self.multi_agent_btn.setToolTip("多Agent协作：请在 config/multi_agent_config.json 中启用")
        input_layout.addWidget(self.multi_agent_btn)

        input_outer.addWidget(input_row)

        middle_layout.addWidget(self._input_container)  # Fixed height at bottom

        # ── 代码编辑器面板（Cursor 风格，可编辑 + 实时 Linter）──
        self.code_editor_panel = CodeEditorPanel()
        self.code_editor_panel.setMinimumWidth(360)
        self.code_editor_panel.file_saved.connect(self._on_editor_file_saved)
        self.code_editor_panel.request_fix.connect(self._on_editor_request_fix)
        self.code_editor_panel.file_opened.connect(self._on_editor_file_opened)
        self.code_editor_panel.file_closed.connect(self._close_file_viewer)
        self.code_editor_panel.preview_url_requested.connect(self._on_preview_url_requested)

        # 保留旧属性名以兼容其他引用（file_viewer_panel → code_editor_panel 的别名）
        self.file_viewer_panel = self.code_editor_panel
        self.file_viewer_name = self.code_editor_panel._file_label
        self.file_viewer_content = self.code_editor_panel._editor

        # 用 QSplitter 实现可拖动调整大小
        self._middle_splitter = QSplitter(Qt.Horizontal)
        self._middle_splitter.setHandleWidth(3)
        self._middle_splitter.setStyleSheet("""
            QSplitter::handle { background: rgba(255,255,255,0.06); }
            QSplitter::handle:hover { background: #6e7fe0; }
        """)

        # 左侧：聊天区 + 终端 + 输入框
        chat_with_input = QWidget()
        cwi_layout = QVBoxLayout(chat_with_input)
        cwi_layout.setContentsMargins(0, 0, 0, 0)
        cwi_layout.setSpacing(0)
        cwi_layout.addWidget(self.chat_scroll)

        # 终端显示组件（默认隐藏）
        self.terminal_widget = TerminalWidget()
        self.terminal_widget.setContentsMargins(12, 8, 12, 0)
        self.terminal_widget.hide()
        cwi_layout.addWidget(self.terminal_widget)

        # 后台任务面板
        self.background_task_panel = BackgroundTaskPanel()
        self.background_task_panel.setContentsMargins(12, 4, 12, 0)
        self.background_task_panel.hide()
        self.background_task_panel.task_clicked.connect(self._on_background_task_clicked)
        cwi_layout.addWidget(self.background_task_panel)

        cwi_layout.addWidget(self._input_container)

        self._chat_widget_ref = chat_with_input

        # ── 布局顺序：中间=代码编辑器，右侧=聊天区 ──
        self._middle_splitter.addWidget(self.code_editor_panel)   # index 0: 中间代码编辑器
        self._middle_splitter.addWidget(chat_with_input)          # index 1: 右侧聊天区

        # 图片生成面板（默认隐藏，挂在最右侧）
        self.image_gen_panel = ImageGeneratorWidget()
        self.image_gen_panel.setMinimumWidth(320)
        self.image_gen_panel.hide()
        self.image_gen_panel.generate_clicked.connect(self._on_image_generate)
        self.image_gen_panel.close_clicked.connect(self._toggle_image_panel)
        self._middle_splitter.addWidget(self.image_gen_panel)     # index 2: 图片生成

        # 视频生成面板（默认隐藏）
        self.video_gen_panel = VideoGeneratorWidget()
        self.video_gen_panel.setMinimumWidth(320)
        self.video_gen_panel.hide()
        self.video_gen_panel.generate_clicked.connect(self._on_video_generate)
        self.video_gen_panel.close_clicked.connect(self._toggle_video_panel)
        self._middle_splitter.addWidget(self.video_gen_panel)     # index 3: 视频生成

        self._middle_splitter.setStretchFactor(0, 2)   # 代码编辑器
        self._middle_splitter.setStretchFactor(1, 3)   # 聊天区
        self._middle_splitter.setStretchFactor(2, 1)   # 图片生成
        self._middle_splitter.setStretchFactor(3, 1)   # 视频生成

        # 设置最小宽度，防止聊天区被过度压缩
        self.code_editor_panel.setMinimumWidth(300)
        chat_with_input.setMinimumWidth(400)

        # 初始无文件：隐藏代码编辑器，聊天区占满
        self.code_editor_panel.hide()
        self._layout_mode = 'full_chat'
        self._apply_layout_mode()

        splitter.addWidget(self._middle_splitter)

    def setup_right_control_panel(self):
        """初始化隐藏参数控件（由设置对话框读写）"""
        from services.config import get_agent_config
        agent_cfg = get_agent_config()
        llm_params = agent_cfg.get("llm_params", {})

        self.temp_slider = QSlider(Qt.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(int(llm_params.get("temperature", 0.3) * 100))
        self.temp_slider.hide()

        self.token_slider = QSlider(Qt.Horizontal)
        self.token_slider.setRange(256, 4096)
        self.token_slider.setValue(llm_params.get("max_tokens", 2048))
        self.token_slider.hide()

        self.steps_slider = QSlider(Qt.Horizontal)
        self.steps_slider.setRange(1, 30)
        self.steps_slider.setValue(agent_cfg.get("max_steps", 15))
        self.steps_slider.hide()

    def setup_style(self):
        # Use current theme
        from ui.styles import get_style as _gs
        def get_style(name): return _gs(name, self.current_theme)
        """设置整体样式"""
        self.setStyleSheet(get_style('main_window'))

    # ---- 文件附件处理 ----


    def _export_chat(self):
        """Export chat to file"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                messages = self._collect_messages()
                with open(file_path, 'w', encoding='utf-8') as f:
                    for msg in messages:
                        role = msg.get('role', 'unknown')
                        content = msg.get('content', '')
                        f.write(f"[{role.upper()}]\n{content}\n\n")
                QMessageBox.information(self, "Success", "Chat exported successfully!")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to export: {str(e)}")

    def _log_agent(self, text: str):
        """Append text to agent log panel"""
        if hasattr(self, 'agent_log'):
            self.agent_log.appendPlainText(text)


    def _toggle_search(self):
        """Toggle search in chat"""
        if not hasattr(self, '_search_visible'):
            self._search_visible = False
        self._search_visible = not self._search_visible
        if self._search_visible:
            if not hasattr(self, 'search_bar'):
                from PySide6.QtWidgets import QLineEdit as _LE
                self.search_bar = _LE()
                self.search_bar.setPlaceholderText("Search in chat... (Ctrl+F)")
                self.search_bar.setStyleSheet("""
                    QLineEdit { background: white; border: 1px solid #d1d5db; border-radius: 8px;
                                padding: 6px 12px; font-size: 13px; }
                    QLineEdit:focus { border-color: #6e7fe0; }
                """)
                self.search_bar.returnPressed.connect(self._do_search)
            self.search_bar.show()
            self.search_bar.setFocus()
        else:
            if hasattr(self, 'search_bar'):
                self.search_bar.hide()

    def _do_search(self):
        """Search through chat messages"""
        if not hasattr(self, 'search_bar'):
            return
        query = self.search_bar.text().strip().lower()
        if not query:
            return
        count = 0
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                text = ""
                if hasattr(w, 'text'):
                    text = w.text.lower()
                elif hasattr(w, 'layout') and w.layout():
                    for j in range(w.layout().count()):
                        sub = w.layout().itemAt(j)
                        if sub and sub.widget() and hasattr(sub.widget(), 'text'):
                            text = sub.widget().text.lower()
                if query in text:
                    count += 1
        self.show_toast(f"Found {count} messages matching '{query}'")

    def _toggle_agent_mode(self, checked):
        """切换 Agent/Chat 模式"""
        self.chat_service.agent_mode = checked
        if checked:
            self.agent_btn.setText("Agent")
            self._log_agent("[Mode] Agent mode enabled - ReAct reasoning + file tools")
            self.show_toast("Agent 模式已启用 (ReAct 推理 + 文件工具)")
        else:
            self.agent_btn.setText("Chat")
            self._log_agent("[Mode] Chat mode enabled")
            self.show_toast("Chat 模式已启用")

    def _toggle_multi_agent(self, checked):
        """切换多Agent协作模式"""
        self.chat_service.toggle_multi_agent(checked)
        if checked:
            agents = self.chat_service.get_available_agents()
            names = ", ".join(a["display"] for a in agents) if agents else "无Agent"
            self.multi_agent_btn.setText("Team")
            self._log_agent(f"[Mode] Multi-Agent enabled: {names}")
            self.show_toast(f"多Agent协作已启用 ({len(agents)} 个Agent)")
        else:
            self.multi_agent_btn.setText("Team")
            self._log_agent("[Mode] Multi-Agent disabled")
            self.show_toast("多Agent协作已关闭")

    def _toggle_layout_mode(self):
        """切换布局：split（代码+聊天）↔ full_chat（全聊天）"""
        if self._layout_mode == 'split':
            self._layout_mode = 'full_chat'
        else:
            if not self.code_editor_panel.has_file():
                self.show_toast("请先从左侧文件树打开一个文件")
                return
            self._layout_mode = 'split'
        self._apply_layout_mode()

    def _apply_layout_mode(self):
        """应用当前布局模式
        split: 中间代码编辑器 + 右侧聊天区
        full_chat: 聊天区占满全部空间（代码编辑器隐藏）
        """
        if self._layout_mode == 'split' and self.code_editor_panel.has_file():
            self.code_editor_panel.show()
            sizes = self._middle_splitter.sizes()
            if len(sizes) >= 2:
                total = sum(sizes)
                if total > 0:
                    # 中间代码编辑器 38%，右侧聊天区 62%
                    self._middle_splitter.setSizes([int(total * 0.38), int(total * 0.62), 0])
        else:
            self.code_editor_panel.hide()
            self._layout_mode = 'full_chat'
            sizes = self._middle_splitter.sizes()
            if len(sizes) >= 2:
                total = sum(sizes)
                if total > 0:
                    self._middle_splitter.setSizes([0, total, 0])

    def _on_editor_file_saved(self, file_path: str, content: str):
        """代码编辑器保存文件回调"""
        self.show_toast(f"已保存: {os.path.basename(file_path)}")
        # 记录到回滚管理器
        if self._rollback_mgr:
            self._rollback_mgr.record_write(file_path, content)

    def _on_editor_file_opened(self, file_path: str):
        """代码编辑器打开文件回调"""
        self.status_label.setText(f"编辑: {os.path.basename(file_path)}")

    def _on_preview_url_requested(self, url: str):
        """请求预览 URL（用于自动启动后端场景）"""
        self.code_editor_panel.open_url(url)
        # 切换到 split 模式以显示预览
        self._layout_mode = 'split'
        self._apply_layout_mode()
        self.status_label.setText(f"预览: {url}")

    def _on_editor_request_fix(self, file_path: str, errors: list, content: str):
        """代码编辑器请求自动修复 — 将错误信息发送到 Agent"""
        error_msgs = "\n".join(
            f"  行 {e['line']}: {e['message']}" for e in errors
        )
        fix_message = (
            f"请修复文件 {file_path} 中的语法错误：\n\n"
            f"{error_msgs}\n\n"
            f"请直接修改文件并确保语法检查通过。"
        )
        # 自动切换到 Agent 模式并发送修复请求
        if not self.chat_service.agent_mode:
            self.agent_btn.setChecked(True)
        self.message_input.setPlainText(fix_message)
        self._on_send_btn_clicked()

    def _open_file_in_editor(self, path: str):
        """在代码编辑器中打开文件 — 自动切换到 split 模式（中间代码+右侧聊天）"""
        self.code_editor_panel.open_file(path)
        self._layout_mode = 'split'
        self._apply_layout_mode()

    def _toggle_image_panel(self):
        """切换图片生成面板显示/隐藏"""
        if self.image_gen_panel.isVisible():
            self.image_gen_panel.hide()
        else:
            self.image_gen_panel.show()

    def _on_image_generate(self, prompt: str, image_path):
        """调用图片生成 API"""
        from services.image_service import ImageWorker, upload_image_to_base64
        self.image_gen_panel.set_generating(True)

        image_url = None
        if image_path:
            image_url = upload_image_to_base64(image_path)

        model = self.image_gen_panel.get_selected_model_id()
        self._img_thread = QThread()
        self._img_worker = ImageWorker(
            prompt=prompt,
            image_url=image_url,
            model=model,
            image_size=self.image_gen_panel.size_combo.currentText().split()[0],
            num_inference_steps=self.image_gen_panel.steps_spin.value(),
            seed=self.image_gen_panel.seed_spin.value() if self.image_gen_panel.seed_spin.value() >= 0 else None,
        )
        self._img_worker.moveToThread(self._img_thread)
        self._img_thread.started.connect(self._img_worker.run)
        self._img_worker.finished.connect(self._on_image_done)
        self._img_worker.error.connect(self._on_image_error)
        self._img_worker.status_log.connect(self.image_gen_panel.append_status_log)
        self._img_thread.start()

    def _on_image_done(self, image_url: str):
        self.image_gen_panel.set_generating(False)
        self.image_gen_panel.set_result(image_url)
        if self._img_thread:
            self._img_thread.quit()
            self._img_thread.wait()

    def _on_image_error(self, msg: str):
        self.image_gen_panel.set_generating(False)
        self.image_gen_panel.set_error(msg)
        if self._img_thread:
            self._img_thread.quit()
            self._img_thread.wait()

    def _toggle_video_panel(self):
        """切换视频生成面板显示/隐藏"""
        if self.video_gen_panel.isVisible():
            self.video_gen_panel.hide()
        else:
            self.video_gen_panel.show()

    def _on_video_generate(self, prompt: str, height: int, width: int,
                           num_frames: int, frame_rate: int, image_path: str = ""):
        """调用视频生成 API（支持文生视频和图生视频）"""
        from services.video_service import VideoWorker
        self.video_gen_panel.set_generating(True)

        self._vid_thread = QThread()
        self._vid_worker = VideoWorker(
            prompt=prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            image=image_path or None,
        )
        self._vid_worker.moveToThread(self._vid_thread)
        self._vid_thread.started.connect(self._vid_worker.run)
        self._vid_worker.finished.connect(self._on_video_done)
        self._vid_worker.error.connect(self._on_video_error)
        self._vid_worker.status_log.connect(self.video_gen_panel.append_status_log)
        self._vid_thread.start()

    def _on_video_done(self, video_url: str):
        self.video_gen_panel.set_generating(False)
        self.video_gen_panel.set_result(video_url)
        if self._vid_thread:
            self._vid_thread.quit()
            self._vid_thread.wait()
        # 同时在聊天区渲染视频
        self._show_chat_video(video_url)

    def _on_video_error(self, msg: str):
        self.video_gen_panel.set_generating(False)
        self.video_gen_panel.set_error(msg)
        if self._vid_thread:
            self._vid_thread.quit()
            self._vid_thread.wait()
        # 错误也同步到聊天区
        self.add_message(f"⚠ 视频生成失败: {msg}", is_user=False)

    def _show_chat_video(self, video_url: str):
        """在聊天区插入一条包含视频播放器的消息"""
        msg_widget = ChatMessageWidget("正在加载视频...", is_user=False)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_widget)

        # 清理旧组件（如果有）
        if hasattr(msg_widget, '_video_container') and msg_widget._video_container:
            msg_widget._video_container.setParent(None)
            msg_widget._video_container.deleteLater()

        # 隐藏初始文本
        msg_widget.message_label.hide()

        # 创建视频播放器容器
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setMaximumWidth(600)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 4, 0, 4)
        container_layout.setSpacing(0)

        player = VideoPlayerWidget(video_url)
        container_layout.addWidget(player)

        # 插入到消息布局中
        msg_widget.message_container.layout().addWidget(container)
        msg_widget._video_container = container

        QTimer.singleShot(50, self.scroll_to_bottom)

    # ── 从聊天框直接调用视频生成 ──
    def _call_video_api(self, prompt: str, height: int = 768, width: int = 1152,
                        num_frames: int = 121, frame_rate: int = 24, image: str = ""):
        """从聊天框调用视频生成 API，结果以消息形式显示在聊天区"""
        self.status_dot.setStyleSheet("color: #fbbf24; font-size: 9px; background: transparent;")
        self.status_label.setText("生成视频中...")
        self._set_send_btn_state(True)

        # 清除上一次残留
        if hasattr(self, '_chat_vid_gen_msg') and self._chat_vid_gen_msg:
            try:
                self._chat_vid_gen_msg.setParent(None)
                self._chat_vid_gen_msg.deleteLater()
            except Exception:
                pass
            self._chat_vid_gen_msg = None

        # 插入「视频生成中...」临时消息
        self._chat_vid_gen_msg = ChatMessageWidget("⏳ 视频生成中...", is_user=False)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._chat_vid_gen_msg)
        QTimer.singleShot(50, self.scroll_to_bottom)

        from services.video_service import VideoWorker
        self._chat_vid_thread = QThread()
        self._chat_vid_worker = VideoWorker(
            prompt=prompt, height=height, width=width,
            num_frames=num_frames, frame_rate=frame_rate,
            image=image or None,)
        self._chat_vid_worker.moveToThread(self._chat_vid_thread)
        self._chat_vid_thread.started.connect(self._chat_vid_worker.run)
        self._chat_vid_worker.finished.connect(self._on_chat_video_done)
        self._chat_vid_worker.error.connect(self._on_chat_video_error)
        self._chat_vid_worker.status_log.connect(self._on_chat_vid_status)
        self._chat_vid_thread.start()

    def _on_chat_vid_status(self, text: str):
        """视频生成进度 → 刷新临时消息"""
        if text and hasattr(self, '_chat_vid_gen_msg') and self._chat_vid_gen_msg:
            try:
                line = text.strip()
                if line:
                    self._chat_vid_gen_msg.update_text(f"⏳ {line}")
            except Exception:
                pass

    def _on_chat_video_done(self, video_url: str):
        """聊天区视频生成完成"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        msg_widget = self._chat_vid_gen_msg if (
            hasattr(self, '_chat_vid_gen_msg') and self._chat_vid_gen_msg
        ) else None
        self._chat_vid_gen_msg = None

        if msg_widget is None:
            msg_widget = ChatMessageWidget("视频生成完成", is_user=False)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_widget)

        # 渲染视频播放器
        self._render_video_in_msg(video_url, msg_widget)

        if self._chat_vid_thread:
            self._chat_vid_thread.quit()
            self._chat_vid_thread.wait()

    def _on_chat_video_error(self, msg: str):
        """聊天区视频生成失败"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        err_msg = f"⚠ 视频生成失败: {msg}"
        if hasattr(self, '_chat_vid_gen_msg') and self._chat_vid_gen_msg:
            self._chat_vid_gen_msg.update_text(err_msg)
            self._chat_vid_gen_msg.on_streaming_finished()
            self._chat_vid_gen_msg = None
        else:
            self.add_message(err_msg, is_user=False)

        if self._chat_vid_thread:
            self._chat_vid_thread.quit()
            self._chat_vid_thread.wait()

    def _render_video_in_msg(self, video_url: str, msg_widget):
        """在已有的消息组件中渲染视频播放器"""
        msg_widget.message_label.hide()

        if hasattr(msg_widget, '_video_container') and msg_widget._video_container:
            msg_widget._video_container.setParent(None)
            msg_widget._video_container.deleteLater()

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setMaximumWidth(600)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 4, 0, 4)
        container_layout.setSpacing(0)

        player = VideoPlayerWidget(video_url)
        container_layout.addWidget(player)

        msg_widget.message_container.layout().addWidget(container)
        msg_widget._video_container = container

        QTimer.singleShot(50, self.scroll_to_bottom)

    def _call_image_api(self, prompt: str, ref_image_path, model: str):
        """从聊天框调用图片生成 API，结果以消息形式显示在聊天区"""
        self.status_dot.setStyleSheet("color: #fbbf24; font-size: 9px; background: transparent;")
        self.status_label.setText("生成图片中...")
        self._set_send_btn_state(True)

        # 清除上一次残留的临时消息（如果有）
        if hasattr(self, '_chat_img_gen_msg') and self._chat_img_gen_msg:
            try:
                self._chat_img_gen_msg.setParent(None)
                self._chat_img_gen_msg.deleteLater()
            except Exception:
                pass
            self._chat_img_gen_msg = None

        # 在聊天区插入一个「图片生成中...」的临时消息
        self._chat_img_gen_msg = ChatMessageWidget(
            "⏳ 图片生成中...", is_user=False)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._chat_img_gen_msg)
        QTimer.singleShot(50, self.scroll_to_bottom)

        image_url = None
        if ref_image_path:
            image_url = upload_image_to_base64(ref_image_path)

        self._chat_img_thread = QThread()
        self._chat_img_worker = ImageWorker(
            prompt=prompt, image_url=image_url, model=model)
        self._chat_img_worker.moveToThread(self._chat_img_thread)
        self._chat_img_thread.started.connect(self._chat_img_worker.run)
        self._chat_img_worker.finished.connect(self._on_chat_image_done)
        self._chat_img_worker.error.connect(self._on_chat_image_error)
        # 将浏览器端进度日志实时刷新到临时消息上
        self._chat_img_worker.status_log.connect(self._on_chat_img_status)
        self._chat_img_thread.start()

    def _on_chat_img_status(self, text: str):
        """浏览器出图进度日志 → 刷新临时提示"""
        if text and hasattr(self, '_chat_img_gen_msg') and self._chat_img_gen_msg:
            try:
                line = text.strip()
                if line:
                    self._chat_img_gen_msg.update_text(f"⏳ {line}")
            except Exception:
                pass

    def _on_chat_image_done(self, image_url: str):
        """聊天区图片生成完成，下载并显示"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        msg_widget = self._chat_img_gen_msg if hasattr(self, '_chat_img_gen_msg') \
            else ChatMessageWidget("正在加载图片...", is_user=False)
        if msg_widget is self._chat_img_gen_msg:
            self._chat_img_gen_msg = None
        else:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_widget)

        QTimer.singleShot(50, self.scroll_to_bottom)

        if image_url and os.path.isfile(image_url):
            pix = QPixmap(image_url)
            if not pix.isNull():
                self._show_chat_image(pix, msg_widget)
            else:
                msg_widget.update_text(f"图片加载失败: {image_url}")
            if self._chat_img_thread:
                self._chat_img_thread.quit()
                self._chat_img_thread.wait()
            return

        self._dl_thread = QThread()
        class _Dl(QObject):
            finished = Signal(QPixmap)
            error = Signal(str)
            def __init__(self, url):
                super().__init__()
                self.url = url
            def run(self):
                import requests as _r
                try:
                    resp = _r.get(self.url, timeout=30)
                    resp.raise_for_status()
                    pix = QPixmap()
                    pix.loadFromData(resp.content)
                    self.finished.emit(pix)
                except Exception as e:
                    self.error.emit(str(e))

        self._dl = _Dl(image_url)
        self._dl.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl.run)
        self._dl.finished.connect(lambda pix: self._show_chat_image(pix, msg_widget))
        self._dl.error.connect(lambda msg: msg_widget.update_text(f"图片加载失败: {msg}"))
        self._dl.finished.connect(self._dl_thread.quit)
        self._dl_thread.start()

        if self._chat_img_thread:
            self._chat_img_thread.quit()
            self._chat_img_thread.wait()

    def _show_chat_image(self, pix: QPixmap, msg_widget):
        """在聊天消息中显示生成的图片，右上角带复制按钮"""
        import tempfile, os

        # 保存到临时文件
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "generated")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"img_{int(__import__('time').time() * 1000)}.png")
        pix.save(tmp_path, "PNG")

        # 隐藏原有文本标签
        msg_widget.message_label.hide()

        # 清理旧的图片组件（如果有）
        if hasattr(msg_widget, '_image_container') and msg_widget._image_container:
            msg_widget._image_container.setParent(None)
            msg_widget._image_container.deleteLater()

        # 创建图片容器
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setMaximumWidth(420)

        # 图片标签
        img_label = QLabel()
        scaled = pix.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img_label.setPixmap(scaled)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border-radius: 12px;
            }
        """)

        # 复制按钮（右上角悬浮）
        copy_btn = QPushButton("📋 复制")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setFixedSize(56, 24)
        copy_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.55);
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 11px;
                padding: 2px 8px;
            }
            QPushButton:hover {
                background: rgba(0,0,0,0.75);
            }
        """)
        # 点击复制原图
        def _copy():
            QApplication.clipboard().setPixmap(pix)
            copy_btn.setText("✓ 已复制")
            QTimer.singleShot(1500, lambda: copy_btn.setText("📋 复制"))
        copy_btn.clicked.connect(_copy)

        # 叠加布局：图片 + 悬浮按钮
        overlay = QVBoxLayout(container)
        overlay.setContentsMargins(0, 0, 0, 0)
        overlay.setSpacing(0)

        img_wrapper = QWidget()
        img_wrapper.setStyleSheet("background: transparent;")
        img_wrapper_layout = QVBoxLayout(img_wrapper)
        img_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        img_wrapper_layout.addWidget(img_label)

        # 把复制按钮放到右上角
        btn_container = QWidget(img_wrapper)
        btn_container.setStyleSheet("background: transparent;")
        btn_container.setFixedSize(60, 28)
        btn_layout = QVBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 4, 4, 0)
        btn_layout.addWidget(copy_btn, 0, Qt.AlignTop | Qt.AlignRight)

        overlay.addWidget(img_wrapper)

        # 插入到消息布局中（message_label 下方）
        msg_widget.message_container.layout().addWidget(container)
        msg_widget._image_container = container

        # 持久化
        if self.current_session_id:
            self.storage.add_message(self.current_session_id, "assistant", "[AI 生成图片]")
        QTimer.singleShot(50, self.scroll_to_bottom)

    def _on_chat_image_error(self, msg: str):
        """聊天区图片生成失败"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        # 把临时消息替换为错误提示
        err_msg = f"⚠ 图片生成失败: {msg}"
        if hasattr(self, '_chat_img_gen_msg') and self._chat_img_gen_msg:
            self._chat_img_gen_msg.update_text(err_msg)
            self._chat_img_gen_msg.on_streaming_finished()
            self._chat_img_gen_msg = None
        else:
            self.add_message(f"⚠ 图片生成失败: {msg}", is_user=False)

        if self._chat_img_thread:
            self._chat_img_thread.quit()
            self._chat_img_thread.wait()

    def _toggle_terminal_panel(self):
        """切换终端面板显示/隐藏"""
        if self.terminal_widget.isVisible():
            self.terminal_widget.hide()
            self.show_terminal_btn.setText("  显示终端")
        else:
            self.terminal_widget.show()
            self.show_terminal_btn.setText("  隐藏终端")

    def _toggle_background_task_panel(self):
        """切换后台任务面板显示/隐藏"""
        if self.background_task_panel.isVisible():
            self.background_task_panel.hide()
        else:
            self.background_task_panel.show()
            self.background_task_panel.refresh()

    def _on_background_task_clicked(self, task_id: str):
        """点击后台任务条目"""
        try:
            from services.core.background_agent import get_background_manager
            mgr = get_background_manager()
            task = mgr.get_task(task_id)
            if task:
                d = task.to_dict()
                status_icon = {
                    "pending": "⏳", "running": "🔄", "completed": "✅",
                    "failed": "❌", "cancelled": "🚫"
                }.get(d["status"], "❓")
                info = f"{status_icon} 任务: {d['name']}\n状态: {d['status']}\n进度: {d['progress']*100:.0f}%"
                if d.get("duration", 0) > 0:
                    info += f"\n耗时: {d['duration']:.1f}s"
                if d["result"]:
                    info += f"\n结果: {d['result'][:500]}"
                if d["error"]:
                    info += f"\n错误: {d['error'][:500]}"
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "后台任务详情", info)
        except Exception as e:
            pass

    def _show_terminal_button(self):
        """终端按钮已常在，无需操作"""
        pass

    def _hide_terminal_button(self):
        """终端按钮已常在，无需操作"""
        pass

    def _auto_resize_input(self):
        """根据内容自动调整输入框高度：初始单行40px，最多200px，底部固定向上扩展"""
        doc = self.message_input.document()
        layout = doc.documentLayout()

        # 逐块累加文档布局中每个 block 的实际占用高度（含折行）
        total = 0.0
        block = doc.begin()
        while block.isValid():
            rect = layout.blockBoundingRect(block)
            total += rect.height()
            block = block.next()

        if not self.message_input.toPlainText().strip():
            total = self.message_input.fontMetrics().lineSpacing()

        margins = self.message_input.contentsMargins()
        frame = self.message_input.frameWidth() * 2
        height = int(total + margins.top() + margins.bottom() + frame + 10)
        clamped = max(40, min(height, 200))
        if clamped < height:
            self.message_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.message_input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        old_h = self.message_input.height()
        self.message_input.setFixedHeight(clamped)
        if old_h != clamped:
            self.message_input.updateGeometry()
            self._middle_widget.updateGeometry()

    def _on_send_btn_clicked(self):
        """发送/停止按钮点击"""
        if self._is_generating:
            self.on_stop_generation()
        else:
            self.send_message()

    def _set_send_btn_state(self, generating: bool):
        """切换按钮状态：发送 ↔ 停止"""
        self._is_generating = generating
        if generating:
            self.send_btn.setText("■")
            self.send_btn.setToolTip("停止生成")
            self.send_btn.setStyleSheet("""
                QPushButton {
                    background: #ef4444;
                    color: white;
                    border: none;
                    border-radius: 22px;
                    font-size: 16px;
                    font-weight: bold;
                }
                QPushButton:hover { background: #dc2626; }
                QPushButton:pressed { background: #b91c1c; }
            """)
        else:
            self.send_btn.setText("➤")
            self.send_btn.setToolTip("发送消息 (Enter)")
            from ui.styles import get_style as _gs
            self.send_btn.setStyleSheet(_gs('send_btn', self.current_theme))

    def _upload_avatar(self):
        """上传用户头像"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择头像", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if file_path:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                avatar = pixmap.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                ChatMessageWidget.set_user_avatar(avatar)
                save_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "data", "user_avatar.png")
                avatar.save(save_path)
                self._update_avatar_btn_icon()
                self.show_toast("头像已更新")

    def _update_avatar_btn_icon(self):
        """更新头像按钮图标"""
        avatar_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "data", "user_avatar.png")
        if os.path.exists(avatar_path):
            pixmap = QPixmap(avatar_path)
            if not pixmap.isNull():
                icon = pixmap.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                from PySide6.QtGui import QIcon
                self.avatar_btn.setIcon(QIcon(icon))
                self.avatar_btn.setIconSize(self.avatar_btn.size())
                self.avatar_btn.setStyleSheet("""
                    QPushButton {
                        border-radius: 18px;
                        border: 2px solid rgba(99, 102, 241, 0.4);
                        background: transparent;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        border-color: #6e7fe0;
                    }
                """)
                return
        # 默认样式
        self.avatar_btn.setText("👤")
        self.avatar_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.15);
                color: white;
                border: 1.5px solid rgba(255,255,255,0.25);
                border-radius: 18px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.25);
                border-color: rgba(255,255,255,0.4);
            }
        """)

    def _load_saved_avatar(self):
        """启动时加载保存的头像"""
        avatar_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data")
        user_avatar_path = os.path.join(avatar_dir, "user_avatar.png")
        if os.path.exists(user_avatar_path):
            pixmap = QPixmap(user_avatar_path)
            if not pixmap.isNull():
                ChatMessageWidget.set_user_avatar(pixmap)
        self._update_avatar_btn_icon()

    def eventFilter(self, obj, event):
        """拦截输入框事件 + 自绘标题栏拖拽移动 / 双击最大化"""
        # ── 自绘无边框标题栏：非按钮区域拖拽移动窗口 ──
        if getattr(self, '_frameless', False) and obj is not None:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                if not isinstance(obj, QPushButton):
                    wnd = self.windowHandle()
                    if wnd is not None and wnd.startSystemMove():
                        event.accept()
                        return True
            elif etype == QEvent.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
                if not isinstance(obj, QPushButton):
                    event.accept()
                    self._toggle_maximize()
                    return True
        # 拦截输入框的粘贴事件和Enter键发送
        if getattr(self, 'message_input', None) is obj and event.type() == event.Type.KeyPress:
            # Ctrl+V 粘贴图片
            if event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier:
                clipboard = QApplication.clipboard()
                mime = clipboard.mimeData()
                if mime.hasImage():
                    image = clipboard.image()
                    if not image.isNull():
                        self._add_image(image)
                    return True
            # Enter 或 Ctrl+Enter 发送消息
            if (event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter) and not (event.modifiers() & Qt.ControlModifier):
                self._on_send_btn_clicked()
                return True
            # Ctrl+Enter 换行
            if (event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
                # 允许默认行为（插入换行）
                return False
        return super().eventFilter(obj, event)

    def _upload_image(self):
        """打开文件对话框选择图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if file_path:
            image = QImage(file_path)
            if not image.isNull():
                self._add_image(image)

    def _add_image(self, image):
        """添加图片到附件列表并显示预览"""
        from PySide6.QtCore import QBuffer, QIODevice

        # 转为 base64
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        import base64
        b64 = base64.b64encode(buffer.data().data()).decode("utf-8")
        buffer.close()

        # 缩略图
        thumb = image.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._attached_images.append((b64, thumb, image))
        self._refresh_image_previews()

    def _refresh_image_previews(self):
        """刷新图片预览区"""
        # 清空旧预览
        while self.image_preview_layout.count():
            child = self.image_preview_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self._attached_images:
            self.image_preview_area.hide()
            return

        self.image_preview_area.show()
        for i, (b64, thumb, orig) in enumerate(self._attached_images):
            preview = QLabel()
            preview.setPixmap(QPixmap.fromImage(thumb))
            preview.setFixedSize(60, 60)
            preview.setStyleSheet("""
                QLabel {
                    border: 2px solid #d1d1d6;
                    border-radius: 8px;
                    background: #ffffff;
                    padding: 2px;
                }
            """)
            preview.setScaledContents(True)

            # 删除按钮叠加
            wrapper = QWidget()
            wrapper.setFixedSize(64, 64)
            wrapper.setStyleSheet("background: transparent;")
            preview.setParent(wrapper)
            preview.move(2, 2)

            remove_btn = QPushButton("✕", wrapper)
            remove_btn.setFixedSize(18, 18)
            remove_btn.move(46, 0)
            remove_btn.setCursor(Qt.PointingHandCursor)
            remove_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,59,48,0.9);
                    color: white;
                    border-radius: 9px;
                    font-size: 10px;
                    border: none;
                }
                QPushButton:hover { background: rgba(255,59,48,1.0); }
            """)
            remove_btn.clicked.connect(lambda _, idx=i: self._remove_image(idx))

            self.image_preview_layout.addWidget(wrapper)

        self.image_preview_layout.addStretch()

    def _remove_image(self, index: int):
        """移除指定索引的附件"""
        if 0 <= index < len(self._attached_images):
            self._attached_images.pop(index)
            self._refresh_image_previews()

    def _update_context_display(self):
        """更新上下文使用率显示"""
        if not self.current_session_id:
            return
        info = self.chat_service.get_context_info(self.current_session_id)
        tokens = info["token_count"]
        usage = info["usage"]
        percent = int(usage * 100)
        self.context_progress.setValue(percent)
        self.context_percent.setText(f"{percent}%")
        if tokens >= 1000:
            self.context_label.setText(f"上下文: {tokens / 1000:.1f}k token")
        else:
            self.context_label.setText(f"上下文: {tokens} token")
        # 高使用率变红
        if percent >= 80:
            self.context_label.setStyleSheet(
                "color: #ef4444; font-size: 11px; background: transparent; font-weight: bold;")
            self.context_percent.setStyleSheet(
                "color: #ef4444; font-size: 11px; background: transparent; font-weight: bold;")
        else:
            self.context_label.setStyleSheet(
                "color: #86868b; font-size: 11px; background: transparent;")
            self.context_percent.setStyleSheet(
                "color: #86868b; font-size: 11px; background: transparent;")

    def add_welcome_message(self):
        """添加欢迎消息"""
        welcome_text = (
            "  **欢迎使用 Sky Code 智能助手**\n\n"
            "我是你的 AI 编程伙伴，可以帮你：\n\n"
            "  **编写代码** — 支持 Python、JS、C++ 等多种语言\n"
            "  **文件操作** — 创建、读取、修改、删除本地文件\n"
            "  **数据分析** — CSV、Excel 文件分析和可视化\n"
            "  **智能推理** — 复杂问题分步解答\n"
            "  **图片生成** — AI 创作图片\n\n"
            "**快捷键**: Enter 发送 | Ctrl+Enter 换行 | Ctrl+N 新对话\n\n"
            "在下方输入框开始对话吧！"
        )
        self.add_message(welcome_text, is_user=False)

    def add_message(self, text: str, is_user: bool, turn_id: int = 0,
                    timestamp: str = "", thinking_time: str = ""):
        """添加消息到聊天区域"""
        message_widget = ChatMessageWidget(text, is_user,
                                           timestamp=timestamp,
                                           thinking_time=thinking_time)
        # 消息创建时窗口可能尚未挂载，直接按当前主题刷新一次，确保颜色正确
        if hasattr(message_widget, 'apply_theme'):
            message_widget.apply_theme(self.current_theme)

        if is_user:
            # 用户消息包一层 wrapper，方便添加回退按钮
            wrapper = QWidget()
            wrapper.setStyleSheet("background: transparent;")
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)
            wrapper_layout.setSpacing(4)
            wrapper_layout.addStretch(1)  # 弹性空间推到右边
            wrapper_layout.addWidget(message_widget)
            # 右侧不留弹性空间，回退按钮会插在 stretch 和消息之间
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrapper)
            self._last_user_msg_wrapper = wrapper
            self._last_user_turn_id = turn_id
        else:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)

        QTimer.singleShot(50, self.scroll_to_bottom)

    def scroll_to_bottom(self):
        """滚动到底部（用户手动上滚时暂停自动滚动）"""
        if not getattr(self, '_auto_scroll_enabled', True):
            return
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_scroll_value_changed(self, value):
        """检测用户手动滚动：离开底部时暂停自动滚动，回到底部时恢复"""
        scrollbar = self.chat_scroll.verticalScrollBar()
        maximum = scrollbar.maximum()
        # 距离底部 50px 以内视为"在底部"，恢复自动滚动
        at_bottom = value >= maximum - 50
        self._auto_scroll_enabled = at_bottom

    def send_message(self):
        """发送消息（支持文字 + 图片）"""
        message = self.message_input.toPlainText().strip()
        has_images = len(self._attached_images) > 0
        if not message and not has_images:
            return

        # 用户发送新消息时恢复自动滚动
        self._auto_scroll_enabled = True

        # 开始新一轮对话（用于回滚）
        self._current_turn_id = self._rollback_mgr.begin_turn()

        # 检查是否选中了图片生成模型
        display_name = self.model_combo.currentText()
        model_info = find_model_by_display(display_name)
        if model_info and model_info.get("type") == "image":
            # 图片生成模式
            display_text = message or "[图片生成]"
            self.add_message(display_text, is_user=True, turn_id=self._current_turn_id,
                            timestamp=datetime.now().strftime("%H:%M:%S"))
            # 持久化用户消息
            if self.current_session_id:
                self.storage.add_message(self.current_session_id, "user", display_text, self._current_turn_id)
            ref_path = None
            if has_images:
                # 保存第一张附件到临时文件作为参考图
                from PySide6.QtCore import QBuffer, QIODevice, QFile
                import tempfile
                _, _, orig_image = self._attached_images[0]
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                orig_image.save(tmp.name)
                ref_path = tmp.name
                display_text = f"[图生图] {message}" if message else "[图生图]"
                self.add_message(display_text, is_user=True, turn_id=self._current_turn_id,
                                timestamp=datetime.now().strftime("%H:%M:%S"))
            # 移动会话到顶部
            self._move_session_to_top(self.current_session_id)
            self.message_input.clear()
            self._attached_images.clear()
            self._refresh_image_previews()
            self._call_image_api(message, ref_path, model_info["model"])
            return

        # 构建消息内容（纯文本或多模态）
        if has_images and message:
            display_text = f"[图片 x{len(self._attached_images)}] {message}"
        elif has_images:
            display_text = f"[图片 x{len(self._attached_images)}]"
        else:
            display_text = message

        self.add_message(display_text, is_user=True, turn_id=self._current_turn_id,
                        timestamp=datetime.now().strftime("%H:%M:%S"))

        # 发送消息时立即将当前会话移到顶部
        self._move_session_to_top(self.current_session_id)

        # 构建 API 消息格式
        if has_images:
            content_parts = []
            for b64, _, _ in self._attached_images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                })
            if message:
                content_parts.append({"type": "text", "text": message})
            api_message = {"role": "user", "content": content_parts}
            self._pending_api_message = api_message
        else:
            self._pending_api_message = {"role": "user", "content": message}

        # 持久化用户消息到数据库
        if self.current_session_id:
            self.storage.add_message(self.current_session_id, "user", display_text, self._current_turn_id)

        # 自动更新会话标题
        if message and self.current_session_id and self.current_session_id in self.sessions:
            session = self.sessions[self.current_session_id]
            if session["title"] == "新对话":
                title = message[:20] + ("..." if len(message) > 20 else "")
                session["title"] = title
                self._update_session_widget_title(self.current_session_id, title)
                self.storage.update_session_title(self.current_session_id, title)

        # 清空输入和附件
        self.message_input.clear()
        self._attached_images.clear()
        self._refresh_image_previews()

        # ── 多任务拆分检测 ──
        from services.core.agent_service import split_user_tasks
        tasks = split_user_tasks(message, display_name) if message else []
        if tasks and len(tasks) >= 2:
            # 显示任务计划，然后逐条执行
            self._pending_task_queue = list(tasks)
            self._show_task_plan_and_execute(tasks)
        else:
            # 单任务：正常流程
            QTimer.singleShot(200, self.call_llm_api)

    def _show_task_plan_and_execute(self, tasks: list):
        """显示多任务计划面板，然后开始执行第一个任务"""
        plan_widget = TaskPlanWidget()
        plan_widget.set_tasks(tasks)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, plan_widget)
        QTimer.singleShot(50, self.scroll_to_bottom)
        self._task_plan_widget = plan_widget

        # 延迟一点让 UI 渲染，然后开始执行第一个任务
        QTimer.singleShot(300, lambda: self._execute_next_task(0))

    def _execute_next_task(self, task_index: int):
        """执行指定索引的任务（按队列顺序）"""
        if not hasattr(self, '_pending_task_queue') or task_index >= len(self._pending_task_queue):
            # 所有任务完成
            if hasattr(self, '_task_plan_widget'):
                self.show_toast("所有任务已完成 ✅")
            return

        task_text = self._pending_task_queue[task_index]
        # 更新当前任务状态为 running
        if hasattr(self, '_task_plan_widget'):
            self._task_plan_widget.update_task_status(task_index, "running")

        # 将子任务的文本设为 pending API 消息，触发 LLM 调用
        self._current_task_index = task_index
        self._pending_api_message = {"role": "user", "content": task_text}
        self._is_multi_task_mode = True

        # 直接调用 LLM API（不重新添加用户消息）
        self.call_llm_api()

    def _on_multi_task_finished(self):
        """单个子任务完成后的回调 — 标记完成并启动下一个"""
        current_idx = getattr(self, '_current_task_index', 0)
        if hasattr(self, '_task_plan_widget'):
            self._task_plan_widget.update_task_status(current_idx, "done")

        next_idx = current_idx + 1
        if hasattr(self, '_pending_task_queue') and next_idx < len(self._pending_task_queue):
            # 执行下一个任务：短暂间隔后继续
            self._is_multi_task_mode = True
            QTimer.singleShot(500, lambda idx=next_idx: self._execute_next_task(idx))
        else:
            self._is_multi_task_mode = False
            self._set_send_btn_state(False)
            self.status_label.setText("就绪")
            self.status_dot.setStyleSheet(
                "color: #22c55e; font-size: 9px; background: transparent;")

    def _safe_cleanup_previous_thread(self):
        """安全清理上一个仍在运行的 API 线程（非阻塞）。
        断开所有信号连接，请求停止，让旧线程自行退出。
        不调用 wait()，避免阻塞主线程。"""
        old_thread = getattr(self, '_api_thread', None)
        old_worker = getattr(self, '_api_worker', None)
        if old_thread is None and old_worker is None:
            return

        # 断开旧 worker 的所有信号连接，防止旧线程的回调干扰新请求
        if old_worker is not None:
            try:
                old_worker.request_stop()
            except Exception:
                pass
            for sig_name in ['finished', 'error', 'chunk_ready', 'chunk_clear',
                             'agent_step', 'agent_thinking', 'agent_done',
                             'code_event', 'tool_call', 'tool_start',
                             'agent_status', 'thought', 'plan_update',
                             'status_log']:
                try:
                    sig = getattr(old_worker, sig_name)
                    sig.disconnect()
                except Exception:
                    pass
            old_worker.deleteLater()

        # 让旧线程退出事件循环
        if old_thread is not None:
            try:
                old_thread.quit()
            except Exception:
                pass
            old_thread.deleteLater()

        # 清空引用
        self._api_thread = None
        self._api_worker = None
        self._api_worker_id = None

    def call_llm_api(self):
        """调用 LLM API（后台线程，支持文字 + 图片）"""
        # ── 安全检查：如果上一个线程仍在运行，先清理 ──
        self._safe_cleanup_previous_thread()

        # 更新状态为"思考中"
        self.status_dot.setStyleSheet("color: #fbbf24; font-size: 9px; background: transparent;")
        self.status_label.setText("思考中...")
        self._set_send_btn_state(True)

        display_name = self.model_combo.currentText()
        temperature = self.temp_slider.value() / 100
        max_tokens = self.token_slider.value()

        # 记录开始时间
        self._api_start_time = time.time()

        # 使用预构建的 API 消息
        pending = getattr(self, '_pending_api_message', None)
        if not pending:
            return
        self._pending_api_message = None

        user_content = pending["content"]

        message_widget = ChatMessageWidget("", is_user=False)
        message_widget.stop_generation.connect(self.on_stop_generation)
        # 创建时窗口可能尚未挂载，立即按当前主题刷新，避免实时输出文字颜色错误
        if hasattr(message_widget, 'apply_theme'):
            message_widget.apply_theme(self.current_theme)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        QTimer.singleShot(50, self.scroll_to_bottom)

        self._api_full_text = ""
        self._api_status_text = ""
        self._last_html_render_len = 0
        self._api_message_widget = message_widget
        self._current_file_panel = None
        self._chatgpt_login_prompt_shown = False
        self._code_feedback_widget = None  # 每次 API 调用重置
        self._current_diff_widgets = []  # 每次 API 调用重置 diff 视图列表
        self._current_diff_group = None  # 每次 API 调用重置 diff group
        self._plan_widget = None  # 每次 API 调用重置 RePlan 面板
        self._token_count = 0  # 重置 token 计数

        if is_chatgpt_model(display_name):
            self._api_status_text = "正在检测 ChatGPT 登录状态...\n"
            message_widget.set_status_log(self._api_status_text)

        # Agent 模式：可折叠思考组件
        self._thinking_widget = None
        if self.chat_service.agent_mode:
            self._thinking_widget = CollapsibleThinking()
            self._thinking_widget.apply_theme(self.current_theme)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._thinking_widget)
            QTimer.singleShot(50, self.scroll_to_bottom)

        self._api_thread = QThread()
        _ws_path = ""
        if self.current_session_id and self.current_session_id in self.sessions:
            _ws_path = self.sessions[self.current_session_id].get("file_path", "")
        self._api_worker = ApiWorker(
            chat_service=self.chat_service,
            session_id=self.current_session_id,
            user_message=user_content,
            model_display=display_name,
            temperature=temperature,
            max_tokens=max_tokens,
            max_steps=self.steps_slider.value(),
            workspace_path=_ws_path if _ws_path else None,
        )
        # 记录 worker 标识，用于回调中判断是否为当前 worker
        self._api_worker_id = id(self._api_worker)
        self._api_worker.moveToThread(self._api_thread)

        self._api_thread.started.connect(self._api_worker.run)
        self._api_worker.chunk_ready.connect(self._on_api_chunk)
        self._api_worker.chunk_clear.connect(self._on_chunk_clear)
        self._api_worker.finished.connect(self._on_api_finished)
        # 关键：worker 完成后退出线程事件循环，否则线程永不退出
        self._api_worker.finished.connect(self._api_thread.quit)
        self._api_worker.error.connect(self._on_api_error)
        self._api_worker.agent_step.connect(self._on_agent_step)
        self._api_worker.agent_thinking.connect(self._on_agent_thinking)
        self._api_worker.agent_done.connect(self._on_agent_done)
        self._api_worker.code_event.connect(self._on_code_event)
        self._api_worker.tool_call.connect(self._on_tool_call)
        self._api_worker.tool_start.connect(self._on_tool_start)
        self._api_worker.agent_status.connect(self._on_agent_status)
        self._api_worker.thought.connect(self._on_thought)
        self._api_worker.plan_update.connect(self._on_plan_update)
        self._api_worker.status_log.connect(
            self._on_api_status_log, Qt.ConnectionType.QueuedConnection)

        self._api_thread.start()

    def _on_api_status_log(self, text: str):
        """ChatGPT 登录轮询 / 上下文同步等状态日志，显示在聊天框与状态栏"""
        self._api_status_text += text
        if self._api_message_widget:
            self._api_message_widget.set_status_log(self._api_status_text)
        if "正在同步上下文" in text or "正在生成对话摘要" in text:
            self.status_label.setText("正在同步上下文…")
        elif "上下文已同步" in text:
            self.status_label.setText("思考中...")
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_chatgpt_login_prompt(self):
        """未登录 ChatGPT 时在主线程弹框提示"""
        if getattr(self, "_chatgpt_login_prompt_shown", False):
            return
        self._chatgpt_login_prompt_shown = True
        QMessageBox.information(
            self,
            "ChatGPT 登录",
            "请先点击「启动 ChatGPT Chrome」，在打开的真实 Chrome 中手动登录后再发送消息。"
            "（推荐改用 ChatGPT-4o (API) 并配置 OPENAI_API_KEY，无需浏览器。）",
        )

    def _update_chatgpt_login_btn_visibility(self, display_name: str):
        if hasattr(self, "chatgpt_login_btn"):
            self.chatgpt_login_btn.setVisible(is_chatgpt_model(display_name))

    def _open_chatgpt_login(self):
        if getattr(self, "_chatgpt_login_thread", None) and self._chatgpt_login_thread.isRunning():
            self.show_toast("ChatGPT 浏览器正在打开...")
            return
        self.show_toast("正在启动 Chrome，请在窗口中手动登录 ChatGPT...")
        self._chatgpt_login_thread = QThread()
        self._chatgpt_login_worker = ChatGPTLoginWorker()
        self._chatgpt_login_worker.moveToThread(self._chatgpt_login_thread)
        self._chatgpt_login_thread.started.connect(self._chatgpt_login_worker.run)
        self._chatgpt_login_worker.finished.connect(self._chatgpt_login_thread.quit)
        self._chatgpt_login_worker.error.connect(
            lambda msg: self.show_toast(f"打开 ChatGPT 失败: {msg}"))
        self._chatgpt_login_thread.start()

    def _on_api_chunk(self, chunk: str):
        self._api_full_text += chunk
        # token 计数（粗略估计：按空格分词）
        if not hasattr(self, '_token_count'):
            self._token_count = 0
        self._token_count += len(chunk.split())

        # 16ms 定时器：始终运行，负责同步 _raw_text（HTML 模式下只更新变量不碰 UI）
        if not hasattr(self, '_chunk_render_timer'):
            self._chunk_render_timer = QTimer()
            self._chunk_render_timer.setSingleShot(True)
            self._chunk_render_timer.timeout.connect(self._flush_chunk_render)
        if not self._chunk_render_timer.isActive():
            self._chunk_render_timer.start(16)

        # HTML 定时器：用 Markdown 快速渲染
        if not hasattr(self, '_chunk_html_timer'):
            self._chunk_html_timer = QTimer()
            self._chunk_html_timer.setSingleShot(True)
            self._chunk_html_timer.timeout.connect(self._flush_chunk_html)
        if not self._chunk_html_timer.isActive():
            self._chunk_html_timer.start(300)

        # 状态标签和滚动
        if not hasattr(self, '_status_scroll_timer'):
            self._status_scroll_timer = QTimer()
            self._status_scroll_timer.setSingleShot(True)
            self._status_scroll_timer.timeout.connect(self._flush_status_and_scroll)
        if not self._status_scroll_timer.isActive():
            self._status_scroll_timer.start(150)

    def _flush_chunk_render(self):
        """每帧 (~16ms) 同步 _raw_text 到 widget。
        纯文本模式下直接 setText 显示；HTML 模式下只更新 _raw_text 变量，
        不碰 UI（由 HTML 定时器负责渲染）。"""
        if self._api_message_widget and self._api_full_text:
            self._api_message_widget.update_text(self._api_full_text, streaming=True)

    def _flush_chunk_html(self):
        """慢速（~300ms）用 Markdown 快速渲染，让代码块背景等实时可见"""
        if not self._api_message_widget or not self._api_full_text:
            return
        last_len = getattr(self, '_last_html_render_len', 0)
        current_len = len(self._api_full_text)
        if current_len != last_len and hasattr(self._api_message_widget, '_stream_render_html'):
            self._api_message_widget._stream_render_html()
            self._last_html_render_len = current_len
        # 重新启动 HTML 定时器
        if hasattr(self, '_chunk_html_timer'):
            self._chunk_html_timer.start(300)

    def _flush_status_and_scroll(self):
        """节流更新 token 计数标签和滚动位置"""
        if self._api_message_widget:
            elapsed = time.time() - getattr(self, '_api_start_time', time.time())
            rate = self._token_count / elapsed if elapsed > 0 else 0
            self._api_message_widget.update_gen_status(self._token_count, rate)
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_chunk_clear(self):
        """清除已流式输出的内容（浏览器模型工具调用场景）"""
        # 停止节流定时器
        if hasattr(self, '_chunk_render_timer') and self._chunk_render_timer.isActive():
            self._chunk_render_timer.stop()
        if hasattr(self, '_chunk_html_timer') and self._chunk_html_timer.isActive():
            self._chunk_html_timer.stop()
        if hasattr(self, '_status_scroll_timer') and self._status_scroll_timer.isActive():
            self._status_scroll_timer.stop()
        self._api_full_text = ""
        self._last_html_render_len = 0
        if self._api_message_widget:
            self._api_message_widget.update_text("")

    def _on_agent_thinking(self, text: str):
        """Agent 模式：显示初始思考状态"""
        if self._thinking_widget:
            self._thinking_widget.expand()
            self._thinking_widget.set_status("思考中...")
            self._thinking_widget.add_thought(text)
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_thought(self, text: str):
        """Agent 模式：结构化思考文本"""
        if self._thinking_widget:
            self._thinking_widget.add_thought(text)
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_tool_call(self, tool_name: str, tool_input: str,
                      tool_output: str, ok: bool):
        """Agent 模式：结构化工具调用 — 添加 ToolCallCard 到思考面板"""
        if self._thinking_widget:
            self._thinking_widget.add_tool_call(
                tool_name, tool_input, tool_output, ok)
        # 文件操作完成后移除转圈指示器
        if tool_name in ("write_file", "edit_file"):
            self._resolve_file_spinner(tool_input)
        # 工具执行完成，清除状态行
        if self._api_message_widget:
            self._api_message_widget.clear_agent_status()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _resolve_file_spinner(self, tool_input: str):
        """从 tool_input 字符串中提取 file_path 并移除转圈"""
        import re
        # 尝试匹配 dict 格式: 'file_path': '...' 或 "file_path": "..."
        fp_match = re.search(r"['\"]file_path['\"]\s*:\s*['\"]([^'\"]+)['\"]", tool_input)
        if fp_match:
            file_path = fp_match.group(1)
            if getattr(self, '_code_feedback_widget', None):
                self._code_feedback_widget._resolve_file_editing(file_path)
            # 同步停止文件列表面板中的转圈
            if getattr(self, '_current_file_panel', None):
                self._current_file_panel.resolve_file_editing(file_path, op_type="", added=0, removed=0)

    def _on_tool_start(self, tool_name: str, file_path: str):
        """Agent 模式：工具开始执行 — 在 CodeFeedbackWidget 和文件列表显示转圈"""
        if not file_path:
            return
        # 延迟初始化 CodeFeedbackWidget
        if not getattr(self, '_code_feedback_widget', None):
            from ui.widgets import CodeFeedbackWidget
            self._code_feedback_widget = CodeFeedbackWidget()
            self.chat_layout.insertWidget(
                self.chat_layout.count() - 1, self._code_feedback_widget)
        self._code_feedback_widget.add_file_editing(file_path, tool_name)
        # 在文件列表面板中标记文件正在编辑（显示转圈）
        panel = self._get_or_create_file_panel()
        panel.mark_file_editing(file_path, tool_name)
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_agent_status(self, tool_name: str, action: str):
        """Agent 模式：工具执行状态更新 — 显示在消息下方的状态行"""
        if self._api_message_widget:
            self._api_message_widget.set_agent_status(tool_name, action)
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_agent_step(self, step_text: str):
        """Agent 模式：兼容旧步骤文本（终端检测等）"""
        QTimer.singleShot(0, self.scroll_to_bottom)

        # 检测文件操作并显示记录
        self._check_file_operation(step_text)

        # 检测是否调用了终端工具（run_command 或 execute_code）
        if "run_command" in step_text or "execute_code" in step_text:
            self._show_terminal_button()
            # 提取命令和输出
            import re
            cmd_match = re.search(r"输入: `(.+?)`", step_text)
            result_match = re.search(r"结果: (.+?)$", step_text, re.DOTALL)
            if cmd_match:
                self.terminal_widget.append_command(cmd_match.group(1))
            if result_match:
                self.terminal_widget.append_output(result_match.group(1)[:500])

    def _on_code_event(self, event_type: str, file_path: str, ok: bool, detail: str):
        """代码操作实时反馈 — 在聊天框中创建/更新 CodeFeedbackWidget"""
        # 延迟初始化或复用 CodeFeedbackWidget
        if not getattr(self, '_code_feedback_widget', None):
            self._code_feedback_widget = CodeFeedbackWidget()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._code_feedback_widget)

        w = self._code_feedback_widget

        # 语法检查事件到达时，文件已写入完毕，停止转圈
        if file_path and event_type == "syntax_check":
            w.resolve_file_editing(file_path)
            if getattr(self, '_current_file_panel', None):
                self._current_file_panel.resolve_file_editing(file_path, op_type="修改", added=0, removed=0)

        if event_type == "syntax_check":
            w.add_syntax_check(file_path, ok, detail)
        elif event_type == "execution":
            w.add_execution_result(file_path, ok, detail)

        w.set_final_status(ok)
        QTimer.singleShot(50, self.scroll_to_bottom)

    def _on_code_feedback_from_tool(self, event_type: str, data: dict):
        """工具层回调 — 从任意线程安全地更新 CodeFeedbackWidget（通过 QTimer 回到主线程）"""
        QTimer.singleShot(0, lambda: self._handle_tool_code_feedback(event_type, data))

    def _handle_tool_code_feedback(self, event_type: str, data: dict):
        """主线程中处理工具层的代码反馈"""
        file_path = data.get("file", "")
        if not file_path:
            return

        if not getattr(self, '_code_feedback_widget', None):
            self._code_feedback_widget = CodeFeedbackWidget()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._code_feedback_widget)

        w = self._code_feedback_widget

        if event_type == "syntax_error":
            w.add_syntax_check(file_path, False, data.get("error", ""))
        elif event_type == "compile_error":
            w.add_syntax_check(file_path, False, data.get("error", ""))
        elif event_type == "syntax_ok":
            w.add_syntax_check(file_path, True, "")
        elif event_type == "execute_ok":
            w.add_execution_result(file_path, True, data.get("output", ""))
        elif event_type == "execute_error":
            w.add_execution_result(file_path, False, data.get("error", ""))
        elif event_type == "execute_timeout":
            w.add_execution_result(file_path, False, "执行超时 (60秒)")

        w.set_final_status(event_type in ("syntax_ok", "execute_ok"))
        QTimer.singleShot(50, self.scroll_to_bottom)

        # ── 同步代码编辑器：Agent 写入的文件如果正在编辑器中打开，自动刷新 ──
        if hasattr(self, 'code_editor_panel'):
            editor_path = self.code_editor_panel.get_file_path()
            if editor_path and os.path.abspath(editor_path) == os.path.abspath(file_path):
                # 文件被 Agent 修改，延迟刷新（避免文件写入未完成）
                QTimer.singleShot(100, self.code_editor_panel.reload_from_disk)

    def _on_diff_from_tool(self, file_path: str, old_content: str, new_content: str, applied: bool = True):
        """edit_file 工具的 diff 回调 — 在聊天框中插入 DiffViewWidget（线程安全）"""
        QTimer.singleShot(0, lambda: self._handle_diff_display(file_path, old_content, new_content, applied))

    def _handle_diff_display(self, file_path: str, old_content: str, new_content: str, applied: bool):
        """主线程中创建并显示 DiffViewWidget（多文件时使用 DiffGroupWidget 合并）"""
        diff_widget = DiffViewWidget(file_path, old_content, new_content, applied=applied)

        # 获取或创建 DiffGroupWidget
        if not hasattr(self, '_current_diff_group') or self._current_diff_group is None:
            self._current_diff_group = DiffGroupWidget()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._current_diff_group)
            # 重置：下轮对话时创建新的 group
            self._current_diff_widgets = []

        if not hasattr(self, '_current_diff_widgets'):
            self._current_diff_widgets = []

        self._current_diff_widgets.append(diff_widget)
        self._current_diff_group.add_diff(diff_widget)

        QTimer.singleShot(50, self.scroll_to_bottom)

    def _emit_file_operation(self, operation_type: str, file_path: str, added: int = 0, removed: int = 0):
        """发射文件操作信号（可在任意线程调用）"""
        self._file_op_signal.operation.emit(operation_type, file_path, added, removed)

    def _on_file_operation(self, operation_type: str, file_path: str, added: int = 0, removed: int = 0):
        """回滚管理器的回调函数，显示文件操作记录（主线程中执行）"""
        file_name = os.path.basename(file_path)
        if operation_type == 'create':
            self._add_file_operation_record("新增", file_name, added=str(added), file_path=file_path)
            if self.current_session_id:
                self.storage.add_file_operation(
                    self.current_session_id, self._current_turn_id, "新增", file_path or file_name, added, 0)
        elif operation_type == 'modify':
            self._add_file_operation_record("修改", file_name, added=str(added), removed=str(removed), file_path=file_path)
            if self.current_session_id:
                self.storage.add_file_operation(
                    self.current_session_id, self._current_turn_id, "修改", file_path or file_name, added, removed)
        elif operation_type == 'delete':
            self._add_file_operation_record("删除", file_name, removed=str(removed), file_path=file_path)
            if self.current_session_id:
                self.storage.add_file_operation(
                    self.current_session_id, self._current_turn_id, "删除", file_path or file_name, 0, removed)

    def _check_file_operation(self, step_text: str):
        """检测文件操作并显示记录（用于 run_command 中的删除操作）"""
        import re
        # 检测 run_command 中的文件操作（如 del, rm, move）
        if "run_command" in step_text:
            if any(cmd in step_text for cmd in ["del ", "rm ", "Remove-Item"]):
                file_match = re.search(r"(?:del|rm|Remove-Item)\s+[`\"']?([^\s`\"']+)[`\"']?", step_text)
                if file_match:
                    file_name = os.path.basename(file_match.group(1))
                    self._add_file_operation_record("删除", file_name)

    def _get_or_create_file_panel(self) -> FileChangesPanel:
        if self._current_file_panel is None:
            self._current_file_panel = FileChangesPanel()
            attach_target = getattr(self, "_api_message_widget", None)
            if not self._is_widget_in_chat(attach_target):
                attach_target = self._find_last_assistant_message_widget()
            if attach_target and hasattr(attach_target, "attach_file_panel"):
                attach_target.attach_file_panel(self._current_file_panel)
            else:
                wrapper = QWidget()
                wrapper.setStyleSheet("background: transparent;")
                wl = QHBoxLayout(wrapper)
                wl.setContentsMargins(16, 0, 16, 0)
                wl.addWidget(self._current_file_panel)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrapper)
        return self._current_file_panel

    def _add_file_operation_record(self, op_type: str, file_name: str,
                                   added: str = "0", removed: str = "0",
                                   file_path: str = ""):
        """添加文件操作到可展开的文件列表"""
        path = file_path or file_name
        panel = self._get_or_create_file_panel()
        panel.add_file(op_type, path, added=added, removed=removed)
        QTimer.singleShot(100, self.scroll_to_bottom)

    def _on_agent_done(self):
        """Agent 模式：思考完成，自动折叠"""
        if self._thinking_widget:
            self._thinking_widget.set_final()

    # ── RePlan 计划列表 ────────────────────────────────

    def _on_plan_update(self, action: str, event_data: dict, step_index: int):
        """处理 RePlan 模式下的计划事件，在聊天框中展示/更新计划列表"""
        steps_data = event_data.get("steps", [])
        plan_version = event_data.get("plan_version", 0)

        if action == "plan_start":
            # 创建新的计划面板
            self._plan_widget = TaskPlanWidget()
            tasks = [{
                "text": f"[{s.get('agent_display', s.get('agent_name', ''))}] {s.get('description', '')}",
                "status": s.get("status", "pending"),
                "step_id": s.get("id", ""),
                "agent_name": s.get("agent_name", ""),
            } for s in steps_data]
            self._plan_widget.set_tasks_extended(tasks)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._plan_widget)
            # 保存 steps 快照供 _on_api_finished 兜底使用
            self._last_plan_steps = steps_data
            QTimer.singleShot(50, self.scroll_to_bottom)

        elif action == "plan_step_update" and hasattr(self, '_plan_widget') and self._plan_widget:
            # 更新单步骤状态
            if 0 <= step_index < len(steps_data):
                s = steps_data[step_index]
                status = s.get("status", "pending")
                result = s.get("result", "")
                self._plan_widget.update_task_status_extended(step_index, status, result)
            # 同时更新快照中对应步骤的状态
            if hasattr(self, '_last_plan_steps') and 0 <= step_index < len(self._last_plan_steps):
                self._last_plan_steps[step_index] = dict(
                    self._last_plan_steps[step_index],
                    **{k: v for k, v in steps_data[step_index].items() if k in ("status", "result")}
                ) if step_index < len(steps_data) else self._last_plan_steps[step_index]

        elif action == "plan_replan" and hasattr(self, '_plan_widget') and self._plan_widget:
            # 重规划：刷新计划列表
            tasks = [{
                "text": f"[{s.get('agent_display', s.get('agent_name', ''))}] {s.get('description', '')}",
                "status": s.get("status", "pending"),
                "step_id": s.get("id", ""),
                "agent_name": s.get("agent_name", ""),
            } for s in steps_data]
            self._plan_widget.set_tasks_extended(tasks)
            self._last_plan_steps = steps_data
            QTimer.singleShot(50, self.scroll_to_bottom)

        elif action == "plan_done":
            # 计划完成：用最终状态刷新面板，确保所有步骤显示正确的 ✓/✗
            if hasattr(self, '_plan_widget') and self._plan_widget:
                tasks = [{
                    "text": f"[{s.get('agent_display', s.get('agent_name', ''))}] {s.get('description', '')}",
                    "status": s.get("status", "pending"),
                    "step_id": s.get("id", ""),
                    "agent_name": s.get("agent_name", ""),
                    "result": s.get("result", ""),
                } for s in steps_data]
                self._plan_widget.set_tasks_extended(tasks)
                # 不自动折叠，保持展开让用户查看最终任务状态
                # self._plan_widget.collapse()
            self._last_plan_steps = steps_data
            QTimer.singleShot(50, self.scroll_to_bottom)

    def _save_incomplete_ai_response(self):
        """关闭软件时保存未完成的 AI 输出到数据库，防止内容丢失"""
        # 只有当 API 正在运行且已有部分输出时才需要保存
        if not getattr(self, '_api_worker', None):
            return
        if not getattr(self, '_api_full_text', None):
            return
        if not self.current_session_id:
            return

        # 刷新节流定时器，确保 _api_full_text 是最新的
        if hasattr(self, '_chunk_render_timer') and self._chunk_render_timer.isActive():
            self._chunk_render_timer.stop()
            self._flush_chunk_render()
        if hasattr(self, '_chunk_html_timer') and self._chunk_html_timer.isActive():
            self._chunk_html_timer.stop()

        # 从 widget 获取最终文本（可能比 _api_full_text 更完整，因为 widget 可能做了格式化）
        ai_text = ""
        if self._api_message_widget:
            ai_text = self._api_message_widget.get_text()
        if not ai_text:
            ai_text = self._api_full_text.strip()
        if not ai_text:
            return

        # 检查是否已经保存过（_on_api_finished 可能已经执行）
        # 通过查询数据库中该 turn_id 是否已有 assistant 消息来判断
        try:
            existing = self.storage.get_messages(self.current_session_id)
            turn_id = getattr(self, '_current_turn_id', 0)
            already_saved = any(
                m["role"] == "assistant" and m.get("turn_id", 0) == turn_id
                for m in existing
            )
            if already_saved:
                return

            # 保存部分输出，标记为未完成
            self.storage.add_message(
                self.current_session_id, "assistant",
                ai_text + "\n\n---\n*⚠️ 输出因软件关闭而中断，以上为已生成的部分内容。*",
                turn_id,
            )

            # 同步到内存记忆
            memory = self.chat_service.memory_service.get_or_create(self.current_session_id)
            memory.add_ai_message(ai_text)

            # 更新会话列表预览
            if self.current_session_id in self.sessions:
                session = self.sessions[self.current_session_id]
                session["messages"] = self._collect_messages()
                list_item = session.get("list_item")
                if list_item:
                    widget = self.session_list.itemWidget(list_item)
                    if isinstance(widget, SessionItemWidget):
                        widget.set_preview(ai_text[:80])
                        widget.set_msg_count(len(session["messages"]))

            import logging
            logging.getLogger(__name__).info(
                f"已保存中断的 AI 输出 ({len(ai_text)} 字符) 到会话 {self.current_session_id}"
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"保存中断的 AI 输出失败: {e}")

    def _on_api_finished(self):
        """API 调用完成"""
        # 如果 _safe_cleanup_previous_thread 已断开旧信号，旧线程的回调不会到达这里
        # 如果 _api_worker_id 为 None，说明已被清理（如新请求已启动），跳过
        if getattr(self, '_api_worker_id', None) is None:
            return

        try:
            # 兜底清理：确保所有文件编辑转圈停止
            if getattr(self, '_code_feedback_widget', None):
                self._code_feedback_widget.resolve_all_pending()
            if getattr(self, '_current_file_panel', None):
                self._current_file_panel.resolve_all_pending()

            # 确保节流定时器的更新被刷新
            if hasattr(self, '_chunk_render_timer') and self._chunk_render_timer.isActive():
                self._chunk_render_timer.stop()
                self._flush_chunk_render()
            if hasattr(self, '_chunk_html_timer') and self._chunk_html_timer.isActive():
                self._chunk_html_timer.stop()
            if hasattr(self, '_status_scroll_timer') and self._status_scroll_timer.isActive():
                self._status_scroll_timer.stop()
                self._flush_status_and_scroll()

            # 计算耗时
            elapsed = time.time() - getattr(self, '_api_start_time', time.time())
            if elapsed >= 60:
                time_str = f"⏱ 耗时 {int(elapsed // 60)}分{int(elapsed % 60)}秒"
            elif elapsed >= 1:
                time_str = f"⏱ 耗时 {elapsed:.1f}秒"
            else:
                time_str = f"⏱ 耗时 {int(elapsed * 1000)}ms"

            if self._api_message_widget:
                self._api_message_widget.set_thinking_time(time_str)
                # 完成生成状态
                token_count = getattr(self, '_token_count', 0)
                self._api_message_widget.finalize_gen_status(token_count, elapsed)
                if self._api_full_text:
                    self._api_message_widget.update_text(self._api_full_text)
                self._api_message_widget.finalize_markdown()
                ai_text = self._api_message_widget.get_text()
                if ai_text and self.current_session_id:
                    self.storage.add_message(self.current_session_id, "assistant", ai_text, self._current_turn_id)

            # 重置 token 计数
            self._token_count = 0
            self._last_html_render_len = 0

            # 添加回退按钮（每轮对话都可以回退）
            turn_id = self._current_turn_id
            if turn_id > 0:
                self._add_rollback_button(turn_id)

            # 更新上下文使用率
            self._update_context_display()
            # 将当前会话移到列表顶部（最新消息的会话排在最前面）
            self._move_session_to_top(self.current_session_id)

            # ── 确保 task plan 面板显示最终状态 ──
            # plan_done 事件可能在 _on_api_finished 之前未被处理，兜底刷新
            if hasattr(self, '_plan_widget') and self._plan_widget:
                last_plan = getattr(self, '_last_plan_steps', None)
                if last_plan:
                    # 将原始 steps_data 转换为 set_tasks_extended 所需的格式
                    # last_plan 中的元素有 description/agent_name/status 等键，
                    # 但 set_tasks_extended 期望 text 键，必须做格式转换
                    tasks = [{
                        "text": f"[{s.get('agent_display', s.get('agent_name', ''))}] {s.get('description', '')}",
                        "status": s.get("status", "pending"),
                        "step_id": s.get("id", ""),
                        "agent_name": s.get("agent_name", ""),
                        "result": s.get("result", ""),
                    } for s in last_plan]
                    self._plan_widget.set_tasks_extended(tasks)
                # 不自动折叠，保持展开让用户查看任务状态
                # self._plan_widget.collapse()

            # ── 多任务模式：当前任务完成 → 触发下一个任务 ──
            if getattr(self, '_is_multi_task_mode', False):
                self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
                self.status_label.setText("就绪")
                QTimer.singleShot(200, self._on_multi_task_finished)
                return

            # 单任务正常完成流程
            self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
            self.status_label.setText("在线")
            self._set_send_btn_state(False)

        except Exception as e:
            print(f"_on_api_finished 异常: {e}")
        finally:
            self._cleanup_api_thread()

    def _move_session_to_top(self, session_id: str):
        """将会话移到列表顶部"""
        if not session_id or session_id not in self.sessions:
            return
        session = self.sessions[session_id]
        list_item = session.get("list_item")
        widget = session.get("widget")
        if not list_item:
            return
        # 更新时间显示
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        session["time"] = current_time
        if widget:
            widget.set_time(current_time)
        # 从当前位置移除
        row = self.session_list.row(list_item)
        if row > 0:  # 只有不在顶部时才移动
            self.session_list.takeItem(row)
            # 插入到顶部
            self.session_list.insertItem(0, list_item)
            self.session_list.setItemWidget(list_item, widget)
            # 更新 sessions 字典顺序
            new_sessions = {session_id: session}
            for sid, s in self.sessions.items():
                if sid != session_id:
                    new_sessions[sid] = s
            self.sessions = new_sessions

    def _cleanup_api_thread(self):
        """清理 API 线程和 Worker（非阻塞）。

        通过断开信号和 quit() 让线程自行退出，不调用 wait() 避免阻塞主线程。
        线程退出后由 deleteLater 自动清理。"""
        worker = getattr(self, '_api_worker', None)
        thread = getattr(self, '_api_thread', None)

        # 断开信号，防止延迟回调
        if worker is not None:
            for sig_name in ['finished', 'error', 'chunk_ready', 'chunk_clear',
                             'agent_step', 'agent_thinking', 'agent_done',
                             'code_event', 'tool_call', 'tool_start',
                             'agent_status', 'thought', 'plan_update',
                             'status_log']:
                try:
                    sig = getattr(worker, sig_name)
                    sig.disconnect()
                except Exception:
                    pass
            worker.deleteLater()

        if thread is not None:
            try:
                thread.quit()
            except Exception:
                pass
            thread.deleteLater()

        self._api_worker = None
        self._api_thread = None
        self._api_worker_id = None

    def _add_rollback_button(self, turn_id: int):
        """给上一条用户消息添加回退按钮"""
        wrapper = getattr(self, '_last_user_msg_wrapper', None)
        if not wrapper:
            return
        self._add_rollback_button_for_wrapper(turn_id, wrapper)

    def _add_rollback_button_for_wrapper(self, turn_id: int, wrapper: QWidget):
        """给指定的 wrapper 添加回退按钮"""
        if not wrapper:
            return
        rollback_btn = QPushButton("↩ 回退")
        rollback_btn.setFixedSize(60, 24)
        rollback_btn.setCursor(Qt.PointingHandCursor)
        rollback_btn.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.1);
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.2);
                border-color: #ef4444;
            }
        """)
        _tid = turn_id
        rollback_btn.clicked.connect(lambda checked, tid=_tid: self._on_rollback(tid))
        # 插入到 stretch 之后、消息之前（回退按钮在消息左边）
        layout = wrapper.layout()
        layout.insertWidget(layout.count() - 1, rollback_btn)

    def _on_rollback(self, turn_id: int):
        """执行回滚"""
        result = self._rollback_mgr.rollback(turn_id)
        restored = result["restored"]
        deleted = result["deleted"]
        errors = result["errors"]

        parts = []
        if restored:
            file_list = ", ".join([os.path.basename(f) for f in restored])
            parts.append(f"已恢复 {len(restored)} 个文件: {file_list}")
        if deleted:
            file_list = ", ".join([os.path.basename(f) for f in deleted])
            parts.append(f"已删除 {len(deleted)} 个新建文件: {file_list}")
        if errors:
            parts.append(f"错误: {'; '.join(errors)}")

        msg = "↩ 回滚完成：" + "，".join(parts) if parts else "↩ 本轮没有文件修改需要回滚"
        self.add_message(msg, is_user=False)
        self.show_toast("回滚完成")

    def _on_api_error(self, error_msg: str):
        # 检查是否是当前 worker 的回调
        if getattr(self, '_api_worker_id', None) is None:
            return
        # 恢复状态
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")

        if "MIMO_API_KEY" in error_msg or "未找到环境变量" in error_msg:
            text = ("⚠ 未配置 API Key，请设置环境变量 MIMO_API_KEY\n\n"
                    f"设置方法：set MIMO_API_KEY=your-api-key\n\n"
                    f"详细错误：{error_msg}")
        else:
            text = f"⚠ 请求出错：{error_msg}"
        if self._api_message_widget:
            self._api_message_widget.set_status_log("")
        self._api_message_widget.update_text(text)
        self._api_message_widget.finalize_markdown()
        if "请先" in error_msg or "ChatGPT" in error_msg and "登录" in error_msg:
            self._on_chatgpt_login_prompt()
        # 持久化错误消息
        if self.current_session_id:
            self.storage.add_message(self.current_session_id, "assistant", text)

        # ── 多任务模式：当前任务失败 → 标记错误，继续下一个 ──
        if getattr(self, '_is_multi_task_mode', False):
            current_idx = getattr(self, '_current_task_index', 0)
            if hasattr(self, '_task_plan_widget'):
                self._task_plan_widget.update_task_status(current_idx, "error")
            self._set_send_btn_state(False)
            # 不在这里调用 _cleanup_api_thread，由 _on_api_finished 的 finally 统一清理
            QTimer.singleShot(300, self._on_multi_task_finished)
            return

        self._set_send_btn_state(False)
        # 不在这里调用 _cleanup_api_thread，由 _on_api_finished 的 finally 统一清理

    def on_stop_generation(self):
        """停止生成"""
        if hasattr(self, '_api_worker') and self._api_worker:
            self._api_worker.request_stop()
        # 注意：不在这里调用 _cleanup_api_thread()，因为线程可能正阻塞在网络 I/O 上。
        # 设置 _stop 标志后，ApiWorker.run() 会在下一次 generator yield 时自行退出，
        # 随后触发的 finished/error 信号会在 _on_api_finished finally 中完成清理。
        # 如果用户在停止后立即发送新消息，_safe_cleanup_previous_thread 会处理旧线程。
        self._set_send_btn_state(False)
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self.show_toast("已停止生成")

    # ---- 会话管理 ----

    def _generate_session_id(self) -> str:
        self._session_counter += 1
        return f"session_{self._session_counter}"

    def _load_sessions_from_db(self):
        """启动时从数据库加载历史会话"""
        db_sessions = self.storage.get_all_sessions()
        if not db_sessions:
            # 数据库为空，创建默认会话
            self._create_new_session("新对话")
            return
        for s in db_sessions:
            sid = s["id"]
            title = s["title"]
            created = s["created_at"]
            # 提取计数器
            try:
                num = int(sid.split("_")[1])
                if num > self._session_counter:
                    self._session_counter = num
            except (IndexError, ValueError):
                pass
            # 创建 UI 条目
            display_time = created[:16].replace("T", " ")
            # 获取最后一条消息作为预览
            db_msgs_for_preview = self.storage.get_display_messages(sid)
            last_preview = ""
            msg_count = len(db_msgs_for_preview)
            if db_msgs_for_preview:
                last = db_msgs_for_preview[-1]
                last_preview = last[0][:80] if last[0] else ""

            item_widget = SessionItemWidget(title, display_time,
                                            preview=last_preview, msg_count=msg_count,
                                            theme=getattr(self, 'current_theme', 'light'))
            self._connect_session_item_signals(item_widget)
            list_item = QListWidgetItem()
            list_item.setSizeHint(item_widget.sizeHint())
            self.session_list.addItem(list_item)
            self.session_list.setItemWidget(list_item, item_widget)
            # 从 DB 加载消息
            messages = self.storage.get_display_messages(sid)
            self.sessions[sid] = {
                "title": title,
                "time": display_time,
                "messages": messages,
                "file_path": s.get("file_path", ""),
                "list_item": list_item,
                "widget": item_widget,
            }
            # 恢复 LangChain 记忆
            db_msgs = self.storage.get_messages(sid)
            memory = self.chat_service.memory_service.get_or_create(sid)
            for msg in db_msgs:
                if msg["role"] == "user":
                    memory.add_user_message(msg["content"])
                elif msg["role"] == "assistant":
                    memory.add_ai_message(msg["content"])

        # 默认选中第一个会话
        if self.sessions:
            first_id = list(self.sessions.keys())[0]
            self.current_session_id = first_id
            self.chat_service.switch_session(first_id)
            # 使用带时间戳的消息格式
            display_messages = self.storage.get_display_messages(first_id)
            self._restore_chat_messages(display_messages)
            # 恢复文件树路径（不自动切换到文件 Tab）
            fp = self.sessions[first_id].get("file_path", "")
            if fp and os.path.isdir(fp):
                index = self.file_model.setRootPath(fp)
                self.file_tree.setRootIndex(index)
                folder_name = os.path.basename(fp.rstrip("/\\")) or fp
                self._file_title_label.setText(f"📂 {folder_name}")
                self._update_breadcrumb(fp)
                # 后台自动索引：恢复工作区时预热缓存
                self._auto_index_workspace(fp)
            else:
                # 初始无工作区，设置空文件树根并显示空状态
                self.file_model.setRootPath("")
                self.file_tree.setRootIndex(QModelIndex())
            self._update_file_view_state()
            # 初始化上下文显示
            self._update_context_display()

    def _collect_messages(self) -> list:
        """收集当前聊天区的所有消息 [(text, is_user), ...]"""
        messages = []
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessageWidget):
                w = item.widget()
                messages.append((w.get_text(), w.is_user))
        return messages

    def _generate_title(self, messages: list) -> str:
        """根据第一条用户消息自动生成会话标题"""
        for text, is_user in messages:
            if is_user:
                return text[:20] + ("..." if len(text) > 20 else "")
        return None  # 没有用户消息时返回 None，保留原标题

    def save_current_session(self):
        """保存当前会话到 sessions 字典（记忆由 ChatService 自动管理）"""
        if not self.current_session_id:
            return
        messages = self._collect_messages()
        title = self._generate_title(messages)
        self.sessions[self.current_session_id]["messages"] = messages
        # 更新会话列表项的预览和消息数
        session = self.sessions[self.current_session_id]
        list_item = session.get("list_item")
        if list_item:
            widget = self.session_list.itemWidget(list_item)
            if isinstance(widget, SessionItemWidget):
                last_msg = messages[-1][0][:80] if messages else ""
                widget.set_preview(last_msg)
                widget.set_msg_count(len(messages))
        # 只有生成了有效标题才更新
        if title:
            self.sessions[self.current_session_id]["title"] = title
            self._update_session_widget_title(self.current_session_id, title)
            # 同步标题到数据库
            self.storage.update_session_title(self.current_session_id, title)

    def _update_session_widget_title(self, session_id: str, title: str):
        """更新会话列表项的标题"""
        session = self.sessions.get(session_id)
        if not session or "list_item" not in session:
            return
        list_item = session["list_item"]
        widget = self.session_list.itemWidget(list_item)
        if widget and hasattr(widget, 'set_title'):
            widget.set_title(title)

    def _create_new_session(self, title: str) -> str:
        """创建新的会话条目，返回 session_id"""
        session_id = self._generate_session_id()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 持久化到数据库
        self.storage.create_session(session_id, title)

        item_widget = SessionItemWidget(title, current_time, preview="", msg_count=0,
                                         theme=getattr(self, 'current_theme', 'light'))
        self._connect_session_item_signals(item_widget)
        list_item = QListWidgetItem()
        list_item.setSizeHint(item_widget.sizeHint())
        self.session_list.insertItem(0, list_item)
        self.session_list.setItemWidget(list_item, item_widget)
        # 隐藏最后一项的分割线
        item_widget.hide_separator()

        self.sessions[session_id] = {
            "title": title,
            "time": current_time,
            "messages": [],
            "list_item": list_item,
            "widget": item_widget,
        }
        self.chat_service.switch_session(session_id)
        self.current_session_id = session_id
        self._update_active_session_highlight()
        self._update_session_count()
        return session_id

    def _clear_chat_area(self):
        """清空聊天区域"""
        self._api_message_widget = None
        self._current_file_panel = None
        while self.chat_layout.count() > 1:
            child = self.chat_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _find_last_assistant_message_widget(self):
        """查找聊天区最后一条 AI 消息组件"""
        for i in range(self.chat_layout.count() - 1, -1, -1):
            item = self.chat_layout.itemAt(i)
            widget = item.widget() if item else None
            if widget is None:
                continue
            if isinstance(widget, ChatMessageWidget) and not widget.is_user:
                return widget
            for msg in widget.findChildren(ChatMessageWidget):
                if not msg.is_user:
                    return msg
        return None

    def _is_widget_in_chat(self, widget) -> bool:
        if widget is None:
            return False
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            root = item.widget() if item else None
            if root is widget:
                return True
            if root and widget in root.findChildren(type(widget)):
                return True
        return False

    def _restore_chat_messages(self, messages: list):
        """根据消息列表重建聊天区域（批量恢复，避免一次性创建所有 widget 导致卡顿）"""
        # 使任何在途的异步恢复批次失效，避免切换会话时旧定时器
        # 在新清空的 chat_layout 上继续 add_message 访问已释放对象（0xC0000005）
        self._restore_token = getattr(self, '_restore_token', 0) + 1
        self._restore_batch_queue = []
        self._clear_chat_area()
        self._last_user_msg_wrapper = None
        if not messages:
            self.add_welcome_message()
            return

        # 解析消息格式
        parsed = []
        for msg in messages:
            if len(msg) >= 4:
                text, is_user, timestamp, turn_id = msg[0], msg[1], msg[2], msg[3]
            elif len(msg) >= 3:
                text, is_user, timestamp = msg[0], msg[1], msg[2]
                turn_id = 0
            elif len(msg) == 2:
                text, is_user = msg
                timestamp = ""
                turn_id = 0
            else:
                continue
            parsed.append((text, is_user, timestamp, turn_id))

        # 第一批：立即创建前 10 条（让用户快速看到内容）
        BATCH_SIZE = 10
        first_batch = parsed[:BATCH_SIZE]
        turn_wrappers = {}

        for text, is_user, timestamp, turn_id in first_batch:
            self.add_message(text, is_user, timestamp=timestamp, turn_id=turn_id)
            if is_user and turn_id > 0:
                turn_wrappers[turn_id] = self._last_user_msg_wrapper

        # 剩余消息分批恢复，每批 10 条，间隔 50ms
        remaining = parsed[BATCH_SIZE:]
        if remaining:
            self._restore_batch_queue = remaining
            self._restore_batch_wrappers = turn_wrappers
            self._restore_active_token = self._restore_token
            QTimer.singleShot(50, self._restore_next_batch)
        else:
            # 全部恢复完成，添加回退按钮
            for turn_id, wrapper in turn_wrappers.items():
                self._add_rollback_button_for_wrapper(turn_id, wrapper)
            self._restore_file_operations()
            QTimer.singleShot(200, self.scroll_to_bottom)
            QTimer.singleShot(500, self.scroll_to_bottom)

    def _restore_next_batch(self):
        """分批恢复剩余消息，每批 10 条"""
        if not getattr(self, '_restore_batch_queue', None):
            return
        # 若此期间已切换会话/新建对话，令牌失效，直接放弃本次恢复
        if getattr(self, '_restore_token', 0) != getattr(self, '_restore_active_token', -1):
            self._restore_batch_queue = []
            return

        batch = self._restore_batch_queue[:10]
        self._restore_batch_queue = self._restore_batch_queue[10:]
        turn_wrappers = getattr(self, '_restore_batch_wrappers', {})

        for text, is_user, timestamp, turn_id in batch:
            self.add_message(text, is_user, timestamp=timestamp, turn_id=turn_id)
            if is_user and turn_id > 0:
                turn_wrappers[turn_id] = self._last_user_msg_wrapper

        if self._restore_batch_queue:
            # 还有剩余，继续下一批
            QTimer.singleShot(50, self._restore_next_batch)
        else:
            # 全部恢复完成
            for turn_id, wrapper in turn_wrappers.items():
                self._add_rollback_button_for_wrapper(turn_id, wrapper)
            self._restore_file_operations()
            QTimer.singleShot(200, self.scroll_to_bottom)
            QTimer.singleShot(500, self.scroll_to_bottom)

    def _restore_file_operations(self):
        """恢复文件操作记录显示"""
        if not self.current_session_id:
            return
        self._current_file_panel = None
        file_ops = self.storage.get_file_operations(self.current_session_id)
        if not file_ops:
            return
        for op in file_ops:
            path = op["file_name"]
            self._add_file_operation_record(
                op["op_type"],
                os.path.basename(path),
                added=str(op["added"]) if op["added"] else "0",
                removed=str(op["removed"]) if op["removed"] else "0",
                file_path=path,
            )
        panel = self._current_file_panel
        if panel and hasattr(panel, "expand"):
            panel.expand()

    def new_chat(self):
        """新建对话（在当前会话中重新开始）"""
        self.save_current_session()
        # 作废任何在途的异步消息恢复批次
        self._restore_token = getattr(self, '_restore_token', 0) + 1
        self._restore_batch_queue = []
        if self.current_session_id:
            self.chat_service.clear_session(self.current_session_id)
            self.storage.clear_messages(self.current_session_id)
            self.storage.clear_provider_sessions(self.current_session_id)
        self._clear_chat_area()
        self.add_welcome_message()
        if self.current_session_id and self.current_session_id in self.sessions:
            self.sessions[self.current_session_id]["messages"] = []
        self.show_toast("已创建新对话")

    def new_session(self):
        """新建会话"""
        self.save_current_session()
        # 作废任何在途的异步消息恢复批次
        self._restore_token = getattr(self, '_restore_token', 0) + 1
        self._restore_batch_queue = []
        self._create_new_session("新对话")
        self._clear_chat_area()
        self.add_welcome_message()
        # 新会话无工作区，清空文件树并显示空状态
        self.file_model.setRootPath("")
        self.file_tree.setRootIndex(QModelIndex())
        self._file_title_label.setText("文件浏览器")
        self._clear_breadcrumb()
        self._update_file_view_state()
        self.show_toast("已创建新会话")

    def _toggle_sessions(self):
        """已废弃：改用 Tab 切换"""
        self._switch_left_tab("sessions")

    def _toggle_file_tree(self):
        """已废弃：改用 Tab 切换"""
        self._switch_left_tab("files")

    def _update_sep_visibility(self):
        """已废弃：Tab 模式不需要"""
        pass

    def _update_session_count(self):
        """更新会话数量显示（Tab栏badge）"""
        count = self.session_list.count()
        self._tab_sessions_btn.setText(f"💬 历史记录 ({count})" if count > 0 else "💬 历史记录")

    def _on_session_clicked(self, item: QListWidgetItem):
        """点击会话列表项，恢复对应会话"""
        target_id = None
        for sid, session in self.sessions.items():
            if session.get("list_item") is item:
                target_id = sid
                break
        if not target_id or target_id == self.current_session_id:
            return
        self.save_current_session()
        self.current_session_id = target_id
        self.chat_service.switch_session(target_id)
        session = self.sessions[target_id]
        # 更新活动状态
        self._update_active_session_highlight()
        # 使用带时间戳的消息格式
        display_messages = self.storage.get_display_messages(target_id)
        self._restore_chat_messages(display_messages)
        self._update_context_display()
        # 恢复该会话的文件树路径（不自动切换到文件 Tab）
        fp = session.get("file_path", "")
        if fp and os.path.isdir(fp):
            index = self.file_model.setRootPath(fp)
            self.file_tree.setRootIndex(index)
            folder_name = os.path.basename(fp.rstrip("/\\")) or fp
            self._file_title_label.setText(f"📂 {folder_name}")
            self._update_breadcrumb(fp)
        else:
            # 无工作区时清空文件树，显示空状态
            self.file_model.setRootPath("")
            self.file_tree.setRootIndex(QModelIndex())
            self._file_title_label.setText("文件浏览器")
            self._clear_breadcrumb()
        self._update_file_view_state()
        # 滚动到对话底部
        QTimer.singleShot(100, self.scroll_to_bottom)
        self.show_toast(f"已切换到: {session['title']}")

    def _update_active_session_highlight(self):
        """更新所有会话列表项的活动状态高亮"""
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            widget = self.session_list.itemWidget(item)
            if isinstance(widget, SessionItemWidget):
                # 判断是否当前活动会话
                is_active = False
                for sid, session in self.sessions.items():
                    if session.get("list_item") is item and sid == self.current_session_id:
                        is_active = True
                        break
                widget.set_active(is_active)

    def _connect_session_item_signals(self, item_widget: SessionItemWidget):
        item_widget.delete_clicked.connect(
            lambda checked=False, w=item_widget: self._confirm_delete_session(w))
        item_widget.context_menu_requested.connect(
            lambda pos, w=item_widget: self._on_session_item_context_menu(w, pos))

    def _session_id_for_widget(self, widget: SessionItemWidget):
        for sid, session in self.sessions.items():
            if session.get("widget") is widget:
                return sid
        return None

    def _confirm_delete_session(self, widget: SessionItemWidget):
        from PySide6.QtWidgets import QMessageBox
        session_id = self._session_id_for_widget(widget)
        if not session_id:
            return
        title = self.sessions.get(session_id, {}).get("title", "该会话")
        reply = QMessageBox.question(
            self,
            "删除会话",
            f"确定删除「{title}」？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.delete_session(widget)

    def delete_session(self, widget: SessionItemWidget):
        """删除指定会话"""
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if self.session_list.itemWidget(item) is widget:
                remove_id = None
                for sid, session in self.sessions.items():
                    if session.get("list_item") is item:
                        remove_id = sid
                        break
                if remove_id:
                    # 删除 LangChain 记忆
                    self.chat_service.remove_session(remove_id)
                    # 从数据库删除
                    self.storage.delete_session(remove_id)
                    del self.sessions[remove_id]
                    if remove_id == self.current_session_id:
                        self.current_session_id = None
                        if self.sessions:
                            last_id = list(self.sessions.keys())[0]
                            self.current_session_id = last_id
                            self.chat_service.switch_session(last_id)
                            # 使用带时间戳的消息格式
                            display_messages = self.storage.get_display_messages(last_id)
                            self._restore_chat_messages(display_messages)
                        else:
                            self._clear_chat_area()
                            self.add_welcome_message()
                self.session_list.takeItem(i)
                widget.deleteLater()
                self.show_toast("已删除会话")
                self._update_session_count()
                return

    # ---- 会话右键菜单 ----

    def _on_session_item_context_menu(self, widget: SessionItemWidget, pos):
        session_id = self._session_id_for_widget(widget)
        if not session_id:
            return
        self._show_session_context_menu(session_id, widget, widget.mapToGlobal(pos))

    def _on_session_list_context_menu(self, pos):
        """会话列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        item = self.session_list.itemAt(pos)
        if item is None:
            menu = QMenu(self)
            menu.setStyleSheet(get_style('context_menu'))
            action = menu.addAction("＋ 新建会话")
            action.triggered.connect(self.new_session)
            menu.exec_(self.session_list.mapToGlobal(pos))
            return

        target_id = None
        for sid, session in self.sessions.items():
            if session.get("list_item") is item:
                target_id = sid
                break
        if not target_id:
            return

        widget = self.session_list.itemWidget(item)
        self._show_session_context_menu(target_id, widget, self.session_list.mapToGlobal(pos))

    def _show_session_context_menu(self, target_id: str, widget, global_pos):
        """显示单个会话的操作菜单"""
        from PySide6.QtWidgets import QMenu
        session = self.sessions.get(target_id)
        if not session:
            return

        is_pinned = getattr(widget, 'is_pinned', lambda: False)() if widget else False

        menu = QMenu(self)
        menu.setStyleSheet(get_style('context_menu'))

        action_rename = menu.addAction("✏️ 重命名")
        action_rename.triggered.connect(lambda: self._rename_session(target_id))

        pin_text = "📌 取消置顶" if is_pinned else "📌 置顶"
        action_pin = menu.addAction(pin_text)
        action_pin.triggered.connect(lambda: self._toggle_session_pin(target_id))

        menu.addSeparator()

        action_copy_title = menu.addAction("📋 复制标题")
        action_copy_title.triggered.connect(
            lambda: self._copy_to_clipboard(session.get("title", "")))

        menu.addSeparator()

        action_delete = menu.addAction("🗑 删除会话")
        action_delete.triggered.connect(
            lambda: self._confirm_delete_session(widget) if widget else None)

        menu.exec_(global_pos)

    def _rename_session(self, session_id: str):
        """重命名会话"""
        from PySide6.QtWidgets import QInputDialog
        session = self.sessions.get(session_id)
        if not session:
            return
        old_title = session.get("title", "")
        new_title, ok = QInputDialog.getText(
            self, "重命名会话", "新名称:", text=old_title)
        if ok and new_title.strip():
            session["title"] = new_title.strip()
            self.storage.update_session_title(session_id, new_title.strip())
            widget = session.get("list_item") and self.session_list.itemWidget(session["list_item"])
            if widget and hasattr(widget, 'set_title'):
                widget.set_title(new_title.strip())
            self.show_toast(f"已重命名为: {new_title.strip()}")

    def _toggle_session_pin(self, session_id: str):
        """切换会话置顶状态"""
        session = self.sessions.get(session_id)
        if not session:
            return
        list_item = session.get("list_item")
        if not list_item:
            return
        widget = self.session_list.itemWidget(list_item)
        if not isinstance(widget, SessionItemWidget):
            return

        is_pinned = widget.is_pinned()
        widget.set_pinned(not is_pinned)

        row = self.session_list.row(list_item)
        self.session_list.takeItem(row)
        if not is_pinned:
            self.session_list.insertItem(0, list_item)
            self.session_list.setItemWidget(list_item, widget)
        else:
            self.session_list.insertItem(self.session_list.count(), list_item)
            self.session_list.setItemWidget(list_item, widget)

        self.show_toast("已取消置顶" if is_pinned else "已置顶")

    def on_model_selected(self, model_name: str):
        """模型选择事件（从右侧卡片点击）"""
        index = self.model_combo.findText(model_name)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self._save_ui_preferences(last_model_display=model_name)
        self.show_toast(f"已切换到模型: {model_name}")

    def _on_model_combo_changed(self, display_name: str):
        """保存上次选择的模型"""
        self._update_chatgpt_login_btn_visibility(display_name)
        if display_name:
            self._save_ui_preferences(last_model_display=display_name)

    def show_toast(self, message: str):
        """显示Toast提示"""
        toast = ToastWidget(message, self)
        toast.move(self.width() // 2 - 140, 60)

    # ---- 文件浏览器 ----

    def _open_folder(self):
        """打开文件夹对话框并显示文件树"""
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self._set_file_tree_root(folder)
            # 保存到当前会话
            if self.current_session_id:
                self.storage.update_session_file_path(self.current_session_id, folder)
                if self.current_session_id in self.sessions:
                    self.sessions[self.current_session_id]["file_path"] = folder

            # 后台自动索引：预热 scan_project 缓存 + RAG 向量数据库
            self._auto_index_workspace(folder)

    @staticmethod
    def _auto_index_workspace(dir_path: str):
        """后台预热项目缓存和向量索引（非阻塞），不影响 UI 响应"""
        try:
            from services.tools import warm_rag_and_scan
            warm_rag_and_scan(dir_path)
        except Exception:
            pass

    def _update_file_view_state(self):
        """根据是否设置了工作区来切换文件视图（正常模式 / 空状态）"""
        root = self.file_model.rootPath()
        has_workspace = bool(root and os.path.isdir(root))
        self._breadcrumb_bar.setVisible(has_workspace)
        self._file_toolbar.setVisible(has_workspace)
        self.file_tree.setVisible(has_workspace)
        self._file_bottom.setVisible(has_workspace)
        self._file_empty_widget.setVisible(not has_workspace)

    def _set_file_tree_root(self, folder: str):
        """设置文件树根目录并更新面包屑导航"""
        index = self.file_model.setRootPath(folder)
        self.file_tree.setRootIndex(index)
        folder_name = os.path.basename(folder.rstrip("/\\")) or folder
        self._file_title_label.setText(f"📂 {folder_name}")
        self._update_breadcrumb(folder)
        self._update_file_view_state()
        # 自动切换到文件 Tab
        if self._current_left_tab != "files":
            self._switch_left_tab("files")

    def _clear_breadcrumb(self):
        """清空面包屑导航"""
        while self._breadcrumb_layout.count() > 1:
            child = self._breadcrumb_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _update_breadcrumb(self, folder: str):
        """更新面包屑导航"""
        # 清除旧面包屑
        self._clear_breadcrumb()

        # 构建路径组件
        parts = []
        path = os.path.normpath(folder)
        while path and path != os.path.dirname(path):
            parts.insert(0, (os.path.basename(path), path))
            path = os.path.dirname(path)
        # 根路径（Windows 盘符或 Unix 根）
        if path:
            parts.insert(0, (path, path))

        for i, (name, p) in enumerate(parts):
            if i > 0:
                sep = QLabel("›")
                sep.setObjectName("breadcrumb_sep")
                self._breadcrumb_layout.addWidget(sep)
            btn = QPushButton(name)
            btn.setObjectName("breadcrumb_btn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, fp=p: self._set_file_tree_root(fp))
            self._breadcrumb_layout.addWidget(btn)

    def _on_file_double_click(self, index):
        """双击文件在右侧查看器中打开"""
        path = self.file_model.filePath(index)
        if self.file_model.isDir(index):
            return
        self._show_file_in_viewer(path)

    def _show_file_in_viewer(self, path: str):
        """在代码编辑器中显示/打开文件"""
        self._open_file_in_editor(path)

    def _close_file_viewer(self):
        """关闭代码编辑器中的文件 — 隐藏编辑器，聊天区占满"""
        self.code_editor_panel.close_file()
        if not self.code_editor_panel.has_file():
            self._layout_mode = 'full_chat'
            self._apply_layout_mode()

    def _on_file_context_menu(self, pos):
        """文件树右键菜单 - 增强版"""
        index = self.file_tree.indexAt(pos)
        if not index.isValid():
            # 空白区右键
            root = self.file_model.rootPath()
            if root:
                self._show_empty_area_menu(pos, root)
            return
        path = self.file_model.filePath(index)
        is_dir = self.file_model.isDir(index)

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(get_style('context_menu'))

        if is_dir:
            menu.addAction("📁 新建目录").triggered.connect(
                lambda: self._create_new_item(path, is_dir=True))
            menu.addAction("📄 新建文件").triggered.connect(
                lambda: self._create_new_item(path, is_dir=False))
            menu.addSeparator()
            menu.addAction("📂 在资源管理器中打开").triggered.connect(
                lambda: self._open_in_explorer(path))
            menu.addAction("🖥 在终端中打开").triggered.connect(
                lambda: self._open_in_terminal(path))
        else:
            menu.addAction("👁 查看文件").triggered.connect(
                lambda: self._show_file_in_viewer(path))
            menu.addAction("📝 用外部程序打开").triggered.connect(
                lambda: self._open_in_explorer(path))
            menu.addSeparator()
            menu.addAction("✏️ 重命名").triggered.connect(
                lambda: self._rename_file_item(path))

        menu.addSeparator()
        menu.addAction("📋 复制绝对路径").triggered.connect(
            lambda: self._copy_to_clipboard(path))
        menu.addAction("📄 复制文件名").triggered.connect(
            lambda: self._copy_to_clipboard(os.path.basename(path)))
        menu.addAction("📂 复制父目录路径").triggered.connect(
            lambda: self._copy_to_clipboard(os.path.dirname(path)))

        menu.addSeparator()
        delete_action = menu.addAction("🗑 删除")
        delete_action.triggered.connect(lambda: self._delete_item(path))

        menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    def _show_empty_area_menu(self, pos, root_path: str):
        """文件树空白区域右键"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(get_style('context_menu'))
        menu.addAction("📁 新建目录").triggered.connect(
            lambda: self._create_new_item(root_path, is_dir=True))
        menu.addAction("📄 新建文件").triggered.connect(
            lambda: self._create_new_item(root_path, is_dir=False))
        menu.addSeparator()
        menu.addAction("🔄 刷新").triggered.connect(self._refresh_file_tree)
        menu.addAction("📌 折叠全部").triggered.connect(
            lambda: self.file_tree.collapseAll())
        menu.addAction("📂 在资源管理器中打开").triggered.connect(
            lambda: self._open_in_explorer(root_path))
        menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    def _rename_file_item(self, path: str):
        """重命名文件/目录"""
        from PySide6.QtWidgets import QInputDialog
        old_name = os.path.basename(path)
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_path = os.path.join(os.path.dirname(path), new_name.strip())
            try:
                os.rename(path, new_path)
                self.show_toast(f"已重命名为: {new_name.strip()}")
            except Exception as e:
                self.show_toast(f"重命名失败: {e}")

    def _open_in_terminal(self, path: str):
        """在终端中打开目录"""
        import subprocess
        subprocess.Popen(["wt", "-d", path], creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _on_root_path_context_menu(self, pos):
        """根路径右键菜单（面包屑上右键）"""
        root_path = self.file_model.rootPath()
        if not root_path:
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(get_style('context_menu'))

        menu.addAction("📁 新建目录").triggered.connect(
            lambda: self._create_new_item(root_path, is_dir=True))
        menu.addAction("📄 新建文件").triggered.connect(
            lambda: self._create_new_item(root_path, is_dir=False))
        menu.addSeparator()
        menu.addAction("📋 复制绝对路径").triggered.connect(
            lambda: self._copy_to_clipboard(root_path))
        menu.addAction("📂 在资源管理器中打开").triggered.connect(
            lambda: self._open_in_explorer(root_path))
        menu.addAction("🖥 在终端中打开").triggered.connect(
            lambda: self._open_in_terminal(root_path))
        menu.addSeparator()
        menu.addAction("🔄 刷新").triggered.connect(self._refresh_file_tree)

        menu.exec_(self._file_title_label.mapToGlobal(pos))

    def _create_new_item(self, parent_path: str, is_dir: bool):
        """在指定目录下新建文件或目录"""
        from PySide6.QtWidgets import QInputDialog
        if is_dir:
            name, ok = QInputDialog.getText(self, "新建目录", "目录名称:")
        else:
            name, ok = QInputDialog.getText(self, "新建文件", "文件名称:")

        if ok and name:
            new_path = os.path.join(parent_path, name)
            try:
                if is_dir:
                    os.makedirs(new_path, exist_ok=True)
                else:
                    with open(new_path, 'w', encoding='utf-8') as f:
                        pass
                self.show_toast(f"已创建: {name}")
            except Exception as e:
                self.show_toast(f"创建失败: {e}")

    def _delete_item(self, path: str):
        """删除文件或目录"""
        import shutil
        from PySide6.QtWidgets import QMessageBox
        
        name = os.path.basename(path)
        is_dir = os.path.isdir(path)
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除{'目录' if is_dir else '文件'} '{name}' 吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.show_toast(f"已删除: {name}")
            except Exception as e:
                self.show_toast(f"删除失败: {e}")

    def _copy_to_clipboard(self, text: str):
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.show_toast("已复制到剪贴板")

    def _open_in_explorer(self, path: str):
        """在资源管理器中打开"""
        import subprocess
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["explorer", "/select,", path])

    def _load_config(self):
        """加载 UI 与背景配置"""
        import json
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_config_path = os.path.join(project_root, "config", "ui_config.json")
        try:
            if os.path.exists(ui_config_path):
                with open(ui_config_path, "r", encoding="utf-8-sig") as f:
                    ui_config = json.load(f)
                self.current_theme = ui_config.get("theme", "light")
                self.background_image = ui_config.get("background_image", "")
                self.background_opacity = ui_config.get("background_opacity", 0.3)
                self._last_model_display = ui_config.get("last_model_display", "")
                # 加载强调色并应用到 styles 模块
                accent_color = ui_config.get("accent_color", "#6e7fe0")
                self.accent_color = accent_color
                from ui import styles as _styles
                _styles.set_accent_color(accent_color, self.current_theme)
            else:
                self._last_model_display = ""
                self.accent_color = "#6e7fe0"
        except Exception:
            self._last_model_display = ""
            self.accent_color = "#6e7fe0"

        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "background_config.json")
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8-sig') as f:
                    bg_config = json.load(f)
                    self.background_image = bg_config.get('image_path', self.background_image)
                    self.background_opacity = bg_config.get('opacity', self.background_opacity)
        except Exception as e:
            print(f"加载背景配置失败: {e}")
        try:
            from services.utils.terminal_config import load_config
            load_config()
        except Exception as e:
            print(f"加载终端配置失败: {e}")
        # 同步更新 central widget 底色以匹配当前主题
        self._update_central_bg_color()

    def _update_central_bg_color(self):
        """根据当前主题更新 BackgroundWidget 的底色"""
        central = self.centralWidget()
        if central and hasattr(central, 'set_bg_color'):
            if self.current_theme == 'dark':
                central.set_bg_color(QColor(39, 40, 46))   # #27282e (DeepSeek风格深灰蓝)
            else:
                central.set_bg_color(QColor(240, 242, 245))  # #f0f2f5

    def _save_ui_preferences(self, **kwargs):
        """保存 UI 偏好（主题、上次模型等）"""
        import json
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_config_path = os.path.join(project_root, "config", "ui_config.json")
        config = {}
        try:
            if os.path.exists(ui_config_path):
                with open(ui_config_path, "r", encoding="utf-8-sig") as f:
                    config = json.load(f)
        except Exception:
            config = {}
        config.update(kwargs)
        try:
            with open(ui_config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存 UI 配置失败: {e}")

    def _save_config(self):
        """保存背景配置文件"""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "background_config.json")
        try:
            bg_config = {
                'image_path': self.background_image,
                'opacity': self.background_opacity
            }

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(bg_config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存背景配置失败: {e}")

    def _save_agent_config(self, temperature: float, max_tokens: int, max_steps: int):
        """将模型参数写入 agent_config.json，确保重启后不丢失"""
        agent_config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "services", "config", "agent_config.json"
        )
        try:
            import json as _json
            with open(agent_config_path, 'r', encoding='utf-8') as f:
                config = _json.load(f)
            config.setdefault("agent", {})
            config["agent"]["max_steps"] = max_steps
            config["agent"].setdefault("llm_params", {})
            config["agent"]["llm_params"]["temperature"] = temperature
            config["agent"]["llm_params"]["max_tokens"] = max_tokens
            with open(agent_config_path, 'w', encoding='utf-8') as f:
                _json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存 agent 配置失败: {e}")

    def show_settings(self):
        """显示设置对话框"""
        current_settings = {
            'background_image': self.background_image,
            'background_opacity': self.background_opacity,
            'theme': self.current_theme,
            'accent_color': getattr(self, 'accent_color', '#6e7fe0'),
            'temperature': self.temp_slider.value() / 100.0 if hasattr(self, 'temp_slider') else 0.7,
            'max_tokens': self.token_slider.value() if hasattr(self, 'token_slider') else 2048,
            'max_steps': self.steps_slider.value() if hasattr(self, 'steps_slider') else 10,
        }
        try:
            from services.utils.terminal_config import get_config
            current_settings.update(get_config())
        except Exception:
            pass

        dialog = SettingsDialog(self, current_settings)
        dialog.settings_changed.connect(self._apply_settings)
        dialog.exec()

    def _apply_settings(self, settings):
        """应用设置"""
        self.background_image = settings.get('background_image', '')
        self.background_opacity = settings.get('background_opacity', 0.3)

        # 应用主题
        new_theme = settings.get('theme', 'light')
        if new_theme != self.current_theme:
            self._apply_theme(new_theme)
        else:
            # 即使主题没变，也应用 accent_color
            self._apply_accent_color(settings.get('accent_color', '#6e7fe0'))

        # 更新参数滑块
        new_temp = settings.get('temperature', 0.7)
        new_tokens = settings.get('max_tokens', 2048)
        new_steps = settings.get('max_steps', 10)

        if hasattr(self, 'temp_slider'):
            self.temp_slider.setValue(int(new_temp * 100))
        if hasattr(self, 'token_slider'):
            self.token_slider.setValue(new_tokens)
        if hasattr(self, 'steps_slider'):
            self.steps_slider.setValue(new_steps)

        # 保存背景配置
        self._save_config()
        # 保存 Agent 参数到 agent_config.json（温度、token、推理步数）
        self._save_agent_config(temperature=new_temp, max_tokens=new_tokens, max_steps=new_steps)

        # 应用背景
        self._apply_background()

        self.show_toast("设置已保存")

    def _apply_background(self):
        """应用背景图片和透明度"""
        central = self.centralWidget()
        if self.background_image and os.path.exists(self.background_image):
            pixmap = QPixmap(self.background_image)
            if not pixmap.isNull():
                central.set_background(pixmap, self.background_opacity)
                # 让子面板半透明，露出底图
                self._set_child_transparency(True)
                self._update_central_bg_color()
                return
        central.clear_background()
        self._set_child_transparency(False)
        self._update_central_bg_color()

    def _set_child_transparency(self, enabled: bool):
        """设置子面板是否半透明（让背景图可见）"""
        from ui.styles import get_style as _gs
        def gs(name): return _gs(name, self.current_theme)
        is_dark = self.current_theme == 'dark'

        if enabled:
            a = 0.7  # 面板透明度，越小越透明
            if is_dark:
                semi = f"rgba(39, 40, 46, {a})"
                semi_w = f"rgba(45, 46, 52, {a})"
                text_color = "#d9dae0"
                border_rgba = "rgba(255,255,255,0.06)"
                accent = getattr(self, 'accent_color', '#6e7fe0')
            else:
                semi = f"rgba(240, 242, 245, {a})"
                semi_w = f"rgba(255, 255, 255, {a})"
                text_color = "#000000"
                border_rgba = "rgba(0,0,0,0.06)"
                accent = getattr(self, 'accent_color', '#6e7fe0')

            # 左侧面板
            self._left_panel.setStyleSheet(
                f"background: {semi_w}; border-right: 1px solid {border_rgba};")
            self.session_list.setStyleSheet(
                f"QListWidget {{ background: transparent; border: none; outline: none; }}"
                f"QListWidget::item {{ background: transparent; border: none; padding: 4px 6px; color: {text_color}; }}"
                f"QListWidget::item:selected {{ background: {accent}22; border-radius: 10px; }}"
                f"QListWidget::item:hover {{ background: {accent}14; border-radius: 10px; }}")
            self.session_list.verticalScrollBar().setStyleSheet(gs('scrollbar_light'))

            # 中间聊天区
            self._middle_widget.setStyleSheet(f"background: {semi};")
            self.chat_container.setStyleSheet(f"background: transparent;")
            self.chat_scroll.setStyleSheet(
                f"QScrollArea {{ background: transparent; border: none; }}"
                f"QScrollArea > QWidget {{ background: transparent; }}"
                f"QWidget {{ background: transparent; }}")
            self.chat_scroll.viewport().setStyleSheet("background: transparent;")
            self.chat_scroll.verticalScrollBar().setStyleSheet(gs('scrollbar_chat'))
            self._input_container.setStyleSheet(
                f"background: {semi}; border-top: 1px solid {border_rgba};")

            # 文件浏览器
            self.file_viewer_panel.setStyleSheet(
                f"background: {semi_w}; border: 1px solid {border_rgba}; border-radius: 10px;")
            self.file_tree.setStyleSheet(f"""
                QTreeView {{
                    background: transparent; color: {text_color}; border: none;
                    font-size: 12px; outline: none;
                }}
                QTreeView::item {{ padding: 4px 0; border-radius: 4px; color: {text_color}; }}
                QTreeView::item:selected {{ background: {accent}26; color: {text_color}; }}
                QTreeView::item:hover {{ background: {accent}14; color: {text_color}; }}
            """)
            self.file_tree.viewport().setStyleSheet("background: transparent;")
            self.file_tree.verticalScrollBar().setStyleSheet(gs('scrollbar_dark'))
        else:
            # 不透明时使用主题样式
            self._left_panel.setStyleSheet(gs('left_panel'))
            self.session_list.setStyleSheet(gs('session_list'))
            self.session_list.verticalScrollBar().setStyleSheet(gs('scrollbar_light'))
            self._middle_widget.setStyleSheet(gs('middle'))
            if is_dark:
                self.chat_container.setStyleSheet("background: #27282e;")
            else:
                self.chat_container.setStyleSheet("background: #f0f2f5;")
            self.chat_scroll.setStyleSheet(gs('chat_scroll'))
            self.chat_scroll.verticalScrollBar().setStyleSheet(gs('scrollbar_chat'))
            # 显式设置 viewport 背景，否则 QScrollArea viewport 会保持亮色
            if is_dark:
                self.chat_scroll.viewport().setStyleSheet("background: #27282e;")
            else:
                self.chat_scroll.viewport().setStyleSheet("background: #f4f5f7;")
            if is_dark:
                self._input_container.setStyleSheet(
                    "background: #27282e; border-top: 1px solid rgba(255,255,255,0.06);")
            else:
                self._input_container.setStyleSheet(
                    "background: #f4f5f7; border-top: 1px solid rgba(0, 0, 0, 0.05);")

            if hasattr(self.file_viewer_panel, 'apply_theme'):
                try:
                    self.file_viewer_panel.apply_theme(self.current_theme)
                except Exception:
                    pass
            self.file_tree.setStyleSheet(gs('file_tree_enhanced'))
            self.file_tree.viewport().setStyleSheet("background: transparent;")
            self.file_tree.verticalScrollBar().setStyleSheet(
                gs('scrollbar_dark') if is_dark else gs('scrollbar_light'))

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)

    def closeEvent(self, event):
        """窗口关闭时清理资源，防止内存泄漏"""
        # 保存未完成的 AI 输出到数据库
        self._save_incomplete_ai_response()
        # 停止正在进行的 API 调用
        if hasattr(self, '_api_worker') and self._api_worker:
            self._api_worker.request_stop()
        # 保存线程引用，清理后等待退出
        _thread_to_wait = getattr(self, '_api_thread', None)
        self._cleanup_api_thread()
        # 关闭时等待线程退出（最多 2 秒），防止进程退出时崩溃
        if _thread_to_wait is not None:
            _thread_to_wait.wait(2000)
        # 停止所有定时器
        if hasattr(self, '_chunk_render_timer'):
            self._chunk_render_timer.stop()
        if hasattr(self, '_status_scroll_timer'):
            self._status_scroll_timer.stop()
        super().closeEvent(event)

    def center_on_screen(self):
        """将窗口居中显示在屏幕上"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            w = min(1400, screen_geometry.width() - 80)
            h = min(860, screen_geometry.height() - 60)
            x = screen_geometry.x() + (screen_geometry.width() - w) // 2
            y = screen_geometry.y() + (screen_geometry.height() - h) // 2
            self.setGeometry(x, y, w, h)
