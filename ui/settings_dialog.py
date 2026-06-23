"""
设置对话框模块
实现背景图片选择、透明度设置、模型参数调节、自定义模型管理等功能
"""

import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QFileDialog, QWidget,
                               QGroupBox, QSizePolicy, QLineEdit, QTextEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QInputDialog, QTabWidget)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap

from services.custom_model_service import CustomModelService, CustomModel


class SettingsDialog(QDialog):
    """设置对话框类"""
    settings_changed = Signal(dict)

    def __init__(self, parent=None, current_settings=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self.setMinimumHeight(500)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint | Qt.MSWindowsFixedSizeDialogHint)

        self.current_settings = current_settings or {}
        self.background_image = self.current_settings.get('background_image', '')
        self.background_opacity = self.current_settings.get('background_opacity', 0.3)

        self.custom_model_service = CustomModelService()

        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 创建选项卡
        tab_widget = QTabWidget()
        tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid rgba(0,0,0,0.06); border-radius: 8px; background: white; }
            QTabBar::tab { background: #f0f0f3; color: #6b7280; border: 1px solid #d1d5db;
                          border-bottom: none; border-radius: 6px 6px 0 0; padding: 8px 16px;
                          margin-right: 2px; font-size: 12px; font-weight: bold; }
            QTabBar::tab:selected { background: white; color: #4f46e5; border-color: #4f46e5; }
            QTabBar::tab:hover { background: #e5e7eb; }
        """)

        # 通用设置选项卡
        general_tab = QWidget()
        general_tab.setStyleSheet("background: transparent;")
        self._setup_general_tab(general_tab)
        tab_widget.addTab(general_tab, "通用设置")

        # 自定义模型选项卡
        model_tab = QWidget()
        model_tab.setStyleSheet("background: transparent;")
        self._setup_model_tab(model_tab)
        tab_widget.addTab(model_tab, "自定义模型")

        layout.addWidget(tab_widget)

        # ── 按钮 ──
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #d1d5db;
                          border-radius: 18px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("保存")
        save_btn.setFixedSize(80, 36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
                          color: white; border-radius: 18px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4338ca, stop:1 #6d28d9); }
        """)
        save_btn.clicked.connect(self.save_settings)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addWidget(btn_row)

    def _setup_general_tab(self, tab):
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── 模型参数组 ──
        param_group = self._make_group("模型参数")
        param_layout = QVBoxLayout(param_group)

        self.temp_slider, self.temp_value = self._make_slider(
            "温度 (Temperature)", 0, 100, 70, param_layout, suffix="")
        self.token_slider, self.token_value = self._make_slider(
            "最大 Token 数", 256, 4096, 2048, param_layout, suffix="")
        self.steps_slider, self.steps_value = self._make_slider(
            "推理步数 (Agent)", 1, 30, 10, param_layout, suffix="")

        layout.addWidget(param_group)

        # ── 背景设置组 ──
        bg_group = self._make_group("背景设置")
        bg_layout = QVBoxLayout(bg_group)

        # 图片选择
        img_row = QWidget()
        img_row.setStyleSheet("background: transparent;")
        img_layout = QHBoxLayout(img_row)
        img_layout.setContentsMargins(0, 0, 0, 0)
        img_layout.setSpacing(10)

        self.bg_image_preview = QLabel("未选择图片")
        self.bg_image_preview.setFixedSize(100, 60)
        self.bg_image_preview.setAlignment(Qt.AlignCenter)
        self.bg_image_preview.setStyleSheet(
            "background: #f0f0f3; border: 1px solid #d1d5db; border-radius: 6px; color: #9ca3af; font-size: 11px;")

        img_btn = QPushButton("选择图片")
        img_btn.setFixedHeight(32)
        img_btn.setCursor(Qt.PointingHandCursor)
        img_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #d1d5db;
                          border-radius: 8px; font-size: 12px; font-weight: bold; padding: 0 16px; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        img_btn.clicked.connect(self.select_background_image)

        clear_btn = QPushButton("清除")
        clear_btn.setFixedHeight(32)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #6b7280; border: 1px solid #d1d5db;
                          border-radius: 8px; font-size: 12px; padding: 0 12px; }
            QPushButton:hover { color: #ef4444; border-color: #fca5a5; }
        """)
        clear_btn.clicked.connect(self.reset_background)

        img_layout.addWidget(self.bg_image_preview)
        img_layout.addWidget(img_btn)
        img_layout.addWidget(clear_btn)
        img_layout.addStretch()
        bg_layout.addWidget(img_row)

        # 透明度
        self.bg_opacity_slider, self.bg_opacity_value = self._make_slider(
            "背景透明度", 0, 100, int(self.background_opacity * 100), bg_layout, suffix="%")

        layout.addWidget(bg_group)
        layout.addStretch()

    def _setup_model_tab(self, tab):
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # 模型列表
        list_group = self._make_group("自定义模型列表")
        list_layout = QVBoxLayout(list_group)

        # 按钮行
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        add_btn = QPushButton("添加模型")
        add_btn.setFixedHeight(32)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet("""
            QPushButton { background: #4f46e5; color: white; border: none;
                          border-radius: 8px; font-size: 12px; font-weight: bold; padding: 0 16px; }
            QPushButton:hover { background: #4338ca; }
        """)
        add_btn.clicked.connect(self._add_custom_model)

        edit_btn = QPushButton("编辑")
        edit_btn.setFixedHeight(32)
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #d1d5db;
                          border-radius: 8px; font-size: 12px; padding: 0 16px; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        edit_btn.clicked.connect(self._edit_custom_model)

        delete_btn = QPushButton("删除")
        delete_btn.setFixedHeight(32)
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #ef4444; border: 1px solid #fca5a5;
                          border-radius: 8px; font-size: 12px; padding: 0 16px; }
            QPushButton:hover { background: #fef2f2; }
        """)
        delete_btn.clicked.connect(self._delete_custom_model)

        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()

        # 模型列表
        self.model_list = QListWidget()
        self.model_list.setMinimumHeight(200)
        self.model_list.setStyleSheet("""
            QListWidget { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                         padding: 4px; font-size: 12px; }
            QListWidget::item { padding: 8px; border-radius: 4px; margin: 2px; }
            QListWidget::item:selected { background: rgba(79, 70, 229, 0.1); color: #4f46e5; }
            QListWidget::item:hover { background: rgba(79, 70, 229, 0.05); }
        """)

        list_layout.addWidget(btn_row)
        list_layout.addWidget(self.model_list)
        layout.addWidget(list_group)

        # 加载模型列表
        self._refresh_model_list()

    def _refresh_model_list(self):
        """刷新模型列表"""
        self.model_list.clear()
        models = self.custom_model_service.get_all()
        for model_id, model in models.items():
            item = QListWidgetItem(f"{model.name} ({model.provider}/{model.model_id})")
            item.setData(Qt.UserRole, model_id)
            self.model_list.addItem(item)

    def _add_custom_model(self):
        """添加自定义模型"""
        dialog = CustomModelDialog(self)
        if dialog.exec() == QDialog.Accepted:
            model = dialog.get_model()
            if model:
                if self.custom_model_service.add(model):
                    self._refresh_model_list()
                    QMessageBox.information(self, "成功", "模型添加成功！")
                else:
                    QMessageBox.warning(self, "错误", "模型已存在！")

    def _edit_custom_model(self):
        """编辑自定义模型"""
        current = self.model_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选择要编辑的模型")
            return

        model_id = current.data(Qt.UserRole)
        model = self.custom_model_service.get(model_id)
        if not model:
            return

        dialog = CustomModelDialog(self, model)
        if dialog.exec() == QDialog.Accepted:
            updated_model = dialog.get_model()
            if updated_model:
                self.custom_model_service.update(model_id, updated_model)
                self._refresh_model_list()
                QMessageBox.information(self, "成功", "模型更新成功！")

    def _delete_custom_model(self):
        """删除自定义模型"""
        current = self.model_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选择要删除的模型")
            return

        model_id = current.data(Qt.UserRole)
        model = self.custom_model_service.get(model_id)
        if not model:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除模型 '{model.name}' 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.custom_model_service.delete(model_id)
            self._refresh_model_list()
            QMessageBox.information(self, "成功", "模型删除成功！")

    def _make_group(self, title):
        g = QGroupBox(title)
        g.setStyleSheet("""
            QGroupBox { font-size: 14px; font-weight: bold; color: #1a1a2e;
                        border: 1px solid rgba(0,0,0,0.06); border-radius: 10px;
                        margin-top: 10px; padding-top: 14px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        """)
        return g

    def _make_slider(self, label_text, min_val, max_val, default, parent_layout, suffix=""):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        header = QWidget()
        header.setStyleSheet("background: transparent;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label_text)
        lbl.setStyleSheet("color: #6b7280; font-size: 12px; background: transparent;")
        val_lbl = QLabel(str(default) + suffix)
        val_lbl.setStyleSheet("color: #1a1a2e; font-size: 12px; font-weight: bold; background: transparent;")
        val_lbl.setFixedWidth(48)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h_lay.addWidget(lbl)
        h_lay.addStretch()
        h_lay.addWidget(val_lbl)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        slider.setStyleSheet("""
            QSlider::groove:horizontal { background: #e5e7eb; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #6366f1; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
            QSlider::handle:horizontal:hover { background: #4f46e5; }
            QSlider::sub-page:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #7c3aed); border-radius: 3px; }
        """)
        sfx = suffix
        slider.valueChanged.connect(lambda v, lbl=val_lbl, s=sfx: lbl.setText(str(v) + s))

        lay.addWidget(header)
        lay.addWidget(slider)
        parent_layout.addWidget(row)

        return slider, val_lbl

    def select_background_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.background_image = path
            self._update_preview()

    def _update_preview(self):
        if self.background_image and os.path.exists(self.background_image):
            pix = QPixmap(self.background_image)
            if not pix.isNull():
                scaled = pix.scaled(100, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.bg_image_preview.setPixmap(scaled)
                self.bg_image_preview.setStyleSheet(
                    "background: #f0f0f3; border: 1px solid #6366f1; border-radius: 6px;")
                return
        self.bg_image_preview.clear()
        self.bg_image_preview.setText("未选择图片")
        self.bg_image_preview.setStyleSheet(
            "background: #f0f0f3; border: 1px solid #d1d5db; border-radius: 6px; color: #9ca3af; font-size: 11px;")

    def reset_background(self):
        self.background_image = ""
        self._update_preview()

    def load_settings(self):
        if self.background_image and os.path.exists(self.background_image):
            self._update_preview()
        # 从 current_settings 加载参数
        self.temp_slider.setValue(int(self.current_settings.get('temperature', 0.7) * 100))
        self.token_slider.setValue(self.current_settings.get('max_tokens', 2048))
        self.steps_slider.setValue(self.current_settings.get('max_steps', 10))

    def update_opacity_value(self, value):
        self.bg_opacity_value.setText(f"{value}%")

    def save_settings(self):
        settings = {
            'background_image': self.background_image,
            'background_opacity': self.bg_opacity_slider.value() / 100.0,
            'temperature': self.temp_slider.value() / 100.0,
            'max_tokens': self.token_slider.value(),
            'max_steps': self.steps_slider.value(),
        }
        self.settings_changed.emit(settings)
        self.accept()


class CustomModelDialog(QDialog):
    """自定义模型编辑对话框"""

    def __init__(self, parent=None, model: CustomModel = None):
        super().__init__(parent)
        self.setWindowTitle("添加自定义模型" if model is None else "编辑自定义模型")
        self.setMinimumWidth(400)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint | Qt.MSWindowsFixedSizeDialogHint)

        self.model = model
        self.setup_ui()

        if model:
            self._load_model(model)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 模型名称
        name_label = QLabel("模型名称 *")
        name_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如: 我的 GPT-4")
        self.name_input.setStyleSheet("""
            QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QLineEdit:focus { border-color: #4f46e5; }
        """)

        # 提供商
        provider_label = QLabel("提供商 *")
        provider_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.provider_input = QLineEdit()
        self.provider_input.setPlaceholderText("例如: openai, anthropic, custom")
        self.provider_input.setStyleSheet("""
            QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QLineEdit:focus { border-color: #4f46e5; }
        """)

        # 模型 ID
        model_id_label = QLabel("模型 ID *")
        model_id_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.model_id_input = QLineEdit()
        self.model_id_input.setPlaceholderText("例如: gpt-4, claude-3-opus")
        self.model_id_input.setStyleSheet("""
            QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QLineEdit:focus { border-color: #4f46e5; }
        """)

        # API 地址
        url_label = QLabel("API 地址 *")
        url_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例如: https://api.openai.com/v1")
        self.url_input.setStyleSheet("""
            QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QLineEdit:focus { border-color: #4f46e5; }
        """)

        # API Key
        key_label = QLabel("API Key *")
        key_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("输入 API Key")
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setStyleSheet("""
            QLineEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QLineEdit:focus { border-color: #4f46e5; }
        """)

        # 描述
        desc_label = QLabel("描述（可选）")
        desc_label.setStyleSheet("color: #374151; font-size: 12px; font-weight: bold;")
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("输入模型描述...")
        self.desc_input.setMaximumHeight(80)
        self.desc_input.setStyleSheet("""
            QTextEdit { background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px;
                       padding: 8px 12px; font-size: 12px; }
            QTextEdit:focus { border-color: #4f46e5; }
        """)

        # 添加到布局
        layout.addWidget(name_label)
        layout.addWidget(self.name_input)
        layout.addWidget(provider_label)
        layout.addWidget(self.provider_input)
        layout.addWidget(model_id_label)
        layout.addWidget(self.model_id_input)
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)
        layout.addWidget(key_label)
        layout.addWidget(self.key_input)
        layout.addWidget(desc_label)
        layout.addWidget(self.desc_input)

        # 按钮
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton { background: #f0f0f3; color: #1a1a2e; border: 1px solid #d1d5db;
                          border-radius: 18px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #e5e7eb; }
        """)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("保存")
        save_btn.setFixedSize(80, 36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
                          color: white; border-radius: 18px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4338ca, stop:1 #6d28d9); }
        """)
        save_btn.clicked.connect(self._on_save)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addWidget(btn_row)

    def _load_model(self, model: CustomModel):
        """加载模型数据到界面"""
        self.name_input.setText(model.name)
        self.provider_input.setText(model.provider)
        self.model_id_input.setText(model.model_id)
        self.url_input.setText(model.base_url)
        self.key_input.setText(model.api_key)
        self.desc_input.setPlainText(model.description)

    def _on_save(self):
        """保存"""
        name = self.name_input.text().strip()
        provider = self.provider_input.text().strip()
        model_id = self.model_id_input.text().strip()
        base_url = self.url_input.text().strip()
        api_key = self.key_input.text().strip()
        description = self.desc_input.toPlainText().strip()

        if not all([name, provider, model_id, base_url, api_key]):
            QMessageBox.warning(self, "错误", "请填写所有必填字段（带 * 号的）")
            return

        self.accept()

    def get_model(self) -> CustomModel:
        """获取模型配置"""
        return CustomModel(
            name=self.name_input.text().strip(),
            provider=self.provider_input.text().strip(),
            model_id=self.model_id_input.text().strip(),
            base_url=self.url_input.text().strip(),
            api_key=self.key_input.text().strip(),
            description=self.desc_input.toPlainText().strip()
        )
