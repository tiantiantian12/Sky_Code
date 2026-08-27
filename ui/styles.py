"""
样式管理模块 - 亮色主题（美化版 v2）
集中管理应用程序的所有样式定义
"""

# 颜色常量
COLORS = {
    'background': '#f4f5f7',
    'card': '#ffffff',
    'primary_start': '#4f46e5',
    'primary_mid': '#6d5acd',
    'primary_end': '#7c3aed',
    'accent': '#6366f1',
    'accent_hover': '#4f46e5',
    'accent_pressed': '#4338ca',
    'text': '#1a1a2e',
    'text_secondary': '#71717a',
    'text_muted': '#a1a1aa',
    'border': 'rgba(0, 0, 0, 0.05)',
    'border_light': 'rgba(0, 0, 0, 0.03)',
    'border_primary': 'rgba(79, 70, 229, 0.25)',
    'error': '#e53e3e',
    'error_hover': '#c53030',
    'success': '#38a169',
    'warning': '#d69e2e',
    'info': '#3182ce',
    'accent_purple': '#8b5cf6',
}

# 深色主题颜色常量 (DeepSeek 桌面客户端真实配色)
# 来源: DeepSeek dark 主题 token (Border 主题)
#   bg-base / bg-layer-1: #27282e    bg-layer-2: #2d2e34    bg-layer-3: #32333a
#   label-primary: hsl(232,6%,88%)≈#d9dae0   secondary: hsl(232,9%,64%)≈#9a9ca6
#   tertiary:      hsl(232,12%,48%)≈#6f7178  brand: hsl(232,70%,65%)≈#6e7fe0
DARK_COLORS = {
    'background': '#27282e',       # 主背景 - DeepSeek 深灰蓝
    'card': '#2d2e34',             # 卡片/面板背景 (layer-2)
    'surface': '#32333a',          # 浮起表面 (layer-3)
    'primary_start': '#6e7fe0',    # 强调色起始 - DeepSeek 蓝紫
    'primary_mid': '#7f8fe6',      # 强调色中间
    'primary_end': '#93a3ec',      # 强调色结束
    'accent': '#6e7fe0',           # 强调色 (brand-primary)
    'accent_hover': '#7f8fe6',
    'accent_pressed': '#5a6bcf',
    'text': '#d9dae0',             # 主文字 (label-primary)
    'text_secondary': '#9a9ca6',   # 次要文字 (label-secondary)
    'text_muted': '#6f7178',       # 弱化文字 (label-tertiary)
    'border': 'rgba(255, 255, 255, 0.06)',   # border-l1
    'border_light': 'rgba(255, 255, 255, 0.04)',
    'border_strong': 'rgba(255, 255, 255, 0.14)',  # border-l3
    'border_primary': 'rgba(110, 127, 224, 0.35)',
    'error': '#f7768e',
    'error_hover': '#e06078',
    'success': '#9ece6a',
    'warning': '#e0af68',
    'info': '#6e7fe0',
    'accent_purple': '#bb9af7',
}


# ── 动态强调色管理 ──────────────────────────────────────────────

# 默认强调色（亮色/深色）
_DEFAULT_LIGHT_ACCENT = '#4f46e5'
_DEFAULT_DARK_ACCENT = '#6e7fe0'

# 当前强调色
_current_light_accent = _DEFAULT_LIGHT_ACCENT
_current_dark_accent = _DEFAULT_DARK_ACCENT

# 默认强调色相关的颜色对 (accent, hover, pressed) for light/dark
_DEFAULT_LIGHT_ACCENT_PAIRS = {
    'accent': '#6366f1',
    'accent_hover': '#4f46e5',
    'accent_pressed': '#4338ca',
    'primary_start': '#4f46e5',
    'primary_mid': '#6d5acd',
    'primary_end': '#7c3aed',
}
_DEFAULT_DARK_ACCENT_PAIRS = {
    'accent': '#6e7fe0',
    'accent_hover': '#7f8fe6',
    'accent_pressed': '#5a6bcf',
    'primary_start': '#6e7fe0',
    'primary_mid': '#7f8fe6',
    'primary_end': '#93a3ec',
}


def _hex_to_rgb(hex_color: str) -> tuple:
    """将 #RRGGBB 格式转换为 (R, G, B) 元组"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (99, 102, 241)


def _adjust_color(hex_color: str, amount: int) -> str:
    """调整颜色亮度，amount 正数变亮，负数变暗"""
    r, g, b = _hex_to_rgb(hex_color)
    r = max(0, min(255, r + amount))
    g = max(0, min(255, g + amount))
    b = max(0, min(255, b + amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(hex_color: str, alpha: float) -> str:
    """将 #RRGGBB + alpha 转换为 rgba(r,g,b,alpha) 格式"""
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def set_accent_color(color: str, theme: str = "light"):
    """设置强调色，并动态更新所有相关样式常量。

    Args:
        color: #RRGGBB 格式的颜色值
        theme: 'light' 或 'dark'，指定更新哪个主题的强调色
    """
    global _current_light_accent, _current_dark_accent
    global COLORS, DARK_COLORS

    if theme == "dark":
        _current_dark_accent = color
        DARK_COLORS['accent'] = color
        DARK_COLORS['accent_hover'] = _adjust_color(color, 20)
        DARK_COLORS['accent_pressed'] = _adjust_color(color, -20)
        DARK_COLORS['primary_start'] = color
        DARK_COLORS['primary_mid'] = _adjust_color(color, 30)
        DARK_COLORS['primary_end'] = _adjust_color(color, 60)
        DARK_COLORS['border_primary'] = _rgba(color, 0.25)
    else:
        _current_light_accent = color
        COLORS['accent'] = color
        COLORS['accent_hover'] = _adjust_color(color, -15)
        COLORS['accent_pressed'] = _adjust_color(color, -30)
        COLORS['primary_start'] = color
        COLORS['primary_mid'] = _adjust_color(color, 30)
        COLORS['primary_end'] = _adjust_color(color, 60)
        COLORS['border_primary'] = _rgba(color, 0.25)

    # 重新生成包含强调色的样式字符串
    _regenerate_accent_styles(theme)


def _regenerate_accent_styles(theme: str = "light"):
    """重新生成包含强调色的样式字符串"""
    if theme == "dark":
        c = DARK_COLORS
        accent = c['accent']
        accent_hover = c['accent_hover']
        accent_pressed = c['accent_pressed']
        p_start = c['primary_start']
        p_mid = c['primary_mid']
        p_end = c['primary_end']

        global DARK_TOP_NAV_STYLE, DARK_SLIDER_STYLE, DARK_NEW_SESSION_BTN_STYLE
        global DARK_MODEL_CARD_SELECTED_STYLE, DARK_USER_MESSAGE_STYLE
        global DARK_LEFT_TAB_BAR_STYLE, DARK_SESSION_ITEM_ENHANCED_STYLE
        global DARK_FILE_TREE_ENHANCED_STYLE, DARK_SEARCH_BAR_STYLE
        global DARK_BREADCRUMB_STYLE, DARK_CONTEXT_MENU_STYLE

        # DeepSeek 风格：扁平、冷灰蓝底色、柔和蓝紫强调，无渐变
        DARK_TOP_NAV_STYLE = f"""
background: #27282e;
border-bottom: 1px solid rgba(255,255,255,0.06);
"""
        DARK_SLIDER_STYLE = f"""
QSlider::groove:horizontal {{
    background: rgba(255, 255, 255, 0.08);
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{
    background: {accent_hover};
}}
QSlider::handle:horizontal:pressed {{
    background: {accent_pressed};
}}
QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 3px;
}}
"""
        DARK_NEW_SESSION_BTN_STYLE = f"""
QPushButton {{
    background: #32333a;
    color: #d9dae0;
    border-radius: 10px;
    font-weight: 600;
    font-size: 12.5px;
    padding: 6px 0;
    border: 1px solid rgba(255,255,255,0.08);
}}
QPushButton:hover {{
    background: #3a3b43;
    border-color: {_rgba(accent, 0.5)};
}}
QPushButton:pressed {{
    background: #2a2b31;
}}
"""
        DARK_MODEL_CARD_SELECTED_STYLE = f"""
background: {_rgba(accent, 0.15)};
border: 1px solid {accent};
border-radius: 10px;
"""
        DARK_USER_MESSAGE_STYLE = f"""
background: #32333a;
color: #e8e9ee;
border-radius: 16px;
border-bottom-right-radius: 4px;
padding: 12px 16px;
font-size: 14px;
line-height: 1.5;
"""
        DARK_LEFT_TAB_BAR_STYLE = f"""
QWidget#left_tab_bar {{
    background: #27282e;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}
QPushButton#left_tab_btn {{
    background: transparent;
    color: #9a9ca6;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 12.5px;
    font-weight: 500;
}}
QPushButton#left_tab_btn:hover {{
    background: {_rgba(accent, 0.10)};
    color: {accent_hover};
}}
QPushButton#left_tab_btn:checked {{
    background: {_rgba(accent, 0.15)};
    color: {accent};
    font-weight: 600;
}}
"""
        DARK_SESSION_ITEM_ENHANCED_STYLE = f"""
QWidget#session_item {{
    background: transparent;
    border-radius: 8px;
    padding: 2px;
}}
QWidget#session_item:hover {{
    background: {_rgba(accent, 0.06)};
}}
QWidget#session_item[active="true"] {{
    background: {_rgba(accent, 0.12)};
    border-left: 3px solid {accent};
}}
"""
        DARK_FILE_TREE_ENHANCED_STYLE = f"""
QTreeView {{
    background: transparent;
    color: #d9dae0;
    border: none;
    font-size: 12.5px;
    outline: none;
}}
QTreeView::item {{
    padding: 5px 4px;
    border-radius: 5px;
    color: #d9dae0;
}}
QTreeView::item:selected {{
    background: {_rgba(accent, 0.18)};
    color: {accent_hover};
}}
QTreeView::item:hover {{
    background: {_rgba(accent, 0.08)};
    color: #e8e9ee;
}}
"""
        DARK_SEARCH_BAR_STYLE = f"""
QLineEdit {{
    background: rgba(255,255,255,0.04);
    color: #d9dae0;
    border: 1.5px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 7px 12px 7px 30px;
    font-size: 12px;
}}
QLineEdit:focus {{
    border-color: {accent};
    background: {_rgba(accent, 0.08)};
}}
QLineEdit::placeholder {{
    color: #6f7178;
}}
"""
        DARK_BREADCRUMB_STYLE = f"""
QWidget#breadcrumb_bar {{
    background: rgba(255,255,255,0.02);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 2px 4px;
}}
QPushButton#breadcrumb_btn {{
    background: transparent;
    color: {accent};
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11.5px;
    font-weight: 500;
}}
QPushButton#breadcrumb_btn:hover {{
    background: {_rgba(accent, 0.10)};
    color: {accent_hover};
}}
QLabel#breadcrumb_sep {{
    color: #6f7178;
    font-size: 13px;
    padding: 0 2px;
}}
"""
        DARK_CONTEXT_MENU_STYLE = f"""
QMenu {{
    background: #2d2e34;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    min-width: 180px;
}}
QMenu::item {{
    padding: 8px 32px 8px 16px;
    border-radius: 6px;
    margin: 1px 3px;
    color: #d9dae0;
}}
QMenu::item:selected {{
    background: {_rgba(accent, 0.15)};
    color: {accent};
}}
QMenu::item:disabled {{
    color: #6f7178;
    background: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: rgba(255,255,255,0.06);
    margin: 5px 10px;
}}
"""
    else:
        c = COLORS
        accent = c['accent']
        accent_hover = c['accent_hover']
        accent_pressed = c['accent_pressed']
        p_start = c['primary_start']
        p_mid = c['primary_mid']
        p_end = c['primary_end']

        global TOP_NAV_STYLE, SLIDER_STYLE, NEW_SESSION_BTN_STYLE
        global MODEL_CARD_SELECTED_STYLE, USER_MESSAGE_STYLE, VOICE_BTN_STYLE, SEND_BTN_STYLE
        global LEFT_TAB_BAR_STYLE, SESSION_ITEM_ENHANCED_STYLE, MESSAGE_INPUT_STYLE
        global FILE_TREE_ENHANCED_STYLE, SEARCH_BAR_STYLE, BREADCRUMB_STYLE
        global CONTEXT_MENU_STYLE, COLLAPSE_BTN_STYLE, EXPAND_BTN_STYLE
        global MODEL_SECTION_STYLE, PARAM_SECTION_STYLE

        TOP_NAV_STYLE = f"""
background: #f4f5f7;
border-bottom: 1px solid rgba(0,0,0,0.06);
"""
        SLIDER_STYLE = f"""
QSlider::groove:horizontal {{
    background: rgba(0, 0, 0, 0.08);
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {p_start};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{
    background: {accent_pressed};
}}
QSlider::handle:horizontal:pressed {{
    background: {accent_pressed};
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p_start}, stop:1 {p_end});
    border-radius: 3px;
}}
"""
        NEW_SESSION_BTN_STYLE = f"""
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent}, stop:1 {p_end});
    color: white;
    border-radius: 10px;
    font-weight: bold;
    font-size: 12.5px;
    padding: 6px 0;
    border: none;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p_start}, stop:1 {p_end});
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent_pressed}, stop:1 {p_mid});
}}
"""
        MODEL_CARD_SELECTED_STYLE = f"""
background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 {p_start}, stop:0.5 {accent}, stop:1 {p_end});
border-radius: 10px;
"""
        USER_MESSAGE_STYLE = f"""
background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 {p_start}, stop:0.5 {accent}, stop:1 {p_end});
border-radius: 16px;
padding: 12px 18px;
"""
        VOICE_BTN_STYLE = f"""
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p_start}, stop:1 {p_end});
    border-radius: 20px;
    color: white;
    font-size: 16px;
    border: none;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent_pressed}, stop:1 {p_mid});
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {accent_pressed}, stop:1 {p_end});
}}
"""
        SEND_BTN_STYLE = f"""
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p_start}, stop:1 {p_end});
    color: white;
    border: none;
    border-radius: 22px;
    font-size: 18px;
    font-weight: bold;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent}, stop:1 {p_mid});
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent_pressed}, stop:1 {p_end});
}}
"""
        LEFT_TAB_BAR_STYLE = f"""
QWidget#left_tab_bar {{
    background: #f0f1f5;
    border-bottom: 1px solid rgba(0,0,0,0.05);
}}
QPushButton#left_tab_btn {{
    background: transparent;
    color: #6b7280;
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 12.5px;
    font-weight: 500;
}}
QPushButton#left_tab_btn:hover {{
    background: {_rgba(accent, 0.08)};
    color: {accent};
}}
QPushButton#left_tab_btn:checked {{
    background: {_rgba(accent, 0.12)};
    color: {p_start};
    font-weight: 600;
}}
"""
        SESSION_ITEM_ENHANCED_STYLE = f"""
QWidget#session_item {{
    background: transparent;
    border-radius: 8px;
    padding: 2px;
}}
QWidget#session_item:hover {{
    background: {_rgba(p_start, 0.04)};
}}
QWidget#session_item[active="true"] {{
    background: {_rgba(p_start, 0.08)};
    border-left: 3px solid {p_start};
}}
"""
        MESSAGE_INPUT_STYLE = f"""
QPlainTextEdit {{
    background: #ffffff;
    color: #1a1a2e;
    border: 1.5px solid rgba(0, 0, 0, 0.08);
    border-radius: 14px;
    padding: 10px 16px;
    font-size: 14px;
    selection-background-color: {_rgba(accent, 0.3)};
}}
QPlainTextEdit:focus {{
    border-color: {p_start};
    border-width: 2px;
}}
QPlainTextEdit:hover {{
    border-color: {_rgba(p_start, 0.3)};
}}
"""
        FILE_TREE_ENHANCED_STYLE = f"""
QTreeView {{
    background: transparent;
    color: #1d1d1f;
    border: none;
    font-size: 12.5px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item {{
    padding: 5px 4px;
    border-radius: 5px;
    color: #1d1d1f;
}}
QTreeView::item:selected {{
    background: {_rgba(accent, 0.12)};
    color: {p_start};
}}
QTreeView::item:hover {{
    background: {_rgba(accent, 0.06)};
    color: #1d1d1f;
}}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    border-image: none;
}}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    border-image: none;
}}
"""
        SEARCH_BAR_STYLE = f"""
QLineEdit {{
    background: rgba(0,0,0,0.04);
    color: #1a1a2e;
    border: 1.5px solid rgba(0,0,0,0.06);
    border-radius: 10px;
    padding: 7px 12px 7px 30px;
    font-size: 12px;
}}
QLineEdit:focus {{
    border-color: {accent};
    background: {_rgba(accent, 0.04)};
}}
QLineEdit::placeholder {{
    color: #a1a1aa;
}}
"""
        BREADCRUMB_STYLE = f"""
QWidget#breadcrumb_bar {{
    background: rgba(0,0,0,0.02);
    border-bottom: 1px solid rgba(0,0,0,0.04);
    padding: 2px 4px;
}}
QPushButton#breadcrumb_btn {{
    background: transparent;
    color: {accent};
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11.5px;
    font-weight: 500;
}}
QPushButton#breadcrumb_btn:hover {{
    background: {_rgba(accent, 0.08)};
    color: {p_start};
}}
QLabel#breadcrumb_sep {{
    color: #d1d5db;
    font-size: 13px;
    padding: 0 2px;
}}
"""
        CONTEXT_MENU_STYLE = f"""
QMenu {{
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    min-width: 180px;
}}
QMenu::item {{
    padding: 8px 32px 8px 16px;
    border-radius: 6px;
    margin: 1px 3px;
}}
QMenu::item:selected {{
    background: {_rgba(accent, 0.10)};
    color: {p_start};
}}
QMenu::item:disabled {{
    color: #9ca3af;
    background: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: rgba(0,0,0,0.06);
    margin: 5px 10px;
}}
"""
        COLLAPSE_BTN_STYLE = f"""
QPushButton {{
    color: #6b7280;
    background: transparent;
    border: none;
    font-size: 11px;
}}
QPushButton:hover {{
    color: {p_start};
}}
"""
        EXPAND_BTN_STYLE = f"""
QPushButton {{
    background: #ffffff;
    color: {p_start};
    border-radius: 10px;
    border: 1px solid rgba(0, 0, 0, 0.06);
    font-weight: bold;
}}
QPushButton:hover {{
    background: {_rgba(p_start, 0.05)};
    border-color: {p_start};
}}
QPushButton:pressed {{
    background: {_rgba(p_start, 0.10)};
}}
"""


# ---- 主窗口 ----
MAIN_WINDOW_STYLE = """
QMainWindow {
    background: #f4f5f7;
}
QWidget {
    font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13.5px;
}
QToolTip {
    background: #1a1a2e;
    color: #f4f5f7;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11.5px;
}
"""

# ---- 顶部导航栏 ----
TOP_NAV_STYLE = """
background: #f4f5f7;
border-bottom: 1px solid rgba(0,0,0,0.06);
"""

# 亮色主题下顶部栏按钮（浅底深字，适配浅色导航栏背景）
TOP_NAV_BTN_STYLE = """
QPushButton {
    background: rgba(0, 0, 0, 0.04);
    color: #1d1d1f;
    border-radius: 8px;
    border: 1px solid rgba(0, 0, 0, 0.06);
    font-size: 12px;
    padding: 4px 10px;
}
QPushButton:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.12);
}
QPushButton:pressed {
    background: rgba(0, 0, 0, 0.10);
}
"""

BRAND_STYLE = """
color: #1d1d1f;
font-size: 19px;
font-weight: bold;
background: transparent;
letter-spacing: 2px;
"""

SETTINGS_BTN_STYLE = """
QPushButton {
    background: rgba(255, 255, 255, 0.15);
    color: white;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    font-size: 13px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 0.25);
    border-color: rgba(255, 255, 255, 0.35);
}
QPushButton:pressed {
    background: rgba(255, 255, 255, 0.1);
}
"""

# ---- 左侧面板 ----
LEFT_PANEL_STYLE = """
background: #fafbfc;
border-right: 1px solid rgba(0, 0, 0, 0.06);
"""

SESSION_LIST_STYLE = """
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    background: transparent;
    border: none;
    padding: 3px 6px;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: transparent;
    border-radius: 8px;
}
QListWidget::item:hover {
    background: transparent;
    border-radius: 8px;
}
"""

# ---- 文件树样式 ----
FILE_TREE_STYLE = """
QTreeView {
    background: transparent;
    color: #000000;
    border: none;
    font-size: 12px;
    outline: none;
}
QTreeView::item {
    padding: 4px 0;
    border-radius: 4px;
    color: #000000;
}
QTreeView::item:selected {
    background: rgba(99, 102, 241, 0.15);
    color: #000000;
}
QTreeView::item:hover {
    background: rgba(99, 102, 241, 0.08);
    color: #000000;
}
QTreeView QAbstractScrollArea {
    background: transparent;
}
QTreeView QAbstractScrollArea::corner {
    background: transparent;
}
"""

NEW_SESSION_BTN_STYLE = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6366f1, stop:1 #8b5cf6);
    color: white;
    border-radius: 10px;
    font-weight: bold;
    font-size: 12.5px;
    padding: 6px 0;
    border: none;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4f46e5, stop:1 #7c3aed);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4338ca, stop:1 #6d28d9);
}
"""

SESSION_ITEM_STYLE = """
QWidget {
    background: transparent;
    border-radius: 8px;
}
QWidget:hover {
    background: rgba(79, 70, 229, 0.05);
}
"""

# ---- 中间对话区 ----
MIDDLE_STYLE = "background: #f4f5f7;"

CHAT_SCROLL_STYLE = """
QScrollArea {
    background: #f4f5f7;
    border: none;
}
"""

DARK_CHAT_SCROLL_STYLE = """
QScrollArea {
    background: #27282e;
    border: none;
}
"""

# 用户消息气泡
USER_MESSAGE_STYLE = """
background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 #4f46e5, stop:0.5 #6366f1, stop:1 #7c3aed);
border-radius: 16px;
padding: 12px 18px;
"""

# AI消息气泡（现代极简：弱边框、大圆角、柔和留白）
AI_MESSAGE_STYLE = """
background: #ffffff;
border-radius: 18px;
padding: 14px 20px;
border: 1px solid rgba(0, 0, 0, 0.04);
box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
"""

# ---- 输入区域 ----
MESSAGE_INPUT_STYLE = """
QPlainTextEdit {
    background: #ffffff;
    color: #1a1a2e;
    border: 1.5px solid rgba(0, 0, 0, 0.08);
    border-radius: 14px;
    padding: 10px 16px;
    font-size: 14px;
    selection-background-color: rgba(99, 102, 241, 0.3);
}
QPlainTextEdit:focus {
    border-color: #4f46e5;
    border-width: 2px;
}
QPlainTextEdit:hover {
    border-color: rgba(79, 70, 229, 0.3);
}
"""

VOICE_BTN_STYLE = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4f46e5, stop:1 #7c3aed);
    border-radius: 20px;
    color: white;
    font-size: 16px;
    border: none;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4338ca, stop:1 #6d28d9);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #3730a3, stop:1 #5b21b6);
}
"""

SEND_BTN_STYLE = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6366f1, stop:1 #a855f7);
    color: white;
    border: none;
    border-radius: 22px;
    font-size: 18px;
    font-weight: bold;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4f46e5, stop:1 #9333ea);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4338ca, stop:1 #7e22ce);
}
"""

# ---- 右侧控制面板 ----
RIGHT_PANEL_STYLE = """
background: #fafbfc;
border-left: 1px solid rgba(0, 0, 0, 0.06);
"""

MODEL_SECTION_STYLE = """
background: rgba(0, 0, 0, 0.02);
border-radius: 12px;
padding: 14px;
"""

PARAM_SECTION_STYLE = """
background: rgba(0, 0, 0, 0.02);
border-radius: 12px;
padding: 14px;
"""

SLIDER_STYLE = """
QSlider::groove:horizontal {
    background: rgba(0, 0, 0, 0.08);
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4f46e5;
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background: #4338ca;
}
QSlider::handle:horizontal:pressed {
    background: #3730a3;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4f46e5, stop:1 #7c3aed);
    border-radius: 3px;
}
"""

COLLAPSE_BTN_STYLE = """
QPushButton {
    color: #6b7280;
    background: transparent;
    border: none;
    font-size: 11px;
}
QPushButton:hover {
    color: #4f46e5;
}
"""

EXPAND_BTN_STYLE = """
QPushButton {
    background: #ffffff;
    color: #4f46e5;
    border-radius: 10px;
    border: 1px solid rgba(0, 0, 0, 0.06);
    font-weight: bold;
}
QPushButton:hover {
    background: rgba(79, 70, 229, 0.05);
    border-color: #4f46e5;
}
QPushButton:pressed {
    background: rgba(79, 70, 229, 0.10);
}
"""

# ---- 通用组件 ----
STOP_BTN_STYLE = """
QPushButton {
    background: #e53e3e;
    color: white;
    border-radius: 6px;
    font-size: 11.5px;
    border: none;
}
QPushButton:hover {
    background: #c53030;
}
"""

CURSOR_STYLE = """
color: #4f46e5;
font-size: 14px;
background: transparent;
"""

MODEL_CARD_STYLE = """
background: #ffffff;
border-radius: 10px;
border: 1px solid rgba(0, 0, 0, 0.06);
"""

MODEL_CARD_SELECTED_STYLE = """
background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 #4f46e5, stop:0.5 #6366f1, stop:1 #7c3aed);
border-radius: 10px;
"""

TOAST_STYLE = """
background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 #1a1a2e, stop:1 #2d2d44);
border-radius: 10px;
"""

# 模型选择下拉框样式
MODEL_COMBO_STYLE = """
QComboBox {
    background: rgba(255, 255, 255, 0.18);
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 8px;
    padding: 6px 30px 6px 14px;
    min-width: 160px;
    font-weight: bold;
    font-size: 13px;
}
QComboBox:hover {
    background: rgba(255, 255, 255, 0.25);
    border-color: rgba(255, 255, 255, 0.35);
}
QComboBox::drop-down {
    border: none;
    width: 28px;
    subcontrol-position: right center;
}
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid rgba(255, 255, 255, 0.8);
    margin-right: 10px;
}
"""


MODEL_DROPDOWN_STYLE = """
QComboBox QAbstractItemView {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #eef2ff;
    selection-color: #4f46e5;
    outline: none;
}
QComboBox QAbstractItemView::item {
    padding: 8px 12px;
    border-radius: 6px;
    min-height: 28px;
}
QComboBox QAbstractItemView::item:hover {
    background: #f3f4f6;
}
QComboBox QAbstractItemView::item:selected {
    background: #eef2ff;
    color: #4f46e5;
    font-weight: bold;
}
"""
NEW_CHAT_BTN_STYLE = """
QPushButton {
    background: rgba(255, 255, 255, 0.2);
    color: white;
    border-radius: 8px;
    font-weight: bold;
    border: 1px solid rgba(255, 255, 255, 0.2);
    font-size: 12px;
    padding: 4px 8px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 0.30);
}
QPushButton:pressed {
    background: rgba(255, 255, 255, 0.15);
}
"""

# ---- 公共滚动条样式 ----
SCROLLBAR_LIGHT = """
QScrollBar:vertical {
    background: transparent;
    width: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.12);
    min-height: 30px;
    border-radius: 2.5px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(0, 0, 0, 0.25);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
"""

SCROLLBAR_CHAT = """
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.12);
    min-height: 40px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(0, 0, 0, 0.22);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

SCROLLBAR_DARK = """
QScrollBar:vertical {
    background: transparent;
    width: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.15);
    min-height: 30px;
    border-radius: 2.5px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.25);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""



# ---- 深色主题主窗口 (DeepSeek风格) ----
DARK_MAIN_WINDOW_STYLE = """
QMainWindow {
    background: #27282e;
}
QWidget {
    font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13.5px;
    color: #d9dae0;
}
QToolTip {
    background: #2d2e34;
    color: #d9dae0;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11.5px;
}
"""

DARK_MIDDLE_STYLE = """
background: #27282e;
border: none;
"""

DARK_LEFT_PANEL_STYLE = """
background: #27282e;
border-right: 1px solid rgba(255, 255, 255, 0.06);
"""

DARK_MESSAGE_INPUT_STYLE = """
QPlainTextEdit {
    background: #2d2e34;
    color: #d9dae0;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 14px;
    selection-background-color: rgba(110, 127, 224, 0.35);
}
QPlainTextEdit:focus {
    border-color: #6e7fe0;
    background: #2d2e34;
}
"""

DARK_TOP_NAV_STYLE = """
background: #27282e;
border-bottom: 1px solid rgba(255, 255, 255, 0.06);
"""

DARK_SLIDER_STYLE = """
QSlider::groove:horizontal {
    background: rgba(255, 255, 255, 0.08);
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #6e7fe0;
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background: #7f8fe6;
}
QSlider::handle:horizontal:pressed {
    background: #5a6bcf;
}
QSlider::sub-page:horizontal {
    background: #6e7fe0;
    border-radius: 3px;
}
"""

DARK_NEW_SESSION_BTN_STYLE = """
QPushButton {
    background: #32333a;
    color: #d9dae0;
    border-radius: 10px;
    font-weight: 600;
    font-size: 12.5px;
    padding: 6px 0;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
QPushButton:hover {
    background: #3a3b43;
    border-color: rgba(110, 127, 224, 0.5);
}
QPushButton:pressed {
    background: #2a2b31;
}
"""

DARK_MODEL_CARD_SELECTED_STYLE = """
background: rgba(110, 127, 224, 0.15);
border: 1px solid #6e7fe0;
border-radius: 10px;
"""

DARK_USER_MESSAGE_STYLE = """
background: #32333a;
color: #e8e9ee;
border-radius: 16px;
border-bottom-right-radius: 4px;
padding: 12px 16px;
font-size: 14px;
line-height: 1.5;
"""

DARK_AI_MESSAGE_STYLE = """
background: #2d2e34;
color: #d9dae0;
border-radius: 18px;
border-bottom-left-radius: 4px;
border: 1px solid rgba(255, 255, 255, 0.06);
padding: 14px 20px;
font-size: 14px;
line-height: 1.5;
"""

DARK_SESSION_LIST_STYLE = """
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    background: transparent;
    border: none;
    padding: 3px 6px;
    color: #d9dae0;
    border-radius: 8px;
}
QListWidget::item:selected {
    background: rgba(110, 127, 224, 0.15);
    border-radius: 8px;
}
QListWidget::item:hover {
    background: rgba(255, 255, 255, 0.04);
    border-radius: 8px;
}
"""


# 动画效果样式
ANIMATED_INPUT_STYLE = """
QPlainTextEdit {
    background: #ffffff;
    color: #1a1a2e;
    border: 2px solid rgba(0, 0, 0, 0.06);
    border-radius: 18px;
    padding: 14px 18px;
    font-size: 14px;
    selection-background-color: rgba(79, 70, 229, 0.2);
    transition: border-color 0.2s ease;
}
QPlainTextEdit:focus {
    border-color: #4f46e5;
    background: #ffffff;
}
"""
# ==================== 左侧面板 Tab 栏 ====================
LEFT_TAB_BAR_STYLE = """
QWidget#left_tab_bar {
    background: #f0f1f5;
    border-bottom: 1px solid rgba(0,0,0,0.05);
}
QPushButton#left_tab_btn {
    background: transparent;
    color: #6b7280;
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 12.5px;
    font-weight: 500;
}
QPushButton#left_tab_btn:hover {
    background: rgba(99,102,241,0.08);
    color: #6366f1;
}
QPushButton#left_tab_btn:checked {
    background: rgba(99,102,241,0.12);
    color: #4f46e5;
    font-weight: 600;
}
"""

DARK_LEFT_TAB_BAR_STYLE = """
QWidget#left_tab_bar {
    background: #27282e;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
QPushButton#left_tab_btn {
    background: transparent;
    color: #9a9ca6;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 12.5px;
    font-weight: 500;
}
QPushButton#left_tab_btn:hover {
    background: rgba(110,127,224,0.10);
    color: #9aa6ee;
}
QPushButton#left_tab_btn:checked {
    background: rgba(110,127,224,0.15);
    color: #8b9af0;
    font-weight: 600;
}
"""

# ==================== 搜索框 ====================
SEARCH_BAR_STYLE = """
QLineEdit {
    background: rgba(0,0,0,0.04);
    color: #1a1a2e;
    border: 1.5px solid rgba(0,0,0,0.06);
    border-radius: 10px;
    padding: 7px 12px 7px 30px;
    font-size: 12px;
}
QLineEdit:focus {
    border-color: #6366f1;
    background: rgba(99,102,241,0.04);
}
QLineEdit::placeholder {
    color: #a1a1aa;
}
"""

DARK_SEARCH_BAR_STYLE = """
QLineEdit {
    background: rgba(255,255,255,0.04);
    color: #d9dae0;
    border: 1.5px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 7px 12px 7px 30px;
    font-size: 12px;
}
QLineEdit:focus {
    border-color: #6e7fe0;
    background: rgba(110,127,224,0.08);
}
QLineEdit::placeholder {
    color: #6f7178;
}
"""

# ==================== 增强会话列表项 ====================
SESSION_ITEM_ENHANCED_STYLE = """
QWidget#session_item {
    background: transparent;
    border-radius: 8px;
    padding: 2px;
}
QWidget#session_item:hover {
    background: rgba(79,70,229,0.04);
}
QWidget#session_item[active="true"] {
    background: rgba(79,70,229,0.08);
    border-left: 3px solid #4f46e5;
}
"""

DARK_SESSION_ITEM_ENHANCED_STYLE = """
QWidget#session_item {
    background: transparent;
    border-radius: 8px;
    padding: 2px;
}
QWidget#session_item:hover {
    background: rgba(110,127,224,0.06);
}
QWidget#session_item[active="true"] {
    background: rgba(110,127,224,0.12);
    border-left: 3px solid #6e7fe0;
}
"""

# ==================== 增强文件树 ====================
FILE_TREE_ENHANCED_STYLE = """
QTreeView {
    background: transparent;
    color: #1d1d1f;
    border: none;
    font-size: 12.5px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeView::item {
    padding: 5px 4px;
    border-radius: 5px;
    color: #1d1d1f;
}
QTreeView::item:selected {
    background: rgba(99,102,241,0.12);
    color: #4f46e5;
}
QTreeView::item:hover {
    background: rgba(99,102,241,0.06);
    color: #1d1d1f;
}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {
    border-image: none;
}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {
    border-image: none;
}
"""

DARK_FILE_TREE_ENHANCED_STYLE = """
QTreeView {
    background: transparent;
    color: #d9dae0;
    border: none;
    font-size: 12.5px;
    outline: none;
}
QTreeView::item {
    padding: 5px 4px;
    border-radius: 5px;
    color: #d9dae0;
}
QTreeView::item:selected {
    background: rgba(110,127,224,0.18);
    color: #8b9af0;
}
QTreeView::item:hover {
    background: rgba(110,127,224,0.08);
    color: #e8e9ee;
}
"""

# ==================== 统一右键菜单 ====================
CONTEXT_MENU_STYLE = """
QMenu {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    min-width: 180px;
}
QMenu::item {
    padding: 8px 32px 8px 16px;
    border-radius: 6px;
    margin: 1px 3px;
}
QMenu::item:selected {
    background: rgba(99,102,241,0.10);
    color: #4f46e5;
}
QMenu::item:disabled {
    color: #9ca3af;
    background: transparent;
}
QMenu::separator {
    height: 1px;
    background: rgba(0,0,0,0.06);
    margin: 5px 10px;
}
"""

DARK_CONTEXT_MENU_STYLE = """
QMenu {
    background: #2b2b2b;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
    min-width: 180px;
}
QMenu::item {
    padding: 8px 32px 8px 16px;
    border-radius: 6px;
    margin: 1px 3px;
    color: #ececec;
}
QMenu::item:selected {
    background: rgba(77,107,254,0.15);
    color: #4d6bfe;
}
QMenu::item:disabled {
    color: #666666;
    background: transparent;
}
QMenu::separator {
    height: 1px;
    background: rgba(255,255,255,0.08);
    margin: 5px 10px;
}
"""

# ==================== 面包屑导航 ====================
BREADCRUMB_STYLE = """
QWidget#breadcrumb_bar {
    background: rgba(0,0,0,0.02);
    border-bottom: 1px solid rgba(0,0,0,0.04);
    padding: 2px 4px;
}
QPushButton#breadcrumb_btn {
    background: transparent;
    color: #6366f1;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11.5px;
    font-weight: 500;
}
QPushButton#breadcrumb_btn:hover {
    background: rgba(99,102,241,0.08);
    color: #4f46e5;
}
QLabel#breadcrumb_sep {
    color: #d1d5db;
    font-size: 13px;
    padding: 0 2px;
}
"""

DARK_BREADCRUMB_STYLE = """
QWidget#breadcrumb_bar {
    background: rgba(255,255,255,0.02);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 2px 4px;
}
QPushButton#breadcrumb_btn {
    background: transparent;
    color: #4d6bfe;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11.5px;
    font-weight: 500;
}
QPushButton#breadcrumb_btn:hover {
    background: rgba(77,107,254,0.10);
    color: #5a7cff;
}
QLabel#breadcrumb_sep {
    color: #666666;
    font-size: 13px;
    padding: 0 2px;
}
"""

# ==================== 文件树工具栏按钮 ====================
FILE_TOOLBAR_BTN_STYLE = """
QPushButton {
    background: transparent;
    color: #71717a;
    border: none;
    border-radius: 5px;
    padding: 4px 6px;
    font-size: 13px;
}
QPushButton:hover {
    background: rgba(0,0,0,0.06);
    color: #4f46e5;
}
"""

DARK_FILE_TOOLBAR_BTN_STYLE = """
QPushButton {
    background: transparent;
    color: #a0a0a0;
    border: none;
    border-radius: 5px;
    padding: 4px 6px;
    font-size: 13px;
}
QPushButton:hover {
    background: rgba(255,255,255,0.08);
    color: #4d6bfe;
}
"""

# ==================== 底部按钮区域增强 ====================
BOTTOM_BAR_STYLE = """
QWidget#bottom_bar {
    background: #f8f9fb;
    border-top: 1px solid rgba(0,0,0,0.05);
}
"""

DARK_BOTTOM_BAR_STYLE = """
QWidget#bottom_bar {
    background: #1e1e1e;
    border-top: 1px solid rgba(255,255,255,0.08);
}
"""

# ==================== 标签 Badge ====================
BADGE_STYLE = """
QLabel {
    color: #71717a;
    background: rgba(0,0,0,0.06);
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 500;
}
"""

DARK_BADGE_STYLE = """
QLabel {
    color: #a0a0a0;
    background: rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 500;
}
"""


def get_style(style_name: str, theme: str = "light") -> str:
    """获取样式，支持 light/dark 主题"""
    if theme == "dark":
        dark_map = {
            'main_window': DARK_MAIN_WINDOW_STYLE,
            'top_nav': DARK_TOP_NAV_STYLE,
            'middle': DARK_MIDDLE_STYLE,
            'left_panel': DARK_LEFT_PANEL_STYLE,
            'chat_scroll': DARK_CHAT_SCROLL_STYLE,
            'message_input': DARK_MESSAGE_INPUT_STYLE,
            'user_message': DARK_USER_MESSAGE_STYLE,
            'ai_message': DARK_AI_MESSAGE_STYLE,
            'session_list': DARK_SESSION_LIST_STYLE,
            'left_tab_bar': DARK_LEFT_TAB_BAR_STYLE,
            'search_bar': DARK_SEARCH_BAR_STYLE,
            'session_item_enhanced': DARK_SESSION_ITEM_ENHANCED_STYLE,
            'file_tree_enhanced': DARK_FILE_TREE_ENHANCED_STYLE,
            'context_menu': DARK_CONTEXT_MENU_STYLE,
            'breadcrumb': DARK_BREADCRUMB_STYLE,
            'file_toolbar_btn': DARK_FILE_TOOLBAR_BTN_STYLE,
            'bottom_bar': DARK_BOTTOM_BAR_STYLE,
            'badge': DARK_BADGE_STYLE,
            # 以下样式主题无关，直接复用
            'brand': BRAND_STYLE,
            'model_combo': MODEL_COMBO_STYLE,
            'new_chat_btn': NEW_CHAT_BTN_STYLE,
            'settings_btn': SETTINGS_BTN_STYLE,
            'new_session_btn': DARK_NEW_SESSION_BTN_STYLE,
            'voice_btn': VOICE_BTN_STYLE,
            'send_btn': SEND_BTN_STYLE,
            'right_panel': DARK_LEFT_PANEL_STYLE,
            'slider': DARK_SLIDER_STYLE,
            'model_card': MODEL_CARD_STYLE,
            'model_card_selected': DARK_MODEL_CARD_SELECTED_STYLE,
            'toast': TOAST_STYLE,
            'stop_btn': STOP_BTN_STYLE,
            'cursor': CURSOR_STYLE,
            'scrollbar_light': SCROLLBAR_DARK,
            'scrollbar_chat': SCROLLBAR_DARK,
            'scrollbar_dark': SCROLLBAR_DARK,
        }
        if style_name in dark_map:
            return dark_map[style_name]
    style_map = {
        'main_window': MAIN_WINDOW_STYLE,
        'top_nav': TOP_NAV_STYLE,
        'top_nav_btn': TOP_NAV_BTN_STYLE,
        'brand': BRAND_STYLE,
        'model_combo': MODEL_COMBO_STYLE,
        'new_chat_btn': NEW_CHAT_BTN_STYLE,
        'settings_btn': SETTINGS_BTN_STYLE,
        'left_panel': LEFT_PANEL_STYLE,
        'session_list': SESSION_LIST_STYLE,
        'new_session_btn': NEW_SESSION_BTN_STYLE,
        'file_tree': FILE_TREE_STYLE,
        'middle': MIDDLE_STYLE,
        'chat_scroll': CHAT_SCROLL_STYLE,
        'message_input': MESSAGE_INPUT_STYLE,
        'voice_btn': VOICE_BTN_STYLE,
        'send_btn': SEND_BTN_STYLE,
        'right_panel': RIGHT_PANEL_STYLE,
        'model_section': MODEL_SECTION_STYLE,
        'param_section': PARAM_SECTION_STYLE,
        'slider': SLIDER_STYLE,
        'collapse_btn': COLLAPSE_BTN_STYLE,
        'expand_btn': EXPAND_BTN_STYLE,
        'user_message': USER_MESSAGE_STYLE,
        'ai_message': AI_MESSAGE_STYLE,
        'stop_btn': STOP_BTN_STYLE,
        'cursor': CURSOR_STYLE,
        'session_item': SESSION_ITEM_STYLE,
        'model_card': MODEL_CARD_STYLE,
        'model_card_selected': MODEL_CARD_SELECTED_STYLE,
        'toast': TOAST_STYLE,
        'scrollbar_light': SCROLLBAR_LIGHT,
        'scrollbar_chat': SCROLLBAR_CHAT,
        'scrollbar_dark': SCROLLBAR_DARK,
        'left_tab_bar': LEFT_TAB_BAR_STYLE,
        'search_bar': SEARCH_BAR_STYLE,
        'session_item_enhanced': SESSION_ITEM_ENHANCED_STYLE,
        'file_tree_enhanced': FILE_TREE_ENHANCED_STYLE,
        'context_menu': CONTEXT_MENU_STYLE,
        'breadcrumb': BREADCRUMB_STYLE,
        'file_toolbar_btn': FILE_TOOLBAR_BTN_STYLE,
        'bottom_bar': BOTTOM_BAR_STYLE,
        'badge': BADGE_STYLE,
    }
    style_map['model_dropdown'] = MODEL_DROPDOWN_STYLE if 'MODEL_DROPDOWN_STYLE' in dir() else ''
    style_map['animated_input'] = ANIMATED_INPUT_STYLE if 'ANIMATED_INPUT_STYLE' in dir() else ''
    return style_map.get(style_name, '')
