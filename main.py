"""
LLM 智能体对话系统 - 入口文件
"""

import sys
import os

# 将项目根目录加入 sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import QApplication, QStyleFactory
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSize

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))

    # 设置应用图标
    ico_path = os.path.join(BASE_DIR, "assets", "ico", "app.ico")
    if os.path.exists(ico_path):
        app.setWindowIcon(QIcon(ico_path))

    window = MainWindow()

    # 同时设置窗口图标
    if os.path.exists(ico_path):
        window.setWindowIcon(QIcon(ico_path))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
