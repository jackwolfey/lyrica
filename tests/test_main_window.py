"""主窗口与持久化测试（UI 逻辑、配置保存/恢复、托盘图标）。"""

import json
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QSystemTrayIcon

import src.main_window as mw
from src.main_window import APP_NAME, APP_VERSION, AboutDialog, MainWindow, format_time
from src.player import PlayMode

PROJECT_ROOT = Path(mw.__file__).resolve().parents[1]


def make_files(folder: Path, names):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(b"")
        paths.append(str(path))
    return paths


def write_json(path: Path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


class MenuRecorder:
    """替代 QMenu，记录构造出的 QAction 而不真正弹出。"""

    def __init__(self, *_args, **_kwargs):
        self.action_list = []
        self.exec_called = 0

    def addAction(self, action):
        self.action_list.append(action)

    def addSeparator(self):
        self.action_list.append(None)

    def addMenu(self, title):
        submenu = MenuRecorder()
        self.action_list.append((title, submenu))
        return submenu

    def exec(self, *_args):
        self.exec_called += 1


def install_menu_recorder(monkeypatch):
    menus = []

    def factory(*args, **kwargs):
        menu = MenuRecorder()
        menus.append(menu)
        return menu

    monkeypatch.setattr(mw, "QMenu", factory)
    return menus


def find_action(menu, text):
    for item in menu.action_list:
        if isinstance(item, QAction) and item.text() == text:
            return item
    raise AssertionError(f"菜单中没有找到 {text!r}")


class SignalStub:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class FakeTrayIcon:
    """QSystemTrayIcon 替身（离屏环境默认没有托盘）。"""

    Information = QSystemTrayIcon.Information

    def __init__(self, parent=None):
        self.parent = parent
        self.icon = None
        self.tooltip = ""
        self.menu = None
        self.visible = False
        self.messages = []
        self.activated = SignalStub()

    @staticmethod
    def isSystemTrayAvailable():
        return True

    def setIcon(self, icon):
        self.icon = icon

    def setToolTip(self, text):
        self.tooltip = text

    def setContextMenu(self, menu):
        self.menu = menu

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def isVisible(self):
        return self.visible

    def showMessage(self, *args):
        self.messages.append(args)


class SyncScanWorker:
    """同步版 ScanWorker：start() 内直接回填结果。"""

    AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus")

    def __init__(self, folder, existing_meta_cache=None, parent=None):
        self._folder = folder
        self.finished_ok = SignalStub()
        self.failed = SignalStub()

    def start(self):
        count = 0
        for _root, _dirs, files in os.walk(self._folder):
            count += sum(1 for name in files if name.lower().endswith(self.AUDIO_EXTS))
        self.finished_ok.emit(self._folder, count, {})

    def deleteLater(self):
        pass


class FailingScanWorker(SyncScanWorker):
    def start(self):
        self.failed.emit("boom")


class ImmediatelyAcceptedDialog(mw.QDialog):
    def exec(self):
        self.accept()
        return self.result()


# ==================== 纯函数 ====================


@pytest.mark.parametrize("raw,expected", [
    (0, "00:00"),
    (59, "00:59"),
    (59.9, "00:59"),
    (60, "01:00"),
    (3599, "59:59"),
    (3600, "60:00"),
    (-5, "00:00"),
    (float("nan"), "00:00"),
])
def test_format_time(raw, expected):
    assert format_time(raw) == expected


def test_settings_path_in_source_mode():
    path = Path(mw.settings_path())

    assert path.name == mw.SETTINGS_FILENAME
    assert path.parent == PROJECT_ROOT


def test_settings_path_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/app/Lyrica")

    expected = os.path.join("/opt/app", "lyrica_settings.json")
    assert mw.settings_path() == expected


# ==================== 初始状态 ====================


def test_initial_state(window):
    assert window.lbl_title.text() == "未加载曲目"
    assert window.lbl_meta.text() == ""
    assert window._tray is None  # 离屏环境无托盘
    assert window.player.get_group_count() == 0
    assert window.slider_volume.value() == 70
    assert window._minimize_to_tray is False


def test_tray_icon_is_generated_programmatically(window):
    icon = window._make_tray_icon()

    assert not icon.isNull()
    assert icon.pixmap(32, 32).width() == 32


# ==================== 列表刷新 ====================


def test_refresh_groups_and_tracks(window, tmp_path):
    paths = make_files(tmp_path / "album", ["a.mp3", "b.mp3"])
    window.player.add_files(paths)
    window._refresh_groups_list()

    assert window.groups_list.count() == 1
    assert window.groups_list.item(0).text() == "album (2)"
    assert window.tracks_list.count() == 2
    assert window.lbl_tracks_header.text() == "🎵 2 首"


def test_on_group_selected_switches_tracks(window, tmp_path):
    base = tmp_path / "music"
    make_files(base / "A", ["a.mp3"])
    make_files(base / "B", ["b1.mp3", "b2.mp3"])
    window.player.add_folder_recursive(str(base))
    window._refresh_groups_list()

    window._on_group_selected(1)

    assert window.player.get_active_group_index() == 1
    assert window.tracks_list.count() == 2


def test_on_active_group_external_syncs_list(window, tmp_path):
    base = tmp_path / "music"
    make_files(base / "A", ["a.mp3"])
    make_files(base / "B", ["b.mp3"])
    window.player.add_folder_recursive(str(base))
    window._refresh_groups_list()

    window._on_active_group_external(1)

    assert window.groups_list.currentRow() == 1


def test_on_track_changed_updates_labels_and_highlight(window, tmp_path):
    paths = make_files(tmp_path / "album", ["a.mp3", "b.mp3"])
    window.player.add_files(paths)
    window.player.preload_metadata({
        paths[0]: {"title": "Song A", "artist": "Alice", "album": "Demo", "duration": "180.0"},
    })

    window.player.track_changed.emit(0)

    assert "a.mp3" in window.lbl_title.text()
    assert window.lbl_title.toolTip() == paths[0]
    assert window.lbl_meta.text() == "Song A<br>Alice · Demo"
    assert window.groups_list.currentRow() == 0
    assert window.tracks_list.currentRow() == 0


def test_on_track_changed_with_invalid_index_resets_labels(window):
    window.lbl_title.setText("something")
    window.lbl_meta.setText("something")

    window.player.track_changed.emit(-1)

    assert window.lbl_title.text() == "未加载曲目"
    assert window.lbl_meta.text() == ""


# ==================== 播放信号槽 ====================


def test_on_state_changed_switches_button_text(window):
    window._on_state_changed(True)
    assert window.btn_play.text() == "⏸"

    window._on_state_changed(False)
    assert window.btn_play.text() == "▶\uFE0F"


def test_on_duration_changed(window):
    window._on_duration_changed(180.0)
    assert window.lbl_total.text() == "03:00"


def test_on_position_changed_updates_slider(window, tmp_path):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3"]))
    window.player.play()

    window._on_position_changed(90.0)

    assert window.lbl_current.text() == "01:30"
    assert window.slider_progress.value() == 500


def test_on_position_changed_while_seeking_only_updates_label(window):
    window._seeking = True
    window.slider_progress.setValue(0)

    window._on_position_changed(90.0)

    assert window.lbl_current.text() == "01:30"
    assert window.slider_progress.value() == 0


def test_seek_end_seeks_player(window, tmp_path):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3"]))
    window.player.play()
    window.slider_progress.setValue(500)

    window._on_seek_start()
    assert window._seeking is True
    window._on_seek_end()

    assert window._seeking is False
    assert window.player._playback.curr_pos == 90.0


# ==================== 控件交互 ====================


def test_volume_slider_sets_player_volume(window):
    window.slider_volume.setValue(30)

    assert window.player.get_volume() == pytest.approx(0.3)


def test_external_volume_syncs_slider(window):
    window.player.set_volume(0.9)

    assert window.slider_volume.value() == 90
    assert window.player.get_volume() == pytest.approx(0.9)


def test_mode_combo_sets_player_mode(window):
    index = window.cmb_mode.findText(PlayMode.SINGLE_LOOP.value)
    window.cmb_mode.setCurrentIndex(index)

    assert window.player.get_play_mode() is PlayMode.SINGLE_LOOP


def test_external_mode_syncs_combo(window):
    window.player.set_play_mode(PlayMode.LIST_LOOP)

    assert window.cmb_mode.currentData() is PlayMode.LIST_LOOP


def test_play_selected_track(window, tmp_path):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3", "b.mp3", "c.mp3"]))
    window._refresh_tracks_list()
    window.tracks_list.setCurrentRow(1)

    window._play_selected_track()

    assert window.player.get_current_index() == 1
    assert window.player._playback.playing


def test_track_double_click_plays(window, tmp_path):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3", "b.mp3", "c.mp3"]))
    window._refresh_tracks_list()

    window._on_track_double_clicked(window.tracks_list.item(2))

    assert window.player.get_current_index() == 2


def test_add_files_dialog_adds_group(window, tmp_path, monkeypatch):
    path = make_files(tmp_path / "album", ["a.mp3"])[0]

    class FakeDialog:
        @staticmethod
        def getOpenFileNames(*_args, **_kwargs):
            return [path], ""

    monkeypatch.setattr(mw, "QFileDialog", FakeDialog)

    window._on_add_files()

    assert window.player.get_group_count() == 1


def test_clear_all_groups_confirmed(window, tmp_path, monkeypatch):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3"]))

    class FakeMessageBox:
        Yes = 1
        No = 0

        @staticmethod
        def question(*_args, **_kwargs):
            return FakeMessageBox.Yes

    monkeypatch.setattr(mw, "QMessageBox", FakeMessageBox)

    window._on_clear_all_groups()

    assert window.player.get_group_count() == 0


def test_error_slot_shows_warning(window, monkeypatch):
    calls = []

    class FakeMessageBox:
        @staticmethod
        def warning(_parent, _title, message):
            calls.append(message)

    monkeypatch.setattr(mw, "QMessageBox", FakeMessageBox)

    window._on_error("boom")

    assert calls == ["boom"]


def test_about_dialog_contents(window):
    dialog = AboutDialog(window)

    assert dialog.windowTitle() == f"关于 {APP_NAME}"
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(APP_VERSION in text for text in texts)
    dialog.deleteLater()


# ==================== 持久化 ====================


def test_save_settings_structure(window, settings_file, tmp_path):
    folder = tmp_path / "album"
    window.player.add_files(make_files(folder, ["a.mp3"]))
    window.player.set_volume(0.4)
    window.player.set_play_mode(PlayMode.LIST_LOOP)

    window._save_settings()

    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert data["version"] == APP_VERSION
    assert data["groups"] == [{"folder_path": str(folder)}]
    assert data["volume"] == pytest.approx(0.4)
    assert data["play_mode"] == "LIST_LOOP"
    assert isinstance(data["window_size"]["w"], int)
    assert isinstance(data["window_size"]["h"], int)
    assert isinstance(data["meta_cache"], dict)
    assert data["minimize_to_tray"] is False
    assert "current_index" in data
    assert "active_group_index" in data


def test_load_applies_saved_window_size(settings_file, window_factory):
    write_json(settings_file, {"window_size": {"w": 900, "h": 600}})

    window = window_factory()

    assert (window.width(), window.height()) == (900, 600)


def test_load_ignores_tiny_window_size(settings_file, window_factory):
    write_json(settings_file, {"window_size": {"w": 100, "h": 100}})

    window = window_factory()

    assert (window.width(), window.height()) == (800, 500)


def test_load_survives_broken_json(settings_file, window_factory):
    settings_file.write_text("{ not json", encoding="utf-8")

    window = window_factory()

    assert window.player.get_group_count() == 0


def test_load_minimize_flag(settings_file, window_factory):
    write_json(settings_file, {"minimize_to_tray": True})

    window = window_factory()

    assert window._minimize_to_tray is True


def test_load_legacy_playlist_key(settings_file, window_factory, tmp_path):
    track = make_files(tmp_path / "album", ["a.mp3"])[0]
    write_json(settings_file, {"playlist": [track]})

    window = window_factory()

    assert window.player.get_group_count() == 1
    assert window.player.get_playlist() == [track]


def test_save_then_load_roundtrip(settings_file, window_factory, tmp_path):
    base = tmp_path / "music"
    make_files(base / "A", ["a.mp3"])
    make_files(base / "B", ["b1.mp3", "b2.mp3"])

    first = window_factory()
    first.player.add_folder_recursive(str(base))
    first.player.set_volume(0.4)
    first.player.set_play_mode(PlayMode.SINGLE_LOOP)
    first.player.play_track_in_group(1, 1)
    first.player._playback._curr_pos = 33.0
    playing_path = first.player.get_playlist()[-1]
    first._save_settings()

    second = window_factory()
    second_fake = second.player._playback

    assert second.player.get_group_count() == 2
    assert second.player.get_volume() == pytest.approx(0.4)
    assert second.player.get_play_mode() is PlayMode.SINGLE_LOOP
    assert second.player.get_active_group_index() == 1
    assert second.player.get_playlist()[second.player.get_current_index()] == playing_path
    assert second_fake.curr_pos == pytest.approx(33.0)
    assert second_fake.paused


def test_force_quit_bypasses_tray_and_saves(window, settings_file, tmp_path):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3"]))
    window.show()

    window._force_quit_app()

    assert window.isVisible() is False
    assert settings_file.exists()


def test_close_event_saves_settings(window, settings_file):
    window.close()

    assert settings_file.exists()


# ==================== 右键菜单 ====================


def test_title_menu_entries_and_tray_toggle(window, monkeypatch):
    window._minimize_to_tray = True
    menus = install_menu_recorder(monkeypatch)

    window._show_title_menu(QPoint(5, 5))

    menu = menus[0]
    texts = [item.text() for item in menu.action_list if isinstance(item, QAction)]
    assert "启用最小化到托盘" in texts
    assert f"关于 {APP_NAME}..." in texts
    assert "退出" in texts

    toggle = find_action(menu, "启用最小化到托盘")
    toggle.setChecked(False)
    assert window._minimize_to_tray is False


def test_groups_menu_move_up(window, tmp_path, monkeypatch):
    base = tmp_path / "music"
    make_files(base / "A", ["a.mp3"])
    make_files(base / "B", ["b.mp3"])
    window.player.add_folder_recursive(str(base))
    window._refresh_groups_list()
    window.groups_list.setCurrentRow(1)
    menus = install_menu_recorder(monkeypatch)

    window._show_groups_menu(QPoint(0, 0))

    menu = menus[0]
    up = find_action(menu, "⬆ 将该组上移")
    down = find_action(menu, "⬇ 将该组下移")
    assert up.isEnabled() is True
    assert down.isEnabled() is False  # 已是最后一组

    up.trigger()

    names = [window.player._groups[i].name for i in range(2)]
    assert names == ["B", "A"]


def test_groups_menu_disabled_without_selection(window, monkeypatch):
    menus = install_menu_recorder(monkeypatch)

    window._show_groups_menu(QPoint(0, 0))

    menu = menus[0]
    assert find_action(menu, "✕ 移除该播放组").isEnabled() is False
    assert find_action(menu, "🗑 清空所有播放组").isEnabled() is False


def test_tracks_menu_sorts_by_filename(window, tmp_path, monkeypatch):
    paths = make_files(tmp_path / "album", ["b.mp3", "a.mp3"])
    window.player.add_files(paths)
    window.player._groups[0].tracks = [paths[0], paths[1]]  # 手动打乱：b, a
    window.player._rebuild_flat_playlist()
    window._refresh_tracks_list()
    menus = install_menu_recorder(monkeypatch)

    window._show_tracks_menu(QPoint(0, 0))

    menu = menus[0]
    sort_menu = next(item[1] for item in menu.action_list
                     if isinstance(item, tuple) and item[0] == "🔀 排序方式")
    find_action(sort_menu, "按文件名").trigger()

    assert [Path(p).name for p in window.player.get_playlist()] == ["a.mp3", "b.mp3"]
    assert find_action(menu, "▶\uFE0F 播放").isEnabled() is False  # 未选中曲目


def test_tracks_menu_play_selected(window, tmp_path, monkeypatch):
    window.player.add_files(make_files(tmp_path / "album", ["a.mp3", "b.mp3"]))
    window._refresh_tracks_list()
    window.tracks_list.setCurrentRow(1)
    menus = install_menu_recorder(monkeypatch)

    window._show_tracks_menu(QPoint(0, 0))

    play = find_action(menus[0], "▶\uFE0F 播放")
    assert play.isEnabled() is True
    play.trigger()

    assert window.player.get_current_index() == 1

# ==================== 视图跳转回归（v1.0.4） ====================


def test_move_group_up_does_not_jump_view(window, make_tree):
    """组上移后视图应停在操作组，而不是跳到正在播放的组。"""
    base = make_tree({"A": ["a.mp3"], "B": ["b.mp3"], "C": ["c.mp3"]})
    window.player.add_folder_recursive(str(base))
    window._refresh_groups_list()
    # 播放 C 的曲目：current_index 落在 C，但用户接下来要操作的是 B
    window.player.play_track_in_group(2, 0)
    # 用户选中 B 组（准备上移它）
    window.groups_list.setCurrentRow(1)
    assert window.player.get_active_group_index() == 1

    window.player.move_group_up(1)

    # B 已移到 index 0，active 跟随 B
    assert window.player.get_active_group_index() == 0
    # 视图应停在 B（index 0），而非播放中的 C（index 2）
    assert window.groups_list.currentRow() == 0


def test_sort_tracks_does_not_jump_view(window, make_tree):
    """曲目排序后视图应停在当前组，而不是跳到正在播放的组。"""
    base = make_tree({"A": ["b.mp3", "a.mp3"], "B": ["b.mp3"]})
    window.player.add_folder_recursive(str(base))
    # 手动打乱 A 的曲目顺序：b, a（_scan_folder_flat 默认按文件名排序）
    a_tracks = list(window.player._groups[0].tracks)
    window.player._groups[0].tracks = [a_tracks[1], a_tracks[0]]
    window.player._rebuild_flat_playlist()
    window._refresh_groups_list()
    # 播放 B 的曲目：current_index 落在 B
    window.player.play_track_in_group(1, 0)
    # 用户选中 A 组（准备排序它）
    window.groups_list.setCurrentRow(0)
    assert window.player.get_active_group_index() == 0

    window.player.sort_group_tracks(0, "filename")

    # 排序已生效
    assert [os.path.basename(p) for p in window.player.get_group_tracks(0)] == ["a.mp3", "b.mp3"]
    # 视图应停在 A（index 0），而非播放中的 B（index 1）
    assert window.groups_list.currentRow() == 0


def test_refresh_tracks_list_no_highlight_when_not_playing(window, make_tree):
    """仅添加文件、从未播放时，刷新列表不应高亮任何行。"""
    base = make_tree({"A": ["a.mp3", "b.mp3"]})
    window.player.add_folder_recursive(str(base))

    window._refresh_tracks_list()

    # add_folder_recursive 会把 _current_index 初始化为 0，
    # 但没有任何曲目被加载/播放，不应据此高亮第 0 行
    assert window.tracks_list.currentRow() == -1

# ==================== 托盘行为 ====================


def test_tray_show_brings_window_back(window):
    window.hide()

    window._tray_show()

    assert window.isVisible()


def test_tray_show_restores_minimized_window(window, monkeypatch):
    window.show()
    window.hide()
    monkeypatch.setattr(window, "isMinimized", lambda: True)

    window._tray_show()

    assert window.isVisible()


def test_tray_activation_trigger_shows_window(window):
    window.hide()

    window._on_tray_activated(QSystemTrayIcon.Trigger)

    assert window.isVisible()


def test_tray_quit_saves_and_quits(window, settings_file, monkeypatch):
    quits = []
    monkeypatch.setattr(mw.QApplication, "quit", staticmethod(lambda: quits.append(True)))

    window._tray_quit()

    assert quits == [True]
    assert settings_file.exists()


def test_init_tray_builds_menu_and_icon(window, monkeypatch):
    monkeypatch.setattr(mw, "QSystemTrayIcon", FakeTrayIcon)

    window._init_tray()

    tray = window._tray
    assert isinstance(tray, FakeTrayIcon)
    assert tray.visible is True
    assert tray.icon is not None and not tray.icon.isNull()
    assert tray.tooltip == APP_NAME
    assert tray.menu is not None
    assert len(tray.menu.actions()) >= 5
    assert tray.activated.slots  # 左键点击已连接


def test_tray_activation_other_reason_does_not_show(window):
    window.hide()

    window._on_tray_activated(QSystemTrayIcon.Context)

    assert window.isVisible() is False


def test_change_event_minimizes_to_tray(window, monkeypatch):
    tray = FakeTrayIcon()
    tray.visible = True
    window._tray = tray
    window._minimize_to_tray = True
    window.show()
    monkeypatch.setattr(window, "isMinimized", lambda: True)

    window.changeEvent(QEvent(QEvent.WindowStateChange))

    assert window.isVisible() is False
    assert tray.messages


def test_close_event_hides_to_tray_when_enabled(window):
    tray = FakeTrayIcon()
    tray.visible = True
    window._tray = tray
    window._minimize_to_tray = True
    window.show()

    window.close()

    assert window.isVisible() is False
    assert tray.messages


# ==================== 添加文件夹流程 ====================


def test_add_folder_scans_then_adds_group(window, tmp_path, monkeypatch):
    base = tmp_path / "music"
    make_files(base / "Album", ["a.mp3"])
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: str(base)))
    monkeypatch.setattr(mw, "ScanWorker", SyncScanWorker)
    monkeypatch.setattr(mw, "QDialog", ImmediatelyAcceptedDialog)

    window._on_add_folder()

    assert window.player.get_group_count() == 1
    assert window.player.get_track_count() == 1


def test_add_folder_scan_failure_shows_warning(window, tmp_path, monkeypatch):
    warnings = []

    class FakeMessageBox:
        @staticmethod
        def warning(_parent, _title, message):
            warnings.append(message)

    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: str(tmp_path)))
    monkeypatch.setattr(mw, "ScanWorker", FailingScanWorker)
    monkeypatch.setattr(mw, "QDialog", ImmediatelyAcceptedDialog)
    monkeypatch.setattr(mw, "QMessageBox", FakeMessageBox)

    window._on_add_folder()

    assert warnings == ["扫描失败：boom"]
    assert window.player.get_group_count() == 0


def test_add_folder_cancelled_by_user(window, monkeypatch):
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: ""))

    window._on_add_folder()

    assert window.player.get_group_count() == 0


def test_add_folder_without_audio_shows_message(window, tmp_path, monkeypatch):
    infos = []

    class FakeMessageBox:
        @staticmethod
        def information(_parent, _title, message):
            infos.append(message)

    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: str(tmp_path)))
    monkeypatch.setattr(mw, "ScanWorker", SyncScanWorker)
    monkeypatch.setattr(mw, "QDialog", ImmediatelyAcceptedDialog)
    monkeypatch.setattr(mw, "QMessageBox", FakeMessageBox)

    window._on_add_folder()

    assert window.player.get_group_count() == 0
    assert infos and "未找到包含音频文件" in infos[0]


# ==================== 异常写盘 / 坏配置 ====================


def test_save_settings_swallows_oserror(window, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("src.main_window.settings_path",
                        lambda: str(blocker / "sub" / "lyrica_settings.json"))

    window._save_settings()  # 不应抛异常


def test_load_invalid_play_mode_keeps_default(settings_file, window_factory):
    write_json(settings_file, {"play_mode": "NOT_A_MODE"})

    window = window_factory()

    assert window.player.get_play_mode() is PlayMode.SEQUENCE
