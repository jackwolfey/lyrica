"""pytest 公共夹具。

两个关键设计：

1. **替换音频后端**：`just_playback` 需要真实声卡，CI/容器里通常没有。
   在导入 `src.player` 之前把假的 `just_playback` 模块塞进 `sys.modules`，
   单元测试只验证播放器状态机，不依赖硬件。
2. **离屏 Qt**：`QT_QPA_PLATFORM=offscreen`，无需 X11/显示器。
"""

import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakePlayback:
    """`just_playback.Playback` 的确定性替身，记录所有调用，便于断言调用序列。"""

    instances: list["FakePlayback"] = []
    DURATION = 180.0  # 每首歌固定 3 分钟

    def __init__(self):
        self.ops: list[str] = []
        self.path = None
        self.loop_enabled = False
        self._active = False
        self._playing = False
        self._paused = False
        self._curr_pos = 0.0
        self._duration = 0.0
        self._volume = 1.0
        FakePlayback.instances.append(self)

    # ---------- 只读属性（与 just_playback 对齐） ----------
    @property
    def duration(self):
        return self._duration

    @property
    def curr_pos(self):
        return self._curr_pos

    @property
    def playing(self):
        return self._playing

    @property
    def paused(self):
        return self._paused

    @property
    def active(self):
        return self._active

    @property
    def volume(self):
        return self._volume

    # ---------- 命令 ----------
    def load_file(self, path):
        self.path = path
        self._duration = self.DURATION
        self._curr_pos = 0.0
        self._active = True
        self._playing = False
        self._paused = False
        self.ops.append("load")
        return True

    def play(self):
        self._active = True
        if self._curr_pos >= self._duration:
            self._curr_pos = 0.0
        self._playing = True
        self._paused = False
        self.ops.append("play")

    def pause(self):
        if self._playing:
            self._playing = False
            self._paused = True
        self.ops.append("pause")

    def resume(self):
        if self._paused:
            self._paused = False
            self._playing = True
        self.ops.append("resume")

    def stop(self):
        self._active = False
        self._playing = False
        self._paused = False
        self._curr_pos = 0.0
        self._duration = 0.0
        self.ops.append("stop")

    def seek(self, position):
        if self._active and self._duration > 0:
            self._curr_pos = max(0.0, min(float(position), self._duration))
            self.ops.append("seek")

    def loop_at_end(self, enabled):
        self.loop_enabled = bool(enabled)

    def set_volume(self, volume):
        self._volume = float(volume)

    # ---------- 测试辅助 ----------
    def finish(self):
        """模拟曲目自然播放结束：仍处于已加载状态，但既不 playing 也不 paused。"""
        self._playing = False
        self._paused = False
        self._curr_pos = self._duration


def _install_fake_backend() -> None:
    module = types.ModuleType("just_playback")
    module.Playback = FakePlayback
    module.__version__ = "fake"
    sys.modules["just_playback"] = module


_install_fake_backend()  # 必须早于任何 `import src.player`


AUDIO_NAMES = ["a.mp3", "b.flac", "c.ogg", "d.wav", "e.m4a", "f.aac", "g.wma", "h.opus"]


def write_files(folder: Path, names) -> list:
    """在 folder 下创建同名空文件，返回绝对路径字符串列表。"""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(b"")
        paths.append(str(path))
    return paths


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _reset_fake_backend():
    FakePlayback.instances.clear()
    yield


@pytest.fixture(autouse=True)
def _no_system_tray(qapp, monkeypatch):
    """离屏环境没有系统托盘；显式固定，保证 `_init_tray` 走空分支。"""
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))


@pytest.fixture
def make_tree(tmp_path):
    """按 {相对路径: [文件名]} 建目录树，返回音乐根目录。"""

    def _make(spec: dict):
        base = tmp_path / "music"
        for rel, names in spec.items():
            write_files(base / rel, names)
        return base

    return _make


@pytest.fixture
def player(qapp):
    from src.player import MusicPlayer

    instance = MusicPlayer()
    yield instance
    instance._timer.stop()


@pytest.fixture
def three_tracks(tmp_path):
    folder = tmp_path / "album"
    paths = write_files(folder, ["a.mp3", "b.mp3", "c.mp3"])
    return paths


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """把配置写到 tmp_path，避免污染项目目录。"""
    path = tmp_path / "lyrica_settings.json"
    monkeypatch.setattr("src.main_window.settings_path", lambda: str(path))
    return path


@pytest.fixture
def window(qapp, settings_file):
    from src.main_window import MainWindow

    instance = MainWindow()
    yield instance
    instance.player._timer.stop()
    instance._force_quit = True
    instance.close()
    instance.deleteLater()


@pytest.fixture
def window_factory(qapp, settings_file):
    """在已写好配置后再构造窗口（用于测试「启动恢复」）。"""
    from src.main_window import MainWindow

    created = []

    def _make():
        instance = MainWindow()
        created.append(instance)
        return instance

    yield _make

    for instance in created:
        instance.player._timer.stop()
        instance._force_quit = True
        instance.close()
        instance.deleteLater()
