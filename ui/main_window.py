﻿"""
主窗口模块
包含MainWindow类及其所有UI布局和交互逻辑
"""

import os
import time
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
                             QThread, Signal, QObject)
from PySide6.QtGui import QIcon, QGuiApplication, QColor, QPalette, QPixmap, QImage

import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.styles import get_style
from ui.widgets import (GlassEffect, ChatMessageWidget, SessionItemWidget,
                         ModelCardWidget, ToastWidget, ModernDropdown, TerminalWidget,
                         BackgroundWidget, ImageGeneratorWidget, CodeHighlighter,
                         TaskProgressWidget, ToolStatusWidget)
from ui.settings_dialog import SettingsDialog
from services.api_service import get_model_display_names, find_model_by_display
from services.chat_service import ChatService
from services.storage_service import StorageService
from services.image_service import ImageWorker, upload_image_to_base64
from services.rollback_service import RollbackManager
from services.tools import set_rollback_manager


class ApiWorker(QObject):
    """API 调用工作线程（支持普通对话和 Agent 模式）"""
    chunk_ready = Signal(str)
    finished = Signal()
    error = Signal(str)
    agent_step = Signal(str)
    agent_thinking = Signal(str)
    agent_done = Signal()

    def __init__(self, chat_service, session_id, user_message,
                 model_display, temperature, max_tokens, max_steps=10, parent=None):
        super().__init__(parent)
        self.chat_service = chat_service
        self.session_id = session_id
        self.user_message = user_message
        self.model_display = model_display
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            if self.chat_service.agent_mode:
                for event in self.chat_service.send_agent_message_stream(
                    session_id=self.session_id,
                    user_message=self.user_message,
                    model_display=self.model_display,
                    max_steps=self.max_steps,
                ):
                    if self._stop:
                        self.agent_done.emit()
                        self.chunk_ready.emit("\n\n⏹ *已停止*")
                        break
                    if event["type"] == "thinking":
                        self.agent_thinking.emit(event["output"])
                    elif event["type"] == "thought":
                        self.agent_step.emit(f"💭 {event['output']}")
                    elif event["type"] == "step":
                        step_text = (
                            f"🔧 **{event['tool']}**\n"
                            f"输入: `{event['input']}`\n"
                            f"结果: {event['output'][:200]}"
                        )
                        self.agent_step.emit(step_text)
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
        # 回滚管理器
        self._rollback_mgr = RollbackManager()
        set_rollback_manager(self._rollback_mgr)
        # 当前轮次 turn_id（用于回滚按钮）
        self._current_turn_id = 0
        self._last_user_msg_wrapper = None
        self._last_user_turn_id = 0
        # 加载配置
        self._load_config()
        self.setup_ui()
        self.setup_style()
        self.center_on_screen()
        # 应用背景
        self._apply_background()
        # 加载保存的头像
        self._load_saved_avatar()
        # UI 创建完成后再加载历史会话
        self._load_sessions_from_db()

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

        splitter.setSizes([220, 700])

        content_layout.addWidget(splitter)
        main_layout.addWidget(content_widget)

    def setup_top_nav_bar(self, parent_layout):
        """设置顶部导航栏"""
        top_bar = QWidget()
        top_bar.setStyleSheet(get_style('top_nav'))
        top_bar.setFixedHeight(50)

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
        brand_icon.setStyleSheet(
            "color: rgba(255,255,255,0.9); font-size: 20px; background: transparent;")
        brand_label = QLabel("Sky Code")
        brand_label.setStyleSheet(get_style('brand'))

        status_container = QWidget()
        status_container.setStyleSheet("background: rgba(255,255,255,0.12); border-radius: 10px;")
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(8, 3, 10, 3)
        status_layout.setSpacing(5)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label = QLabel("在线")
        self.status_label.setStyleSheet(
            "color: rgba(255,255,255,0.85); font-size: 11px; background: transparent;")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)

        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand_label)
        brand_layout.addWidget(status_container)

        # ── 右侧：功能按钮（精简） ──
        btn_container = QWidget()
        btn_container.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.setFixedSize(72, 32)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(get_style('settings_btn'))
        clear_btn.clicked.connect(self.new_chat)

        open_folder_btn = QPushButton("📂 文件")
        open_folder_btn.setFixedSize(72, 32)
        open_folder_btn.setCursor(Qt.PointingHandCursor)
        open_folder_btn.setStyleSheet(get_style('settings_btn'))
        open_folder_btn.clicked.connect(self._open_folder)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setToolTip("设置")
        settings_btn.setStyleSheet(get_style('settings_btn'))
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
        img_gen_btn = QPushButton("🎨 画图")
        img_gen_btn.setFixedSize(72, 32)
        img_gen_btn.setCursor(Qt.PointingHandCursor)
        img_gen_btn.setStyleSheet(get_style('settings_btn'))
        img_gen_btn.clicked.connect(self._toggle_image_panel)
        btn_layout.addWidget(img_gen_btn)
        btn_layout.addWidget(settings_btn)
        btn_layout.addWidget(self.avatar_btn)

        top_layout.addWidget(brand_container)
        top_layout.addStretch()
        top_layout.addWidget(btn_container)

        parent_layout.addWidget(top_bar)

    def setup_left_session_panel(self, splitter):
        """设置左侧导航栏：历史对话 + 文件浏览器（独立可折叠）"""
        self._left_panel = QWidget()
        self._left_panel.setFixedWidth(280)
        self._left_panel.setStyleSheet(get_style('left_panel'))

        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # ─── 历史对话（可折叠，默认收起）───
        self._sessions_collapsed = True
        session_header = QWidget()
        session_header.setFixedHeight(40)
        session_header.setCursor(Qt.PointingHandCursor)
        session_header.setStyleSheet("""
            QWidget {
                background: transparent;
                border-bottom: 1px solid rgba(0, 0, 0, 0.04);
            }
            QWidget:hover {
                background: rgba(79, 70, 229, 0.04);
            }
        """)
        sh_layout = QHBoxLayout(session_header)
        sh_layout.setContentsMargins(14, 0, 14, 0)
        sh_layout.setSpacing(8)

        self.session_arrow = QLabel("▸")
        self.session_arrow.setStyleSheet("color: #71717a; font-size: 11px; background: transparent;")
        self.session_arrow.setFixedWidth(12)

        sh_title = QLabel("历史对话")
        sh_title.setStyleSheet("color: #18181b; font-size: 13px; font-weight: 600; background: transparent;")

        self.session_count_label = QLabel()
        self.session_count_label.setStyleSheet("""
            color: #71717a; font-size: 11px; background: rgba(0,0,0,0.05);
            padding: 1px 6px; border-radius: 8px;
        """)

        sh_layout.addWidget(self.session_arrow)
        sh_layout.addWidget(sh_title)
        sh_layout.addWidget(self.session_count_label)
        sh_layout.addStretch()
        session_header.mousePressEvent = lambda e: self._toggle_sessions() if e.button() == Qt.LeftButton else None

        self.session_list = QListWidget()
        self.session_list.setStyleSheet(get_style('session_list'))
        self.session_list.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))
        self.session_list.itemClicked.connect(self._on_session_clicked)

        # ─── 分隔线 ───
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.HLine)
        self._sep.setStyleSheet("background: rgba(0,0,0,0.06); max-height: 1px;")

        # ─── 文件浏览器（独立可折叠）───
        file_header = QWidget()
        file_header.setFixedHeight(40)
        file_header.setCursor(Qt.PointingHandCursor)
        file_header.setStyleSheet("""
            QWidget {
                background: transparent;
                border-bottom: 1px solid rgba(0, 0, 0, 0.04);
            }
            QWidget:hover {
                background: rgba(79, 70, 229, 0.04);
            }
        """)
        fh_layout = QHBoxLayout(file_header)
        fh_layout.setContentsMargins(14, 0, 10, 0)
        fh_layout.setSpacing(8)

        self.file_arrow = QLabel("▸")
        self.file_arrow.setStyleSheet("color: #71717a; font-size: 11px; background: transparent;")
        self.file_arrow.setFixedWidth(12)

        self.file_title_label = QLabel("文件浏览器")
        self.file_title_label.setStyleSheet("color: #18181b; font-size: 13px; font-weight: 600; background: transparent;")
        self.file_title_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_title_label.customContextMenuRequested.connect(self._on_root_path_context_menu)

        fe_open_btn = QPushButton("打开")
        fe_open_btn.setFixedSize(40, 24)
        fe_open_btn.setCursor(Qt.PointingHandCursor)
        fe_open_btn.setStyleSheet("""
            QPushButton {
                color: #4f46e5;
                background: rgba(79, 70, 229, 0.08);
                border: 1px solid rgba(79, 70, 229, 0.15);
                border-radius: 6px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(79, 70, 229, 0.15);
                border-color: rgba(79, 70, 229, 0.25);
            }
        """)
        fe_open_btn.clicked.connect(self._open_folder)

        fh_layout.addWidget(self.file_arrow)
        fh_layout.addWidget(self.file_title_label)
        fh_layout.addStretch()
        fh_layout.addWidget(fe_open_btn)
        file_header.mousePressEvent = lambda e: self._toggle_file_tree() if e.button() == Qt.LeftButton else None

        self.file_tree = QTreeView()
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")
        self.file_model.setNameFilterDisables(False)
        self.file_tree.setModel(self.file_model)
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAnimated(True)
        self.file_tree.setColumnHidden(1, True)
        self.file_tree.setColumnHidden(2, True)
        self.file_tree.setColumnHidden(3, True)
        self.file_tree.setStyleSheet(get_style('file_tree'))
        self.file_tree.viewport().setStyleSheet("background: transparent;")
        self.file_tree.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))
        self.file_tree.doubleClicked.connect(self._on_file_double_click)
        self.file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self._on_file_context_menu)

        # ─── 底部按钮区 ───
        bottom_container = QWidget()
        bottom_container.setStyleSheet("background: transparent; border-top: 1px solid rgba(0,0,0,0.04);")
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(12, 10, 12, 12)
        bottom_layout.setSpacing(8)

        # 显示终端按钮（始终可见）
        self.show_terminal_btn = QPushButton("  显示终端")
        self.show_terminal_btn.setFixedHeight(34)
        self.show_terminal_btn.setCursor(Qt.PointingHandCursor)
        self.show_terminal_btn.setStyleSheet("""
            QPushButton {
                background: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 500;
                text-align: left;
                padding-left: 12px;
            }
            QPushButton:hover {
                background: #2a2a3d;
                border-color: #45475a;
            }
        """)
        self.show_terminal_btn.clicked.connect(self._toggle_terminal_panel)

        new_session_btn = QPushButton("＋ 新建会话")
        new_session_btn.setFixedHeight(36)
        new_session_btn.setCursor(Qt.PointingHandCursor)
        new_session_btn.setStyleSheet(get_style('new_session_btn'))
        new_session_btn.clicked.connect(self.new_session)

        bottom_layout.addWidget(self.show_terminal_btn)
        bottom_layout.addWidget(new_session_btn)

        left_layout.addWidget(session_header)
        left_layout.addWidget(self.session_list)
        left_layout.addWidget(self._sep)
        left_layout.addWidget(file_header)
        self.file_tree.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        left_layout.addWidget(self.file_tree)

        # 默认收起历史对话列表
        self.session_list.hide()
        self.session_list.setMaximumHeight(0)
        self._sep.hide()

        # 弹性空间（把头部推到顶部，按钮推到底部）
        self._left_spacer = QWidget()
        self._left_spacer.setStyleSheet("background: transparent;")
        self._left_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        left_layout.addWidget(self._left_spacer)
        left_layout.addWidget(bottom_container)

        # 初始化 file_tree 为折叠状态
        self._file_collapsed = True
        self.file_tree.hide()
        self.file_tree.setMaximumHeight(0)
        self._update_sep_visibility()

        splitter.addWidget(self._left_panel)

    def setup_middle_chat_area(self, splitter):
        """设置中间对话区"""
        self._middle_widget = QWidget()
        self._middle_widget.setStyleSheet(get_style('middle'))

        middle_layout = QVBoxLayout(self._middle_widget)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        # 对话消息区域
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet(get_style('chat_scroll'))
        self.chat_scroll.verticalScrollBar().setStyleSheet(get_style('scrollbar_chat'))

        # 消息容器
        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: #f0f2f5;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(24, 16, 24, 16)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch()

        self.add_welcome_message()

        self.chat_scroll.setWidget(self.chat_container)

        # 输入区域
        self._input_container = QWidget()
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
        self.context_label.setStyleSheet(
            "color: #86868b; font-size: 11px; background: transparent;")

        self.context_progress = QProgressBar()
        self.context_progress.setFixedHeight(6)
        self.context_progress.setRange(0, 100)
        self.context_progress.setValue(0)
        self.context_progress.setTextVisible(False)
        self.context_progress.setStyleSheet("""
            QProgressBar {
                background: #e5e5ea;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:0.7 #a855f7, stop:1 #ef4444);
                border-radius: 3px;
            }
        """)

        self.context_percent = QLabel("0%")
        self.context_percent.setFixedWidth(36)
        self.context_percent.setStyleSheet(
            "color: #86868b; font-size: 11px; background: transparent;")

        context_layout.addWidget(self.context_label)
        context_layout.addWidget(self.context_progress, 1)
        context_layout.addWidget(self.context_percent)

        input_outer.addWidget(context_bar)

        # 输入行
        input_row = QWidget()
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

        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("输入消息... (按 Enter 发送，支持粘贴图片)")
        self.message_input.setStyleSheet(get_style('message_input'))
        self.message_input.returnPressed.connect(self.send_message)
        # 安装事件过滤器以捕获粘贴事件
        self.message_input.installEventFilter(self)

        # 发送/停止按钮
        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(44, 44)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("发送消息 (Enter)")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #6366f1, stop:1 #a855f7);
                color: white;
                border: none;
                border-radius: 22px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4f46e5, stop:1 #9333ea);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4338ca, stop:1 #7e22ce);
            }
        """)
        self.send_btn.clicked.connect(self._on_send_btn_clicked)
        self._is_generating = False

        # 模型选择器（输入框右侧）
        model_names = get_model_display_names()
        if not model_names:
            model_names = ["mimo-v2.5-pro"]
        self.model_combo = ModernDropdown(model_names)
        self.model_combo.setFixedHeight(38)
        self.model_combo.setMinimumWidth(150)

        input_layout.addWidget(upload_btn)
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.model_combo)

        # Agent 模式切换按钮
        self.agent_btn = QPushButton("🤖")
        self.agent_btn.setFixedSize(38, 38)
        self.agent_btn.setCursor(Qt.PointingHandCursor)
        self.agent_btn.setToolTip("Agent 模式: 启用后 AI 可读写本地文件")
        self.agent_btn.setStyleSheet("""
            QPushButton {
                background: #f0f0f3; border: 1.5px solid #d1d1d6;
                border-radius: 19px; font-size: 16px;
            }
            QPushButton:hover { border-color: #6366f1; }
            QPushButton:checked {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6366f1, stop:1 #a855f7);
                border-color: #6366f1; color: white;
            }
        """)
        self.agent_btn.setCheckable(True)
        self.agent_btn.toggled.connect(self._toggle_agent_mode)
        input_layout.addWidget(self.agent_btn)

        input_outer.addWidget(input_row)

        # ── 文件查看器面板（默认隐藏） ──
        self.file_viewer_panel = QWidget()
        self.file_viewer_panel.setMinimumWidth(360)
        self.file_viewer_panel.setStyleSheet("background: #1e1e2e;")
        fv_layout = QVBoxLayout(self.file_viewer_panel)
        fv_layout.setContentsMargins(0, 0, 0, 0)
        fv_layout.setSpacing(0)

        # 文件查看器标题栏
        fv_header = QWidget()
        fv_header.setFixedHeight(36)
        fv_header.setStyleSheet("background: #181825; border-bottom: 1px solid #313244;")
        fv_header_layout = QHBoxLayout(fv_header)
        fv_header_layout.setContentsMargins(12, 0, 8, 0)

        self.file_viewer_name = QLabel("未选择文件")
        self.file_viewer_name.setStyleSheet(
            "color: #cdd6f4; font-size: 12px; font-weight: bold; background: transparent;")
        fv_close_btn = QPushButton("✕")
        fv_close_btn.setFixedSize(20, 20)
        fv_close_btn.setCursor(Qt.PointingHandCursor)
        fv_close_btn.setStyleSheet("""
            QPushButton { color: #6c7086; background: transparent; border: none;
                          border-radius: 10px; font-size: 11px; }
            QPushButton:hover { color: #f38ba8; background: rgba(243,139,168,0.15); }
        """)
        fv_close_btn.clicked.connect(self._close_file_viewer)
        fv_header_layout.addWidget(self.file_viewer_name)
        fv_header_layout.addStretch()
        fv_header_layout.addWidget(fv_close_btn)

        # 文件内容显示区
        self.file_viewer_content = QPlainTextEdit()
        self.file_viewer_content.setReadOnly(True)
        self.file_viewer_content.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.file_viewer_content.setStyleSheet("""
            QPlainTextEdit {
                background: #1e1e2e;
                color: #cdd6f4;
                font-family: Consolas, "Courier New", "Source Code Pro", monospace;
                font-size: 13px;
                border: none;
                padding: 12px;
                selection-background-color: rgba(137, 180, 250, 0.3);
            }
        """)

        # 挂载语法高亮器
        self._code_highlighter = CodeHighlighter(self.file_viewer_content.document())

        fv_layout.addWidget(fv_header)
        fv_layout.addWidget(self.file_viewer_content)
        self.file_viewer_panel.hide()

        # 用 QSplitter 实现可拖动调整大小
        self._middle_splitter = QSplitter(Qt.Horizontal)
        self._middle_splitter.setHandleWidth(3)
        self._middle_splitter.setStyleSheet("""
            QSplitter::handle { background: #e0e0e5; }
            QSplitter::handle:hover { background: #6366f1; }
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

        cwi_layout.addWidget(self._input_container)

        self._middle_splitter.addWidget(chat_with_input)
        self._middle_splitter.addWidget(self.file_viewer_panel)

        # 图片生成面板（默认隐藏）
        self.image_gen_panel = ImageGeneratorWidget()
        self.image_gen_panel.setMinimumWidth(320)
        self.image_gen_panel.hide()
        self.image_gen_panel.generate_clicked.connect(self._on_image_generate)
        self.image_gen_panel.close_clicked.connect(self._toggle_image_panel)
        self._middle_splitter.addWidget(self.image_gen_panel)

        self._middle_splitter.setStretchFactor(0, 3)
        self._middle_splitter.setStretchFactor(1, 2)
        self._middle_splitter.setStretchFactor(2, 1)

        splitter.addWidget(self._middle_splitter)

    def setup_right_control_panel(self):
        """参数滑块（隐藏，由设置对话框控制）"""
        self.temp_slider = QSlider(Qt.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(70)
        self.temp_slider.hide()

        self.token_slider = QSlider(Qt.Horizontal)
        self.token_slider.setRange(256, 4096)
        self.token_slider.setValue(2048)
        self.token_slider.hide()

        self.steps_slider = QSlider(Qt.Horizontal)
        self.steps_slider.setRange(1, 30)
        self.steps_slider.setValue(10)
        self.steps_slider.hide()

    def setup_style(self):
        """设置整体样式"""
        self.setStyleSheet(get_style('main_window'))

    # ---- 文件附件处理 ----

    def _toggle_agent_mode(self, checked):
        """切换 Agent 模式"""
        self.chat_service.agent_mode = checked
        if checked:
            self.show_toast("Agent 模式已启用 (ReAct 推理 + 文件工具)")
        else:
            self.show_toast("Agent 模式已关闭")

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

        # 如果传入了本地图片路径，转为 data URL（图生图）
        image_url = None
        if image_path:
            image_url = upload_image_to_base64(image_path)

        self._img_thread = QThread()
        self._img_worker = ImageWorker(
            prompt=prompt, image_url=image_url,
            image_size=self.image_gen_panel.size_combo.currentText().split()[0],
            num_inference_steps=self.image_gen_panel.steps_spin.value(),
            seed=self.image_gen_panel.seed_spin.value() if self.image_gen_panel.seed_spin.value() >= 0 else None,
        )
        self._img_worker.moveToThread(self._img_thread)
        self._img_thread.started.connect(self._img_worker.run)
        self._img_worker.finished.connect(self._on_image_done)
        self._img_worker.error.connect(self._on_image_error)
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

    def _call_image_api(self, prompt: str, ref_image_path, model: str):
        """从聊天框调用图片生成 API，结果以消息形式显示在聊天区"""
        self.status_dot.setStyleSheet("color: #fbbf24; font-size: 9px; background: transparent;")
        self.status_label.setText("生成图片中...")
        self._set_send_btn_state(True)

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
        self._chat_img_thread.start()

    def _on_chat_image_done(self, image_url: str):
        """聊天区图片生成完成，下载并显示"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        # 添加"正在加载图片"消息
        msg_widget = ChatMessageWidget("正在加载图片...", is_user=False)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_widget)
        QTimer.singleShot(50, self.scroll_to_bottom)

        # 异步下载图片
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
        """在聊天消息中显示生成的图片"""
        import tempfile, os
        # 保存到临时文件
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "generated")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"img_{int(__import__('time').time() * 1000)}.png")
        pix.save(tmp_path, "PNG")

        # 用本地文件路径渲染图片
        from PySide6.QtCore import QUrl
        md = f'<img src="{QUrl.fromLocalFile(tmp_path).toString()}" style="max-width:400px; border-radius:12px;" />'
        msg_widget.update_text(md)
        msg_widget.on_streaming_finished()
        # 持久化
        if self.current_session_id:
            self.storage.add_message(self.current_session_id, "assistant", "[AI 生成图片]")
        QTimer.singleShot(50, self.scroll_to_bottom)

    def _on_chat_image_error(self, msg: str):
        """聊天区图片生成失败"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)
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

    def _show_terminal_button(self):
        """终端按钮已常在，无需操作"""
        pass

    def _hide_terminal_button(self):
        """终端按钮已常在，无需操作"""
        pass

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
            self.send_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #6366f1, stop:1 #a855f7);
                    color: white;
                    border: none;
                    border-radius: 22px;
                    font-size: 18px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #4f46e5, stop:1 #9333ea);
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #4338ca, stop:1 #7e22ce);
                }
            """)

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
                        border-color: #6366f1;
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
        """拦截输入框的粘贴事件，支持粘贴图片"""
        if obj is self.message_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier:
                clipboard = QApplication.clipboard()
                mime = clipboard.mimeData()
                if mime.hasImage():
                    image = clipboard.image()
                    if not image.isNull():
                        self._add_image(image)
                    return True
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
        welcome_text = ("欢迎使用 LLM 智能体对话系统！\n\n"
                        "我是您的AI助手，可以帮您解答问题、编写代码、创意写作等。\n"
                        "请在下方输入您的问题。")
        self.add_message(welcome_text, is_user=False)

    def add_message(self, text: str, is_user: bool, turn_id: int = 0,
                    timestamp: str = "", thinking_time: str = ""):
        """添加消息到聊天区域"""
        message_widget = ChatMessageWidget(text, is_user,
                                           timestamp=timestamp,
                                           thinking_time=thinking_time)

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
        """滚动到底部"""
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def send_message(self):
        """发送消息（支持文字 + 图片）"""
        message = self.message_input.text().strip()
        has_images = len(self._attached_images) > 0
        if not message and not has_images:
            return

        # 开始新一轮对话（用于回滚）
        self._current_turn_id = self._rollback_mgr.begin_turn()

        # 检查是否选中了图片生成模型
        display_name = self.model_combo.currentText()
        model_info = find_model_by_display(display_name)
        if model_info and model_info.get("type") == "image":
            # 图片生成模式
            self.add_message(message or "[图片生成]", is_user=True, turn_id=self._current_turn_id,
                            timestamp=datetime.now().strftime("%H:%M:%S"))
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
            self.storage.add_message(self.current_session_id, "user", display_text)

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
        QTimer.singleShot(200, self.call_llm_api)

    def call_llm_api(self):
        """调用 LLM API（后台线程，支持文字 + 图片）"""
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
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        QTimer.singleShot(50, self.scroll_to_bottom)

        self._api_full_text = ""
        self._api_message_widget = message_widget

        # Agent 模式：创建可折叠思考组件和工具状态组件
        self._thinking_widget = None
        self._tool_status_widget = None
        if self.chat_service.agent_mode:
            from ui.widgets import CollapsibleThinking
            self._thinking_widget = CollapsibleThinking()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._thinking_widget)
            
            # 添加工具调用状态显示组件
            self._tool_status_widget = ToolStatusWidget()
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._tool_status_widget)
            
            QTimer.singleShot(50, self.scroll_to_bottom)

        self._api_thread = QThread()
        self._api_worker = ApiWorker(
            chat_service=self.chat_service,
            session_id=self.current_session_id,
            user_message=user_content,
            model_display=display_name,
            temperature=temperature,
            max_tokens=max_tokens,
            max_steps=self.steps_slider.value(),
        )
        self._api_worker.moveToThread(self._api_thread)

        self._api_thread.started.connect(self._api_worker.run)
        self._api_worker.chunk_ready.connect(self._on_api_chunk)
        self._api_worker.finished.connect(self._on_api_finished)
        self._api_worker.error.connect(self._on_api_error)
        self._api_worker.agent_step.connect(self._on_agent_step)
        self._api_worker.agent_thinking.connect(self._on_agent_thinking)
        self._api_worker.agent_done.connect(self._on_agent_done)

        self._api_thread.start()

    def _on_api_chunk(self, chunk: str):
        self._api_full_text += chunk
        # 节流：使用定时器批量更新 UI，避免每个 chunk 都刷新
        if not hasattr(self, '_chunk_update_timer'):
            self._chunk_update_timer = QTimer()
            self._chunk_update_timer.setSingleShot(True)
            self._chunk_update_timer.timeout.connect(self._flush_chunk_update)
        if not self._chunk_update_timer.isActive():
            self._chunk_update_timer.start(50)  # 50ms 节流

    def _flush_chunk_update(self):
        """批量刷新 chunk 更新"""
        if self._api_message_widget and self._api_full_text:
            self._api_message_widget.update_text(self._api_full_text)
            # 使用 singleShot 避免阻塞
            QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_agent_thinking(self, text: str):
        """Agent 模式：显示初始思考状态"""
        if self._thinking_widget:
            self._thinking_widget.expand()
            self._thinking_widget.append_thinking(text)
            QTimer.singleShot(0, self.scroll_to_bottom)

    def _on_agent_step(self, step_text: str):
        """Agent 模式：流式显示工具调用步骤"""
        if self._thinking_widget:
            self._thinking_widget.append_thinking(step_text)
            QTimer.singleShot(0, self.scroll_to_bottom)

        # 更新工具调用状态显示
        if self._tool_status_widget and "调用工具:" in step_text:
            import re
            tool_match = re.search(r"调用工具: (\w+)", step_text)
            if tool_match:
                tool_name = tool_match.group(1)
                self._tool_status_widget.add_tool_call(tool_name, step_text[:100])

        # 检测是否调用了终端工具
        if "run_command" in step_text:
            self._show_terminal_button()
            # 提取命令和输出
            import re
            cmd_match = re.search(r"输入: `(.+?)`", step_text)
            result_match = re.search(r"结果: (.+?)$", step_text, re.DOTALL)
            if cmd_match:
                self.terminal_widget.append_command(cmd_match.group(1))
            if result_match:
                self.terminal_widget.append_output(result_match.group(1)[:500])

    def _on_agent_done(self):
        """Agent 模式：思考完成，自动折叠"""
        if self._thinking_widget:
            self._thinking_widget.set_final()
        if self._tool_status_widget:
            self._tool_status_widget.update_last_status(True)

    def _on_api_finished(self):
        """API 调用完成"""
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        # 确保节流定时器的更新被刷新
        if hasattr(self, '_chunk_update_timer') and self._chunk_update_timer.isActive():
            self._chunk_update_timer.stop()
            self._flush_chunk_update()

        # 计算耗时
        elapsed = time.time() - getattr(self, '_api_start_time', time.time())
        if elapsed >= 60:
            time_str = f"⏱ 耗时 {int(elapsed // 60)}分{int(elapsed % 60)}秒"
        elif elapsed >= 1:
            time_str = f"⏱ 耗时 {elapsed:.1f}秒"
        else:
            time_str = f"⏱ 耗时 {int(elapsed * 1000)}ms"
        self._api_message_widget.set_thinking_time(time_str)

        self._api_message_widget.on_streaming_finished()
        ai_text = self._api_message_widget.get_text()
        if ai_text and self.current_session_id:
            self.storage.add_message(self.current_session_id, "assistant", ai_text)

        # 智能体模式下添加回退按钮
        turn_id = self._current_turn_id
        if turn_id > 0 and self.chat_service.agent_mode:
            self._add_rollback_button(turn_id)

        # 更新上下文使用率
        self._update_context_display()
        # 将当前会话移到列表顶部（最新消息的会话排在最前面）
        self._move_session_to_top(self.current_session_id)
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
        """清理 API 线程和 Worker，防止内存泄漏"""
        if hasattr(self, '_api_thread') and self._api_thread:
            self._api_thread.quit()
            self._api_thread.wait()
            if hasattr(self, '_api_worker') and self._api_worker:
                self._api_worker.deleteLater()
                self._api_worker = None
            self._api_thread.deleteLater()
            self._api_thread = None

    def _add_rollback_button(self, turn_id: int):
        """给上一条用户消息添加回退按钮"""
        wrapper = getattr(self, '_last_user_msg_wrapper', None)
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
            parts.append(f"已恢复 {len(restored)} 个文件")
        if deleted:
            parts.append(f"已删除 {len(deleted)} 个新建文件")
        if errors:
            parts.append(f"错误: {'; '.join(errors)}")

        msg = "↩ 回滚完成：" + "，".join(parts) if parts else "↩ 本轮没有文件修改需要回滚"
        self.add_message(msg, is_user=False)
        self.show_toast("回滚完成")

    def _on_api_error(self, error_msg: str):
        # 恢复状态
        self.status_dot.setStyleSheet("color: #4ade80; font-size: 10px; background: transparent;")
        self.status_label.setText("在线")
        self._set_send_btn_state(False)

        if "MIMO_API_KEY" in error_msg or "未找到环境变量" in error_msg:
            text = ("⚠ 未配置 API Key，请设置环境变量 MIMO_API_KEY\n\n"
                    f"设置方法：set MIMO_API_KEY=your-api-key\n\n"
                    f"详细错误：{error_msg}")
        else:
            text = f"⚠ 请求出错：{error_msg}"
        self._api_message_widget.update_text(text)
        self._api_message_widget.on_streaming_finished()
        # 持久化错误消息
        if self.current_session_id:
            self.storage.add_message(self.current_session_id, "assistant", text)
        self._cleanup_api_thread()

    def on_stop_generation(self):
        """停止生成"""
        if self._api_worker:
            self._api_worker.request_stop()
        if self._api_thread:
            self._api_thread.quit()
            self._api_thread.wait(3000)
        self._cleanup_api_thread()
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
            item_widget = SessionItemWidget(title, display_time)
            item_widget.delete_clicked.connect(
                lambda checked=False, w=item_widget: self.delete_session(w))
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
            # 恢复文件树路径
            fp = self.sessions[first_id].get("file_path", "")
            if fp and os.path.isdir(fp):
                self._set_file_tree_root(fp)
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
        return "新对话"

    def save_current_session(self):
        """保存当前会话到 sessions 字典（记忆由 ChatService 自动管理）"""
        if not self.current_session_id:
            return
        messages = self._collect_messages()
        title = self._generate_title(messages)
        self.sessions[self.current_session_id]["messages"] = messages
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

        item_widget = SessionItemWidget(title, current_time)
        item_widget.delete_clicked.connect(
            lambda checked=False, w=item_widget: self.delete_session(w))
        list_item = QListWidgetItem()
        list_item.setSizeHint(item_widget.sizeHint())
        self.session_list.insertItem(0, list_item)
        self.session_list.setItemWidget(list_item, item_widget)

        self.sessions[session_id] = {
            "title": title,
            "time": current_time,
            "messages": [],
            "list_item": list_item,
            "widget": item_widget,
        }
        self.chat_service.switch_session(session_id)
        self.current_session_id = session_id
        return session_id

    def _clear_chat_area(self):
        """清空聊天区域"""
        while self.chat_layout.count() > 1:
            child = self.chat_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _restore_chat_messages(self, messages: list):
        """根据消息列表重建聊天区域"""
        self._clear_chat_area()
        if not messages:
            self.add_welcome_message()
        else:
            for msg in messages:
                if len(msg) >= 3:
                    text, is_user, timestamp = msg[0], msg[1], msg[2]
                elif len(msg) == 2:
                    text, is_user = msg
                    timestamp = ""
                else:
                    continue
                msg_widget = ChatMessageWidget(text, is_user, timestamp=timestamp)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg_widget)
            QTimer.singleShot(200, self.scroll_to_bottom)
            # 二次确保滚动到底部（HTML 渲染可能延迟）
            QTimer.singleShot(500, self.scroll_to_bottom)

    def new_chat(self):
        """新建对话（在当前会话中重新开始）"""
        self.save_current_session()
        if self.current_session_id:
            self.chat_service.clear_session(self.current_session_id)
            self.storage.clear_messages(self.current_session_id)
        self._clear_chat_area()
        self.add_welcome_message()
        if self.current_session_id and self.current_session_id in self.sessions:
            self.sessions[self.current_session_id]["messages"] = []
        self.show_toast("已创建新对话")

    def new_session(self):
        """新建会话"""
        self.save_current_session()
        self._create_new_session("新对话")
        self._clear_chat_area()
        self.add_welcome_message()
        self.show_toast("已创建新会话")

    def _toggle_sessions(self):
        """折叠/展开历史对话列表"""
        self._sessions_collapsed = not self._sessions_collapsed
        if self._sessions_collapsed:
            self.session_list.hide()
            self.session_list.setMaximumHeight(0)
            self.session_arrow.setText("▸")
        else:
            self.session_list.setMaximumHeight(16777215)
            self.session_list.show()
            self.session_arrow.setText("▾")
        self._update_sep_visibility()
        self._update_session_count()

    def _toggle_file_tree(self):
        """折叠/展开文件浏览器"""
        self._file_collapsed = not self._file_collapsed
        if self._file_collapsed:
            self.file_tree.hide()
            self.file_tree.setMaximumHeight(0)
            self.file_arrow.setText("▸")
        else:
            self.file_tree.setMaximumHeight(16777215)
            self.file_tree.show()
            self.file_arrow.setText("▾")
        self._update_sep_visibility()

    def _update_sep_visibility(self):
        """分隔线和弹性空间的可见性"""
        both_collapsed = self._sessions_collapsed and self._file_collapsed
        self._sep.hide() if both_collapsed else self._sep.show()
        # 弹性空间：仅在文件树折叠时显示（文件树展开时自己填满空间）
        self._left_spacer.show() if self._file_collapsed else self._left_spacer.hide()

    def _update_session_count(self):
        """更新会话数量显示"""
        count = self.session_list.count()
        if self._sessions_collapsed:
            self.session_count_label.setText(f"({count})")
        else:
            self.session_count_label.setText("")

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
        # 使用带时间戳的消息格式
        display_messages = self.storage.get_display_messages(target_id)
        self._restore_chat_messages(display_messages)
        self._update_context_display()
        # 恢复该会话的文件树路径
        fp = session.get("file_path", "")
        if fp and os.path.isdir(fp):
            self._set_file_tree_root(fp)
        # 滚动到对话底部
        QTimer.singleShot(100, self.scroll_to_bottom)
        self.show_toast(f"已切换到: {session['title']}")

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
                return

    def on_model_selected(self, model_name: str):
        """模型选择事件（从右侧卡片点击）"""
        index = self.model_combo.findText(model_name)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.show_toast(f"已切换到模型: {model_name}")

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

    def _set_file_tree_root(self, folder: str):
        """设置文件树根目录"""
        index = self.file_model.setRootPath(folder)
        self.file_tree.setRootIndex(index)
        folder_name = folder.split("/")[-1] if "/" in folder else folder.split("\\")[-1]
        self.file_title_label.setText(folder_name)
        if self._file_collapsed:
            self._toggle_file_tree()
        else:
            self.file_tree.show()

    def _on_file_double_click(self, index):
        """双击文件在右侧查看器中打开"""
        path = self.file_model.filePath(index)
        if self.file_model.isDir(index):
            return
        self._show_file_in_viewer(path)

    def _show_file_in_viewer(self, path: str):
        """在文件查看器中显示文件内容"""
        import os
        # 跳过二进制文件
        ext = os.path.splitext(path)[1].lower()
        text_exts = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json', '.xml',
                     '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf',
                     '.sh', '.bat', '.ps1', '.cmd', '.sql', '.csv', '.log', '.env',
                     '.gitignore', '.dockerfile', '.makefile', '.c', '.cpp', '.h', '.hpp',
                     '.java', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.r'}
        if ext not in text_exts and ext != '':
            self.file_viewer_content.setPlainText(f"不支持预览此文件类型: {ext}")
            self.file_viewer_name.setText(os.path.basename(path))
            self.file_viewer_panel.show()
            return

        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(500000)  # 最多 500KB
            self.file_viewer_content.setPlainText(content)
        except Exception as e:
            self.file_viewer_content.setPlainText(f"读取失败: {e}")

        self.file_viewer_name.setText(os.path.basename(path))
        self.file_viewer_panel.show()

    def _close_file_viewer(self):
        """关闭文件查看器"""
        self.file_viewer_panel.hide()

    def _on_file_context_menu(self, pos):
        """文件树右键菜单"""
        index = self.file_tree.indexAt(pos)
        if not index.isValid():
            return
        path = self.file_model.filePath(index)
        is_dir = self.file_model.isDir(index)

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #ffffff; border: 1px solid #e0e0e5; border-radius: 8px;
                    padding: 4px; font-size: 13px; }
            QMenu::item { padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background: rgba(99,102,241,0.12); color: #6366f1; }
        """)

        if is_dir:
            action_new_dir = menu.addAction("📁 新建目录")
            action_new_dir.triggered.connect(lambda: self._create_new_item(path, is_dir=True))
            action_new_file = menu.addAction("📄 新建文件")
            action_new_file.triggered.connect(lambda: self._create_new_item(path, is_dir=False))
            menu.addSeparator()
            action_open = menu.addAction("📂 打开文件夹")
            action_open.triggered.connect(lambda: self._open_in_explorer(path))
        else:
            action_view = menu.addAction("👁 查看文件")
            action_view.triggered.connect(lambda: self._show_file_in_viewer(path))
            action_open = menu.addAction("📝 用外部程序打开")
            action_open.triggered.connect(lambda: self._open_in_explorer(path))

        menu.addSeparator()

        action_copy_path = menu.addAction("📋 复制绝对路径")
        action_copy_path.triggered.connect(lambda: self._copy_to_clipboard(path))

        action_copy_name = menu.addAction("📄 复制文件名")
        action_copy_name.triggered.connect(
            lambda: self._copy_to_clipboard(os.path.basename(path)))

        menu.addSeparator()

        action_delete = menu.addAction("🗑 删除")
        action_delete.triggered.connect(lambda: self._delete_item(path))

        menu.exec_(self.file_tree.viewport().mapToGlobal(pos))

    def _on_root_path_context_menu(self, pos):
        """根路径右键菜单"""
        root_path = self.file_model.rootPath()
        if not root_path:
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #ffffff; border: 1px solid #e0e0e5; border-radius: 8px;
                    padding: 4px; font-size: 13px; }
            QMenu::item { padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background: rgba(99,102,241,0.12); color: #6366f1; }
        """)

        action_new_dir = menu.addAction("📁 新建目录")
        action_new_dir.triggered.connect(lambda: self._create_new_item(root_path, is_dir=True))

        action_new_file = menu.addAction("📄 新建文件")
        action_new_file.triggered.connect(lambda: self._create_new_item(root_path, is_dir=False))

        menu.addSeparator()

        action_copy_path = menu.addAction("📋 复制绝对路径")
        action_copy_path.triggered.connect(lambda: self._copy_to_clipboard(root_path))

        action_open = menu.addAction("📂 在资源管理器中打开")
        action_open.triggered.connect(lambda: self._open_in_explorer(root_path))

        menu.addSeparator()

        action_delete = menu.addAction("🗑 删除")
        action_delete.triggered.connect(lambda: self._delete_item(root_path))

        menu.exec_(self.file_title_label.mapToGlobal(pos))

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
        """加载背景配置文件"""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "background_config.json")
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    bg_config = json.load(f)
                    self.background_image = bg_config.get('image_path', '')
                    self.background_opacity = bg_config.get('opacity', 0.3)
        except Exception as e:
            print(f"加载背景配置失败: {e}")

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

    def show_settings(self):
        """显示设置对话框"""
        current_settings = {
            'background_image': self.background_image,
            'background_opacity': self.background_opacity,
            'temperature': self.temp_slider.value() / 100.0 if hasattr(self, 'temp_slider') else 0.7,
            'max_tokens': self.token_slider.value() if hasattr(self, 'token_slider') else 2048,
            'max_steps': self.steps_slider.value() if hasattr(self, 'steps_slider') else 10,
        }

        dialog = SettingsDialog(self, current_settings)
        dialog.settings_changed.connect(self._apply_settings)
        dialog.exec()

    def _apply_settings(self, settings):
        """应用设置"""
        self.background_image = settings.get('background_image', '')
        self.background_opacity = settings.get('background_opacity', 0.3)

        # 更新参数滑块
        if hasattr(self, 'temp_slider'):
            self.temp_slider.setValue(int(settings.get('temperature', 0.7) * 100))
        if hasattr(self, 'token_slider'):
            self.token_slider.setValue(settings.get('max_tokens', 2048))
        if hasattr(self, 'steps_slider'):
            self.steps_slider.setValue(settings.get('max_steps', 10))

        # 保存配置
        self._save_config()

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
                return
        central.clear_background()
        self._set_child_transparency(False)

    def _set_child_transparency(self, enabled: bool):
        """设置子面板是否半透明（让背景图可见）"""
        if enabled:
            a = 0.7  # 面板透明度，越小越透明
            semi = f"rgba(240, 242, 245, {a})"
            semi_w = f"rgba(255, 255, 255, {a})"

            # 左侧面板（历史对话）
            self._left_panel.setStyleSheet(
                f"background: {semi_w}; border-right: 1px solid rgba(0,0,0,0.06);")
            self.session_list.setStyleSheet(
                f"QListWidget {{ background: transparent; border: none; outline: none; }}"
                f"QListWidget::item {{ background: transparent; border: none; padding: 4px 6px; }}"
                f"QListWidget::item:selected {{ background: rgba(99,102,241,0.15); border-radius: 10px; }}"
                f"QListWidget::item:hover {{ background: rgba(99,102,241,0.08); border-radius: 10px; }}")
            self.session_list.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))

            # 中间聊天区
            self._middle_widget.setStyleSheet(f"background: {semi};")
            self.chat_container.setStyleSheet(f"background: transparent;")
            # QScrollArea 及其 viewport 必须透明
            self.chat_scroll.setStyleSheet(
                f"QScrollArea {{ background: transparent; border: none; }}"
                f"QScrollArea > QWidget {{ background: transparent; }}"
                f"QWidget {{ background: transparent; }}")
            self.chat_scroll.viewport().setStyleSheet("background: transparent;")
            self.chat_scroll.verticalScrollBar().setStyleSheet(get_style('scrollbar_chat'))
            self._input_container.setStyleSheet(
                f"background: {semi}; border-top: 1px solid rgba(0,0,0,0.04);")

            # 文件浏览器
            self.file_viewer_panel.setStyleSheet(
                f"background: {semi_w}; border: 1px solid rgba(0,0,0,0.05); border-radius: 10px;")
            self.file_tree.setStyleSheet(f"""
                QTreeView {{
                    background: transparent; color: #000000; border: none;
                    font-size: 12px; outline: none;
                }}
                QTreeView::item {{ padding: 4px 0; border-radius: 4px; color: #000000; }}
                QTreeView::item:selected {{ background: rgba(99, 102, 241, 0.15); color: #000000; }}
                QTreeView::item:hover {{ background: rgba(99, 102, 241, 0.08); color: #000000; }}
            """)
            self.file_tree.viewport().setStyleSheet("background: transparent;")
            self.file_tree.verticalScrollBar().setStyleSheet(get_style('scrollbar_dark'))
        else:
            self._left_panel.setStyleSheet(get_style('left_panel'))
            self.session_list.setStyleSheet(get_style('session_list'))
            self.session_list.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))
            self._middle_widget.setStyleSheet(get_style('middle'))
            self.chat_container.setStyleSheet("background: #f0f2f5;")
            self.chat_scroll.setStyleSheet(get_style('chat_scroll'))
            self.chat_scroll.verticalScrollBar().setStyleSheet(get_style('scrollbar_chat'))
            self.chat_scroll.viewport().setStyleSheet("")
            self._input_container.setStyleSheet(
                "background: #f4f5f7; border-top: 1px solid rgba(0, 0, 0, 0.05);")

            self.file_viewer_panel.setStyleSheet(
                "background: #ffffff; border: 1px solid rgba(0, 0, 0, 0.05); border-radius: 10px;")
            self.file_tree.setStyleSheet(get_style('file_tree'))
            self.file_tree.viewport().setStyleSheet("background: transparent;")
            self.file_tree.verticalScrollBar().setStyleSheet(get_style('scrollbar_light'))

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)

    def closeEvent(self, event):
        """窗口关闭时清理资源，防止内存泄漏"""
        # 停止正在进行的 API 调用
        if hasattr(self, '_api_worker') and self._api_worker:
            self._api_worker.request_stop()
        self._cleanup_api_thread()
        # 停止所有定时器
        if hasattr(self, '_chunk_update_timer'):
            self._chunk_update_timer.stop()
        super().closeEvent(event)

    def center_on_screen(self):
        """将窗口居中显示在屏幕上"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            w = min(1200, screen_geometry.width() - 100)
            h = min(720, screen_geometry.height() - 100)
            x = screen_geometry.x() + (screen_geometry.width() - w) // 2
            y = screen_geometry.y() + (screen_geometry.height() - h) // 2
            self.setGeometry(x, y, w, h)
