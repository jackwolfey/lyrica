#!/usr/bin/env python3
"""
Lyrica v1.0.3 — 轻量级本地音乐播放器
基于 Python + just_playback + PySide6
"""

import sys

from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow


# 简洁现代的 Catppuccin Mocha 暗色 QSS
STYLESHEET = """
QMainWindow {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QLabel {
    color: #cdd6f4;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 2px 8px;
    font-weight: 500;
    text-align: center;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton:disabled {
    color: #6c7086;
    background-color: #2a2b3c;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #313244;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #89b4fa;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #f5c2e7;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

/* === QComboBox：完全隐藏下拉箭头，收窄 === */
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 0 6px;
}
QComboBox:hover {
    border-color: #585b70;
}
QComboBox::drop-down {
    width: 0;
    border: none;
    background: none;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #45475a;
    outline: 0;
}

QListWidget {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 2px;
    font-size: 12px;
    outline: 0;
}
QListWidget::item {
    padding: 4px 8px;
    border-radius: 3px;
}
QListWidget::item:selected {
    background-color: #45475a;
    color: #f5c2e7;
}
QListWidget::item:hover {
    background-color: #313244;
}

/* 右键菜单 */
QMenu {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 0;
}
QMenu::item {
    padding: 5px 20px;
    border-radius: 2px;
}
QMenu::item:selected {
    background-color: #45475a;
    color: #f5c2e7;
}
QMenu::separator {
    height: 1px;
    background: #45475a;
    margin: 4px 8px;
}

/* 对话框 */
QDialog {
    background-color: #1e1e2e;
    color: #cdd6f4;
}

/* Splitter 分隔线 */
QSplitter::handle {
    background-color: #313244;
}
QSplitter::handle:hover {
    background-color: #45475a;
}
QSplitter::handle:horizontal {
    width: 2px;
}

/* 播放组 / 曲目面板 */
QFrame#leftPanel, QFrame#rightPanel {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 4px;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Lyrica")
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
