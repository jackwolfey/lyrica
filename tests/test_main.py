"""程序入口与样式表测试。"""

import pytest

import main as main_module


def test_stylesheet_is_dark_catppuccin():
    qss = main_module.STYLESHEET

    assert "#1e1e2e" in qss  # 背景色
    assert "QMainWindow" in qss
    assert "QSlider::handle:horizontal" in qss


def test_main_boots_application(monkeypatch):
    created = {}

    class FakeApp:
        def __init__(self, argv):
            created["argv"] = argv

        def setApplicationName(self, name):
            created["app_name"] = name

        def setStyleSheet(self, qss):
            created["qss"] = qss

        def exec(self):
            return 0

    class FakeWindow:
        def __init__(self):
            created["window"] = True

        def show(self):
            created["shown"] = True

    monkeypatch.setattr(main_module, "QApplication", FakeApp)
    monkeypatch.setattr(main_module, "MainWindow", FakeWindow)

    with pytest.raises(SystemExit) as exit_info:
        main_module.main()

    assert exit_info.value.code == 0
    assert created["app_name"] == "Lyrica"
    assert created["window"] is True
    assert created["shown"] is True
    assert created["qss"] == main_module.STYLESHEET
