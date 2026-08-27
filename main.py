"""
LLM 智能体对话系统 - 入口文件
"""

import sys
import os

# 将项目根目录加入 sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ── Windows 任务栏图标修复 ──
# 必须在 QApplication 创建之前调用，否则 Windows 会用 python.exe 的默认图标
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SkyCode.LLMAgent.1.0")
    except Exception:
        pass

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
