"""
Lyrica v1.0 - 主窗口 UI
左侧：播放组 | 右侧：当前组曲目
新增：系统托盘、元数据展示、排序菜单
"""

import json
import os
import sys
from typing import Any, Dict, Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Slot, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QPen, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSlider, QSplitter, QSystemTrayIcon,
    QVBoxLayout, QWidget, QGridLayout, QStyledItemDelegate
)

from .player import PlayMode, MusicPlayer


# v1.1: 自定义 delegate —— 给"正在播放"的 item 画一个空心圆角矩形
class PlayingItemDelegate(QStyledItemDelegate):
    """在 QListWidget 正常选中/未选中之外，再叠加一个"正在播放"的视觉标记：
    一个半透明主题色的空心圆角矩形。通过 playing_index_getter callable
    实时获取当前"正在播放"的 row index，解耦状态管理。"""

    def __init__(self, playing_index_getter=None, parent=None):
        super().__init__(parent)
        self._get_playing = playing_index_getter

    def paint(self, painter, option, index):
        # 1. 先画 Qt 默认的选中/未选中
        super().paint(painter, option, index)
        # 2. 如果这行就是"正在播放"的 item，叠一个空心圆角矩形
        playing_row = self._get_playing() if self._get_playing else None
        if playing_row is not None and index.row() == playing_row:
            rect = option.rect.adjusted(3, 2, -3, -2)
            pen = QPen(QColor("#89b4fa"))  # Catppuccin blue
            pen.setWidth(2)
            pen.setCosmetic(True)          # 缩放时线宽不变
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)


# v1.1: 单行超长横向滚动（marquee）标签
class _MarqueeLabel(QLabel):
    """默认居中显示；文本宽度超过 label 宽度时，自动从右向左横向滚动。
    滚动一次后停住，当文本变短/窗口变宽到足以显示完整时自动停止滚动。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._offset = 0          # 当前滚动偏移（像素）
        self._marquee_active = False
        self._scroll_speed = 1.0   # 每 tick 滚动像素
        self._scroll_interval = 40  # ms（约 25 fps）
        self._marquee_timer = QTimer(self)
        self._marquee_timer.timeout.connect(self._tick_marquee)
        self.setAlignment(Qt.AlignCenter)

    def setText(self, text):
        super().setText(text)
        self._restart_marquee()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._restart_marquee()

    def showEvent(self, event):
        super().showEvent(event)
        self._restart_marquee()

    def _restart_marquee(self):
        self._marquee_timer.stop()
        self._offset = 0
        self._marquee_active = False
        # 超出宽度 → 启用 marquee
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self.text())
        label_width = self.width()
        if text_width > label_width + 4:  # 留 4px 裕量
            self._marquee_active = True
            self._marquee_timer.start(self._scroll_interval)
        self.update()

    def _tick_marquee(self):
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self.text())
        label_width = self.width()
        gap = 40  # 滚动一圈后留空多少像素再回来

        # 如果窗口变宽到能完整显示 → 停止
        if text_width <= label_width + 4:
            self._marquee_active = False
            self._marquee_timer.stop()
            self._offset = 0
            self.update()
            return

        max_offset = text_width + gap
        self._offset += self._scroll_speed
        if self._offset > max_offset:
            self._offset = 0
        self.update()

    def paintEvent(self, event):
        if not self._marquee_active:
            super().paintEvent(event)
            return
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        fm = painter.fontMetrics()
        text = self.text()
        text_w = fm.horizontalAdvance(text)
        label_w = self.width()
        gap = 40  # 两段文本间的间隔
        # 垂直居中：baseline y 坐标
        v_center = (self.height() + fm.ascent() - fm.descent()) // 2

        # 从居中位置开始，随 offset 向左移动；滚出左边后右边再跟一份
        cur_x = (label_w - text_w) // 2 - self._offset
        painter.drawText(int(cur_x), v_center, text)
        painter.drawText(int(cur_x + text_w + gap), v_center, text)
        painter.end()


APP_NAME = "Lyrica"
APP_VERSION = "1.0.5"
SETTINGS_FILENAME = "lyrica_settings.json"


def settings_path() -> str:
    if getattr(sys, "frozen", False):
        project_dir = os.path.dirname(sys.executable)
    else:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_dir, SETTINGS_FILENAME)


def format_time(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:
        return "00:00"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


# ============ 后台扫描线程（v1.0.1 新增，v1.0.2 优化2 + 并行化） ============
class ScanWorker(QThread):
    """递归扫描文件夹，并行预读 metadata。
    v1.0.3 优化：接受已有 meta_cache → 跳过已缓存文件；ThreadPoolExecutor 并行读 IO。"""
    finished_ok = Signal(str, int, dict)  # folder_path, total_audio_count, meta_cache
    failed = Signal(str)

    def __init__(self, folder_path: str, existing_meta_cache: dict = None, parent=None):
        super().__init__(parent)
        self._folder = os.path.abspath(folder_path)
        self._existing = existing_meta_cache or {}

    def run(self):
        try:
            from tinytag import TinyTag
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # Step 1: 快速 os.walk 只收集路径（不读 metadata）
            all_paths: list = []
            for root, dirs, files in os.walk(self._folder):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"):
                        all_paths.append(os.path.join(root, fname))

            # Step 2: 已缓存的直接复用，跳过 TinyTag；同时确保 _mtime 存在
            meta_cache: dict = {}
            need_read: list = []
            for p in all_paths:
                if p in self._existing:
                    # 拷贝 + 补 _mtime（老版本持久化恢复的缓存可能缺 _mtime）
                    entry = dict(self._existing[p])
                    if "_mtime" not in entry:
                        try:
                            entry["_mtime"] = os.path.getmtime(p)
                        except OSError:
                            pass
                    meta_cache[p] = entry
                else:
                    need_read.append(p)

            # Step 3: 并行读剩下的
            if need_read:
                def _read_one(p):
                    try:
                        tag = TinyTag.get(p)
                        entry = {
                            "title": tag.title,
                            "artist": tag.artist,
                            "album": tag.album,
                            "duration": f"{tag.duration:.1f}" if tag.duration is not None else None,
                        }
                        try:
                            entry["_mtime"] = os.path.getmtime(p)
                        except OSError:
                            pass
                        return p, entry
                    except Exception:
                        return p, {"title": None, "artist": None, "album": None, "duration": None}

                workers = min(8, len(need_read))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_read_one, p): p for p in need_read}
                    for fut in as_completed(futures):
                        p, m = fut.result()
                        meta_cache[p] = m

            self.finished_ok.emit(self._folder, len(all_paths), meta_cache)
        except Exception as e:
            self.failed.emit(str(e))


# ============ 关于对话框 ============
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setFixedSize(360, 220)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        title = QLabel(f"🎵 {APP_NAME}")
        f = title.font()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignCenter)

        ver = QLabel(f"版本 {APP_VERSION}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet("color:#a6adc8;")

        desc = QLabel(
            "轻量级本地音乐播放器\n基于 Python + just_playback + PySide6\nhttps://github.com/jackwolfey/lyrica")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#45475a;")
        close_btn = QPushButton("✕ 关闭")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addStretch()

        layout.addWidget(title)
        layout.addWidget(ver)
        layout.addSpacing(6)
        layout.addWidget(desc)
        layout.addSpacing(6)
        layout.addWidget(sep)
        layout.addLayout(btn_row)


# ============ 主窗口 ============
class MainWindow(QMainWindow):
    """Lyrica 主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(800, 500)
        self.setMinimumSize(560, 360)

        self.player = MusicPlayer(self)
        self._seeking: bool = False

        # 配置项
        self._minimize_to_tray: bool = False  # 最小化到托盘开关
        self._force_quit: bool = False  # v1.0.3: 强制退出标志（绕过托盘拦截）

        # 系统托盘（可选）
        self._tray: Optional[QSystemTrayIcon] = None

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._init_tray()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ---------- 顶部：歌曲信息 + 元数据 ----------
        # 标题栏：单行 + 超长自动横向滚动（marquee）
        self.lbl_title = _MarqueeLabel("未加载曲目")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        font = self.lbl_title.font()
        font.setPointSize(13)
        font.setBold(True)
        self.lbl_title.setFont(font)
        self.lbl_title.setMinimumHeight(24)
        self.lbl_title.setMaximumHeight(30)  # 强制单行高度
        self.lbl_title.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lbl_title.customContextMenuRequested.connect(self._show_title_menu)
        # v1.1: 左键点击标题栏 → 滚动并高亮当前正在播放的组和曲目
        self.lbl_title.installEventFilter(self)
        root.addWidget(self.lbl_title)

        # v1.0 新增，v1.1 改造：元数据两行各自独立 marquee
        self.lbl_meta_title = _MarqueeLabel("")  # 曲目名行
        self.lbl_meta_title.setAlignment(Qt.AlignCenter)
        self.lbl_meta_title.setMinimumHeight(14)
        self.lbl_meta_detail = _MarqueeLabel("")  # 艺术家 · 专辑行
        self.lbl_meta_detail.setAlignment(Qt.AlignCenter)
        self.lbl_meta_detail.setStyleSheet("color:#a6adc8;")
        self.lbl_meta_detail.setMinimumHeight(14)
        meta_col = QVBoxLayout()
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(0)
        meta_col.addWidget(self.lbl_meta_title)
        meta_col.addWidget(self.lbl_meta_detail)
        meta_container = QWidget()
        meta_container.setLayout(meta_col)
        meta_container.setMaximumHeight(30)
        root.addWidget(meta_container)

        # ---------- 进度条 + 时间 ----------
        progress_row = QHBoxLayout()
        progress_row.setSpacing(6)
        self.lbl_current = QLabel("00:00")
        self.lbl_current.setFixedWidth(40)
        self.lbl_current.setAlignment(Qt.AlignCenter)
        self.slider_progress = QSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.setValue(0)
        self.lbl_total = QLabel("00:00")
        self.lbl_total.setFixedWidth(40)
        self.lbl_total.setAlignment(Qt.AlignCenter)
        progress_row.addWidget(self.lbl_current)
        progress_row.addWidget(self.slider_progress, 1)
        progress_row.addWidget(self.lbl_total)
        root.addLayout(progress_row)

        # ---------- 紧凑控制按钮区 ----------
        # 三段式：左 = 播放模式（锚左） | 中 = ⏮ ▶ ⏭ 窗口居中 | 右 = 🔊 音量（锚右）
        self.cmb_mode = QComboBox()
        for mode in PlayMode:
            self.cmb_mode.addItem(mode.value, mode)
        # 宽度收窄到恰好容纳最长文本（四个汉字）：文本宽 + 左右 padding 各 6px + 边框各 1px
        _fm = self.cmb_mode.fontMetrics()
        _text_w = max(_fm.horizontalAdvance(m.value) for m in PlayMode)
        self.cmb_mode.setFixedWidth(_text_w + 6 * 2 + 1 * 2)
        self.cmb_mode.setFixedHeight(26)

        self.btn_prev = QPushButton("⏮\uFE0F")
        self.btn_prev.setFixedSize(32, 26)
        self.btn_play = QPushButton("▶\uFE0F")
        self.btn_play.setFixedSize(50, 26)
        self.btn_play.setDefault(True)
        self.btn_next = QPushButton("⏭\uFE0F")
        self.btn_next.setFixedSize(32, 26)

        # 左段：锚左
        left_section = QHBoxLayout()
        left_section.setSpacing(4)
        left_section.setContentsMargins(0, 0, 0, 0)
        left_section.addWidget(self.cmb_mode)

        # 中段：紧凑按钮组，不再自带 stretch
        center_section = QHBoxLayout()
        center_section.setSpacing(4)
        center_section.setContentsMargins(0, 0, 0, 0)
        center_section.addWidget(self.btn_prev)
        center_section.addWidget(self.btn_play)
        center_section.addWidget(self.btn_next)

        # 右段：锚右
        right_section = QHBoxLayout()
        right_section.setSpacing(4);
        right_section.setContentsMargins(0, 0, 0, 0)
        right_section.addWidget(QLabel("🔊"))
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(70)
        self.slider_volume.setFixedWidth(100)
        self.slider_volume.setFixedHeight(18)
        right_section.addWidget(self.slider_volume)

        # 用 QGridLayout 把三段叠在同一格，各自按 alignment 贴左/居中/贴右。
        # 中段相对整格（= 窗口内容宽度）居中，与左右段宽度无关，
        # 所以左侧下拉框收窄、右侧音量段宽度变化都不会再影响 ⏮▶⏭ 的居中。
        ctrl_container = QWidget()
        ctrl_grid = QGridLayout(ctrl_container)
        ctrl_grid.setContentsMargins(0, 0, 0, 0)
        ctrl_grid.setSpacing(0)
        ctrl_grid.setColumnStretch(0, 1)

        left_widget = QWidget()
        left_widget.setLayout(left_section)
        center_widget = QWidget()
        center_widget.setLayout(center_section)
        right_widget = QWidget()
        right_widget.setLayout(right_section)

        ctrl_grid.addWidget(left_widget, 0, 0, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        ctrl_grid.addWidget(center_widget, 0, 0, alignment=Qt.AlignCenter)
        ctrl_grid.addWidget(right_widget, 0, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)

        root.addWidget(ctrl_container)

        # ---------- 核心：左右双面板 ----------
        splitter = QSplitter(Qt.Horizontal)

        groups_title = QLabel("📁 播放组")
        gf = groups_title.font()
        gf.setBold(True)
        gf.setPointSize(9)
        groups_title.setFont(gf)
        groups_title.setStyleSheet("color:#a6adc8; padding: 2px;")

        self.groups_list = QListWidget()
        self.groups_list.setAlternatingRowColors(True)
        self.groups_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.groups_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_groups_menu)
        self.groups_list.currentRowChanged.connect(self._on_group_selected)

        left_container = QFrame()
        left_container.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(2)
        left_layout.addWidget(groups_title)
        left_layout.addWidget(self.groups_list)

        self.lbl_tracks_header = QLabel("🎵 曲目")
        tf = self.lbl_tracks_header.font()
        tf.setBold(True)
        tf.setPointSize(9)
        self.lbl_tracks_header.setFont(tf)
        self.lbl_tracks_header.setStyleSheet("color:#a6adc8; padding: 2px;")

        self.tracks_list = QListWidget()
        self.tracks_list.setAlternatingRowColors(True)
        self.tracks_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tracks_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tracks_list.customContextMenuRequested.connect(self._show_tracks_menu)
        self.tracks_list.itemDoubleClicked.connect(self._on_track_double_clicked)

        for lst in (self.groups_list, self.tracks_list):
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setTextElideMode(Qt.ElideRight)

        # v1.1: 接 delegate —— 给"正在播放"项画空心圆角矩形
        # groups_list: 哪一组包含当前播放的曲目
        def _playing_group_row():
            flat = self.player.get_current_index()
            if flat < 0: return None
            return self.player.get_group_of_track(flat)

        # tracks_list: 当前 active_group 内哪首正在播放（若不在当前组则不高亮）
        def _playing_track_row():
            flat = self.player.get_current_index()
            if flat < 0: return None
            gi = self.player.get_group_of_track(flat)
            if gi != self.player.get_active_group_index(): return None
            return self.player.get_track_index_in_group(flat)

        self.groups_list.setItemDelegate(PlayingItemDelegate(_playing_group_row))
        self.tracks_list.setItemDelegate(PlayingItemDelegate(_playing_track_row))

        right_container = QFrame()
        right_container.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(2)
        right_layout.addWidget(self.lbl_tracks_header)
        right_layout.addWidget(self.tracks_list)

        splitter.addWidget(left_container)
        splitter.addWidget(right_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 580])
        splitter.setChildrenCollapsible(False)

        root.addWidget(splitter, 1)
        self.player.set_volume(0.7)

    # ==================== 信号连接 ====================

    def _connect_signals(self):
        self.btn_play.clicked.connect(self.player.toggle_play_pause)
        self.btn_prev.clicked.connect(self.player.prev_track)
        self.btn_next.clicked.connect(self.player.next_track)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.slider_progress.sliderPressed.connect(self._on_seek_start)
        self.slider_progress.sliderReleased.connect(self._on_seek_end)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)

        self.player.position_changed.connect(self._on_position_changed)
        self.player.duration_changed.connect(self._on_duration_changed)
        self.player.state_changed.connect(self._on_state_changed)
        self.player.track_changed.connect(self._on_track_changed)
        self.player.playlist_changed.connect(self._refresh_tracks_list)
        self.player.groups_changed.connect(self._refresh_groups_list)
        self.player.active_group_changed.connect(self._on_active_group_external)
        self.player.volume_changed.connect(self._on_volume_external)
        self.player.play_mode_changed.connect(self._on_play_mode_external)
        self.player.error_occurred.connect(self._on_error)

        # v1.1: 全局快捷键（窗口焦点内生效）
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.player.toggle_play_pause)
        QShortcut(QKeySequence(Qt.Key_Left),   self, activated=self._seek_backward)
        QShortcut(QKeySequence(Qt.Key_Right),  self, activated=self._seek_forward)
        QShortcut(QKeySequence(Qt.Key_Up),     self, activated=self._volume_up)
        QShortcut(QKeySequence(Qt.Key_Down),   self, activated=self._volume_down)
        # 切歌备选（避免某些键盘上方向键布局导致冲突）
        QShortcut(QKeySequence(Qt.Key_Comma),  self, activated=self.player.prev_track)
        QShortcut(QKeySequence(Qt.Key_Period), self, activated=self.player.next_track)

    # ==================== 全局快捷键辅助槽（v1.1 新增） ====================

    @Slot()
    def _seek_backward(self):
        self.player.seek(max(0.0, self.player.get_current_position() - 5))

    @Slot()
    def _seek_forward(self):
        self.player.seek(min(self.player.get_duration(), self.player.get_current_position() + 5))

    @Slot()
    def _volume_up(self):
        self.player.set_volume(min(1.0, self.player.get_volume() + 0.05))

    @Slot()
    def _volume_down(self):
        self.player.set_volume(max(0.0, self.player.get_volume() - 0.05))

    # ==================== 标题栏点击 → 滚动高亮正在播放（v1.1 新增） ====================

    def eventFilter(self, obj, event):
        # 监听 lbl_title 左键点击 → 滚动到正在播放的曲目
        if obj is self.lbl_title and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._scroll_to_playing()
                return True  # 吃掉左键，不触发任何默认行为
        return super().eventFilter(obj, event)

    @Slot()
    def _scroll_to_playing(self):
        """把左侧播放组和右侧曲目列表滚动并高亮到"正在播放"的那一项。
        如果当前激活的播放组里没有正在播放的曲目，会自动切换组。"""
        flat = self.player.get_current_index()
        if flat < 0:
            return
        gi = self.player.get_group_of_track(flat)
        ti = self.player.get_track_index_in_group(flat)

        # 1. 让 player 切到正确的播放组 —— set_active_group 会 emit
        #    active_group_changed → _on_active_group_external → 刷新 tracks_list + 同步 groups_list 选中
        if 0 <= gi < self.groups_list.count():
            self.player.set_active_group(gi)
            self.groups_list.scrollToItem(
                self.groups_list.item(gi), QListWidget.EnsureVisible
            )

        # 2. tracks_list 已被上面的 set_active_group 刷新成正确组，
        #    现在在其中高亮正在播放的曲目并滚动可见
        if 0 <= ti < self.tracks_list.count():
            self.tracks_list.blockSignals(True)
            self.tracks_list.setCurrentRow(ti)
            self.tracks_list.blockSignals(False)
            self.tracks_list.scrollToItem(
                self.tracks_list.item(ti), QListWidget.PositionAtCenter
            )

    # ==================== 系统托盘（v1.0 新增） ====================

    def _make_tray_icon(self) -> QIcon:
        """程序化生成一个简单的托盘图标（音符形状），避免文件依赖"""
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        # 圆形底
        p.setBrush(QColor("#89b4fa"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 28, 28)
        # 白色音符杆 + 椭圆头
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(12, 24, 12, 8)
        p.drawLine(12, 8, 22, 5)
        p.drawEllipse(6, 20, 8, 6)
        p.end()
        return QIcon(pix)

    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return

        icon = self._make_tray_icon()
        self.setWindowIcon(icon)

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(icon)  # 关键修复：必须设置非空图标
        self._tray.setToolTip(APP_NAME)

        menu = QMenu()
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self._tray_show)
        act_play = QAction("▶\uFE0F 播放 / 暂停", self)
        act_play.triggered.connect(self.player.toggle_play_pause)
        act_next = QAction("⏭\uFE0F 下一曲", self)
        act_next.triggered.connect(self.player.next_track)
        act_prev = QAction("⏮\uFE0F 上一曲", self)
        act_prev.triggered.connect(self.player.prev_track)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._tray_quit)

        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_play)
        menu.addAction(act_next)
        menu.addAction(act_prev)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._tray_show()

    def _tray_show(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._save_settings()
        QApplication.quit()

    def set_minimize_to_tray(self, enabled: bool):
        self._minimize_to_tray = enabled

    # 重写 closeEvent / changeEvent 实现最小化到托盘
    def changeEvent(self, event):
        # 开启最小化到托盘后，点最小化按钮也直接 hide() 掉任务栏图标
        if event.type() == QEvent.WindowStateChange:
            if self._minimize_to_tray and self._tray is not None and self.isMinimized():
                self.hide()
                self._tray.showMessage(APP_NAME, "已最小化到系统托盘", QSystemTrayIcon.Information, 800)
        super().changeEvent(event)

    # ==================== 右键菜单 ====================

    @Slot(QPoint)
    def _show_title_menu(self, pos: QPoint):
        menu = QMenu(self)

        # v1.0 新增：托盘开关
        act_tray = QAction("启用最小化到托盘", self, checkable=True)
        act_tray.setChecked(self._minimize_to_tray)
        act_tray.toggled.connect(self.set_minimize_to_tray)
        menu.addAction(act_tray)
        menu.addSeparator()

        act_about = QAction(f"关于 {APP_NAME}...", self)
        act_about.triggered.connect(lambda: AboutDialog(self).exec())
        menu.addAction(act_about)

        # v1.0.3：直接退出，无视最小化到托盘开关
        menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._force_quit_app)
        act_quit.setShortcut("Ctrl+Q")
        menu.addAction(act_quit)

        menu.exec(self.lbl_title.mapToGlobal(pos))

    @Slot(QPoint)
    def _show_groups_menu(self, pos: QPoint):
        menu = QMenu(self)

        act_add_folder = QAction("📁 添加文件夹...", self)
        act_add_files = QAction("📄 添加文件...", self)
        menu.addAction(act_add_folder)
        menu.addAction(act_add_files)
        menu.addSeparator()

        # v1.0 新增：组排序 / 移动
        gi = self.groups_list.currentRow()
        act_up = QAction("⬆ 将该组上移", self)
        act_up.setEnabled(gi > 0)
        act_down = QAction("⬇ 将该组下移", self)
        act_down.setEnabled(0 <= gi < self.player.get_group_count() - 1)
        act_reset = QAction("🔄 按默认顺序排序", self)
        act_reset.setEnabled(self.player.get_group_count() > 1)
        menu.addAction(act_up)
        menu.addAction(act_down)
        menu.addAction(act_reset)
        menu.addSeparator()

        act_remove_group = QAction("✕ 移除该播放组", self)
        act_remove_group.setEnabled(gi >= 0)
        menu.addAction(act_remove_group)
        menu.addSeparator()

        act_clear = QAction("🗑 清空所有播放组", self)
        act_clear.setEnabled(self.player.get_group_count() > 0)
        menu.addAction(act_clear)

        act_add_folder.triggered.connect(self._on_add_folder)
        act_add_files.triggered.connect(self._on_add_files)
        act_up.triggered.connect(lambda: self.player.move_group_up(gi))
        act_down.triggered.connect(lambda: self.player.move_group_down(gi))
        act_reset.triggered.connect(self.player.reset_group_order)
        act_remove_group.triggered.connect(lambda: self.player.remove_group(gi))
        act_clear.triggered.connect(self._on_clear_all_groups)

        menu.exec(self.groups_list.mapToGlobal(pos))

    @Slot(QPoint)
    def _show_tracks_menu(self, pos: QPoint):
        menu = QMenu(self)

        # v1.0 新增：排序子菜单
        gi = self.player.get_active_group_index()
        has_group = gi >= 0 and self.player.get_group_track_count(gi) > 0

        sort_menu = menu.addMenu("🔀 排序方式")
        act_sort_name = QAction("按文件名", self)
        act_sort_title = QAction("按曲目名称", self)
        act_sort_artist = QAction("按艺术家", self)
        act_sort_album = QAction("按专辑", self)
        for a in (act_sort_name, act_sort_title, act_sort_artist, act_sort_album):
            a.setEnabled(has_group)
        sort_menu.addAction(act_sort_name)
        sort_menu.addAction(act_sort_title)
        sort_menu.addAction(act_sort_artist)
        sort_menu.addAction(act_sort_album)
        menu.addSeparator()

        act_play = QAction("▶\uFE0F 播放", self)
        act_play.setEnabled(self.tracks_list.currentRow() >= 0)
        menu.addAction(act_play)

        act_sort_name.triggered.connect(lambda: self.player.sort_group_tracks(gi, "filename"))
        act_sort_title.triggered.connect(lambda: self.player.sort_group_tracks(gi, "title"))
        act_sort_artist.triggered.connect(lambda: self.player.sort_group_tracks(gi, "artist"))
        act_sort_album.triggered.connect(lambda: self.player.sort_group_tracks(gi, "album"))
        act_play.triggered.connect(self._play_selected_track)

        menu.exec(self.tracks_list.mapToGlobal(pos))

    def _play_selected_track(self):
        row = self.tracks_list.currentRow()
        gi = self.player.get_active_group_index()
        if row >= 0 and gi >= 0:
            self.player.play_track_in_group(gi, row)

    # ==================== 槽函数：按钮/菜单事件 ====================

    @Slot()
    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if not folder:
            return

        # v1.0.1：后台线程扫描 + 等待对话框
        dlg = QDialog(self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setFixedSize(320, 120)
        dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        lbl = QLabel(f"正在分析文件夹内容...\n{folder}")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color:#a6adc8;")
        layout.addWidget(lbl)
        btn = QPushButton("✕ 取消")
        btn.clicked.connect(dlg.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # v1.0.3: 传入已有 meta_cache → 跳过已缓存文件，避免重复 TinyTag 读
        worker = ScanWorker(folder, self.player._meta_cache, self)
        cancel_flag = {"cancelled": False}

        def on_ok(folder_path: str, count: int, meta_cache: dict):
            if cancel_flag["cancelled"]:
                worker.deleteLater()
                return
            # v1.0.2 优化2: 把 ScanWorker 预读的 metadata 填入 player 缓存
            self.player.preload_metadata(meta_cache)
            added = self.player.add_folder_recursive(folder_path)
            dlg.accept()
            worker.deleteLater()
            if added == 0:
                QMessageBox.information(self, APP_NAME,
                                        f"未找到包含音频文件的子文件夹：\n{folder_path}")

        def on_err(msg: str):
            if cancel_flag["cancelled"]:
                worker.deleteLater()
                return
            dlg.reject()
            QMessageBox.warning(self, APP_NAME, f"扫描失败：{msg}")
            worker.deleteLater()

        def on_cancel():
            cancel_flag["cancelled"] = True

        btn.clicked.connect(on_cancel)  # 先标记取消，再 reject dialog
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_err)

        worker.start()
        dlg.exec()
        # 如果对话框因其他原因（如窗口关闭）退出，也标记取消
        cancel_flag["cancelled"] = cancel_flag["cancelled"] or dlg.result() != QDialog.Accepted

    @Slot()
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus);;所有文件 (*.*)"
        )
        if files:
            self.player.add_files(files)

    @Slot()
    def _on_clear_all_groups(self):
        if self.player.get_group_count() > 0:
            ret = QMessageBox.question(self, APP_NAME, "确定清空所有播放组吗？",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret == QMessageBox.Yes:
                self.player.clear_groups()

    @Slot(int)
    def _on_mode_changed(self, index: int):
        mode = self.cmb_mode.itemData(index)
        if isinstance(mode, PlayMode): self.player.set_play_mode(mode)

    @Slot()
    def _on_seek_start(self):
        self._seeking = True

    @Slot()
    def _on_seek_end(self):
        self._seeking = False
        val = self.slider_progress.value() / 1000.0
        duration = self.player.get_duration()
        self.player.seek(val * duration)

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self.player.set_volume(value / 100.0)

    @Slot(str)
    def _on_volume_external(self, vol: float):
        self.slider_volume.blockSignals(True)
        self.slider_volume.setValue(int(vol * 100))
        self.slider_volume.blockSignals(False)

    @Slot(object)
    def _on_play_mode_external(self, mode: PlayMode):
        self.cmb_mode.blockSignals(True)
        idx = self.cmb_mode.findData(mode)
        if idx >= 0: self.cmb_mode.setCurrentIndex(idx)
        self.cmb_mode.blockSignals(False)

    @Slot(int)
    def _on_group_selected(self, group_index: int):
        self.player.set_active_group(group_index)
        self._refresh_tracks_list()

    @Slot(int)
    def _on_active_group_external(self, group_index: int):
        if group_index >= 0:
            self.groups_list.blockSignals(True)
            self.groups_list.setCurrentRow(group_index)
            self.groups_list.blockSignals(False)
        self._refresh_tracks_list()

    @Slot(QListWidgetItem)
    def _on_track_double_clicked(self, item: QListWidgetItem):
        row = self.tracks_list.row(item)
        gi = self.player.get_active_group_index()
        if row >= 0 and gi >= 0:
            self.player.play_track_in_group(gi, row)

    # ==================== 槽函数：播放器信号 ====================

    @Slot(float)
    def _on_position_changed(self, pos: float):
        if self._seeking:
            self.lbl_current.setText(format_time(pos))
            return
        duration = self.player.get_duration()
        if duration > 0:
            self.slider_progress.blockSignals(True)
            self.slider_progress.setValue(int(min(pos / duration, 1.0) * 1000))
            self.slider_progress.blockSignals(False)
        self.lbl_current.setText(format_time(pos))

    @Slot(float)
    def _on_duration_changed(self, duration: float):
        self.lbl_total.setText(format_time(duration))

    @Slot(bool)
    def _on_state_changed(self, playing: bool):
        self.btn_play.setText("⏸" if playing else "▶\uFE0F")
        self.btn_play.setToolTip("暂停" if playing else "播放")

    @Slot(int)
    def _on_track_changed(self, flat_index: int):
        if 0 <= flat_index < self.player.get_track_count():
            path = self.player.get_playlist()[flat_index]
            # 标题（单行 + 超长自动 marquee）
            name = self.player.get_track_name(flat_index)
            self.lbl_title.setText(f"🎵 {name}")
            self.lbl_title.setToolTip(path)
            # 元数据（v1.1 改造为两个独立 marquee label）
            meta = self.player.get_track_metadata(path)
            track_title = meta.get("title") or ""
            # fallback：用无扩展名的文件名
            if not track_title:
                track_title = os.path.splitext(os.path.basename(path))[0]
            self.lbl_meta_title.setText(track_title)
            parts = []
            if meta.get("artist"): parts.append(meta["artist"])
            if meta.get("album"): parts.append(meta["album"])
            self.lbl_meta_detail.setText(" · ".join(parts) if parts else "")

            # 高亮左侧组
            gi = self.player.get_group_of_track(flat_index)
            ti = self.player.get_track_index_in_group(flat_index)
            if gi >= 0:
                self.groups_list.blockSignals(True)
                self.groups_list.setCurrentRow(gi)
                self.groups_list.blockSignals(False)
            if 0 <= ti < self.tracks_list.count():
                self.tracks_list.blockSignals(True)
                self.tracks_list.setCurrentRow(ti)
                self.tracks_list.blockSignals(False)
        else:
            self.lbl_title.setText("未加载曲目")
            self.lbl_title.setToolTip("")
            self.lbl_meta_title.setText("")
            self.lbl_meta_detail.setText("")

    @Slot()
    def _refresh_groups_list(self):
        # v1.0.2 优化1: 冻结 repaint + 批量 addItems，避免 N 次 layout
        self.groups_list.blockSignals(True)
        self.groups_list.setUpdatesEnabled(False)
        self.groups_list.clear()
        names = [self.player.get_group_name(i) for i in range(self.player.get_group_count())]
        self.groups_list.addItems(names)
        cur = self.player.get_active_group_index()
        if 0 <= cur < self.groups_list.count(): self.groups_list.setCurrentRow(cur)
        self.groups_list.setUpdatesEnabled(True)
        self.groups_list.blockSignals(False)
        self._refresh_tracks_list()

    @Slot()
    def _refresh_tracks_list(self):
        gi = self.player.get_active_group_index()
        self.tracks_list.blockSignals(True)
        self.tracks_list.setUpdatesEnabled(False)
        self.tracks_list.clear()
        if 0 <= gi:
            tracks = self.player.get_group_tracks(gi)
            # 每行用 _MarqueeLabel 作为 widget，超长自动横向滚动
            for t in tracks:
                row = QListWidgetItem(self.tracks_list)
                row.setData(Qt.UserRole, t)  # 存完整路径
                label = _MarqueeLabel(os.path.basename(t))
                label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                label.setContentsMargins(4, 0, 4, 0)
                self.tracks_list.addItem(row)
                self.tracks_list.setItemWidget(row, label)
            self.lbl_tracks_header.setText(f"🎵 {len(tracks)} 首")

            # 仅在真正有已加载曲目时恢复高亮
            cur = self.player.get_current_index()
            has_loaded = self.player.is_playing() or self.player.is_paused()
            if cur >= 0 and has_loaded and self.player.get_group_of_track(cur) == gi:
                ti = self.player.get_track_index_in_group(cur)
                if 0 <= ti < self.tracks_list.count():
                    self.tracks_list.setCurrentRow(ti)
        else:
            self.lbl_tracks_header.setText("🎵 曲目")
        self.tracks_list.setUpdatesEnabled(True)
        self.tracks_list.blockSignals(False)

    @Slot(str)
    def _on_error(self, msg: str):
        QMessageBox.warning(self, APP_NAME, msg)

    # ==================== 关闭时持久化 ====================

    def closeEvent(self, event):
        # v1.0.3: _force_quit 直接退出，无视最小化到托盘
        if self._force_quit:
            self._save_settings()
            event.accept()
            return
        # v1.0.1：启用最小化到托盘后，关闭窗口 → 直接 hide()
        # hide() 会同时隐藏任务栏图标，托盘图标保持可见
        if self._minimize_to_tray and self._tray is not None and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._tray.showMessage(APP_NAME, "程序在托盘继续运行", QSystemTrayIcon.Information, 800)
            return
        self._save_settings()
        event.accept()

    @Slot()
    def _force_quit_app(self):
        """v1.0.3: 强制退出（绕过托盘）— 标题右键菜单 / Ctrl+Q 入口"""
        self._force_quit = True
        self.close()

    def _save_settings(self):
        mode = self.player.get_play_mode()
        data: Dict[str, Any] = {
            "version": APP_VERSION,
            "groups": self.player.get_playlists_for_save(),
            "active_group_index": self.player.get_active_group_index(),
            "current_index": max(0, self.player.get_current_index()),
            "play_mode": mode.name if isinstance(mode, PlayMode) else "SEQUENCE",
            "volume": self.player.get_volume(),
            "position": self.player.get_current_position(),
            "window_size": {"w": self.width(), "h": self.height()},
            # v1.0 新增
            "minimize_to_tray": self._minimize_to_tray,
            # v1.0.2: metadata 缓存持久化，重启后排序零 IO
            "meta_cache": self.player._meta_cache,
        }
        try:
            with open(settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_settings(self):
        path = settings_path()
        if not os.path.isfile(path): return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        # 窗口大小
        ws = data.get("window_size")
        if isinstance(ws, dict):
            w = int(ws.get("w", 0))
            h = int(ws.get("h", 0))
            if w >= 400 and h >= 300: self.resize(QSize(w, h))

        # v1.0：最小化到托盘开关
        self._minimize_to_tray = bool(data.get("minimize_to_tray", True))

        # 播放组
        restored = False
        saved_groups = data.get("groups")
        if isinstance(saved_groups, list) and saved_groups:
            count = self.player.restore_groups(saved_groups)
            restored = count > 0
        if not restored:
            legacy = [p for p in data.get("playlist", []) if os.path.isfile(p)]
            if legacy:
                self.player.add_files(legacy)
                restored = True

        if restored:
            agi = int(data.get("active_group_index", 0))
            if 0 <= agi < self.player.get_group_count():
                self.player.set_active_group(agi)
            idx = int(data.get("current_index", 0))
            if 0 <= idx < self.player.get_track_count():
                self.player._current_index = idx
            self._refresh_groups_list()
            self._refresh_tracks_list()
            if 0 <= self.player.get_current_index():
                self.player.track_changed.emit(self.player.get_current_index())

            # v1.0.1：恢复上次暂停的播放位置
            saved_pos = float(data.get("position", 0.0))
            if saved_pos > 0.01:
                self.player.restore_paused_position(saved_pos)

        # v1.0.2: 还原 metadata 缓存，重启后排序热命中
        saved_meta = data.get("meta_cache")
        if isinstance(saved_meta, dict):
            self.player._meta_cache = saved_meta

        mode_name = data.get("play_mode", "SEQUENCE")
        try:
            self.player.set_play_mode(PlayMode[mode_name])
        except KeyError:
            pass
        self.player.set_volume(float(data.get("volume", 0.7)))
