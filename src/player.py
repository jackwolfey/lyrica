"""
Lyrica v0.3 - 核心播放器模块
引入 PlaylistGroup：每个文件夹 = 一个播放组
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from tinytag import TinyTag
from just_playback import Playback


# 支持的音频文件扩展名
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}


class PlayMode(Enum):
    # 组内范围（v1.1：语义修正，原先是全曲库范围）
    SEQUENCE = "组内顺序"
    LIST_LOOP = "组内循环"
    # 跨全曲库范围（v1.1 新增，对应原 SEQUENCE/LIST_LOOP 的实际行为）
    CROSS_SEQUENCE = "跨组顺序"
    CROSS_LIST_LOOP = "跨组循环"
    # 单曲循环：永远只在当前曲目，无范围概念
    SINGLE_LOOP = "单曲循环"


@dataclass
class PlaylistGroup:
    """一个播放组 = 一个包含音乐文件的文件夹"""
    name: str               # 显示名（文件夹名）
    folder_path: str        # 该文件夹的绝对路径
    tracks: List[str] = field(default_factory=list)  # 该文件夹下的音乐文件绝对路径（排序后）

    def to_dict(self) -> dict:
        return {"folder_path": self.folder_path}


class MusicPlayer(QObject):
    """轻量级本地音乐播放器"""

    # ===== 信号 =====
    position_changed = Signal(float)
    duration_changed = Signal(float)
    state_changed = Signal(bool)
    track_changed = Signal(int)              # 平铺列表中的索引
    playlist_changed = Signal()               # 平铺曲目列表变化
    groups_changed = Signal()                 # 播放组列表变化
    active_group_changed = Signal(int)        # 当前激活的组索引
    play_mode_changed = Signal(object)
    volume_changed = Signal(float)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playback = Playback()

        # ===== 组数据模型 =====
        self._groups: List[PlaylistGroup] = []
        self._active_group_index: int = -1    # 左侧当前选中的组（UI 展示用）

        # ===== 平铺数据模型（用于播放控制） =====
        self._playlist: List[str] = []        # 所有组的 tracks 顺序拼接
        self._current_index: int = -1         # 当前播放曲目在平铺列表中的索引

        self._play_mode: PlayMode = PlayMode.SEQUENCE

        self._was_playing: bool = False
        self._loaded_path: Optional[str] = None

        # 元数据缓存（避免每次都 IO 读 tag）
        self._meta_cache: Dict[str, Dict[str, Optional[str]]] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ==================== 播放组管理（v0.3 新增） ====================

    def add_folder_recursive(self, folder_path: str) -> int:
        """扫描文件夹及所有子文件夹，每个含音乐的文件夹作为一个 PlaylistGroup。
        返回新添加的曲目总数。"""
        if not os.path.isdir(folder_path):
            return 0

        total_added = 0
        folder_path = os.path.abspath(folder_path)

        # 收集所有候选文件夹：本身 + 所有子文件夹
        candidate_folders = {folder_path}
        for root, dirs, files in os.walk(folder_path):
            for d in dirs:
                candidate_folders.add(os.path.abspath(os.path.join(root, d)))

        # 当前已收录的文件集合（防止嵌套重复）
        existing_files = {t for g in self._groups for t in g.tracks}

        new_groups_added = False
        for fpath in sorted(candidate_folders):
            tracks = self._scan_folder_flat(fpath)
            # 去重：只保留新文件
            unique_tracks = [t for t in tracks if t not in existing_files]
            if unique_tracks:
                group_name = os.path.basename(fpath) or fpath
                group = PlaylistGroup(
                    name=group_name,
                    folder_path=fpath,
                    tracks=unique_tracks,
                )
                self._groups.append(group)
                existing_files.update(unique_tracks)
                total_added += len(unique_tracks)
                new_groups_added = True

        if total_added > 0:
            self._rebuild_flat_playlist()
            if self._active_group_index == -1:
                self._active_group_index = 0
            if self._current_index == -1:
                self._current_index = 0

        self.groups_changed.emit()
        self.playlist_changed.emit()
        if new_groups_added and self._active_group_index >= 0:
            self.active_group_changed.emit(self._active_group_index)

        return total_added

    def _scan_folder_flat(self, folder_path: str) -> List[str]:
        """只扫描 folder_path 直接子文件，不递归"""
        tracks = []
        try:
            for fname in sorted(os.listdir(folder_path)):
                full = os.path.join(folder_path, fname)
                if os.path.isfile(full):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        tracks.append(os.path.abspath(full))
        except OSError:
            pass
        return tracks

    def _rebuild_flat_playlist(self):
        """从 groups 重建平铺 playlist"""
        self._playlist = []
        for g in self._groups:
            self._playlist.extend(g.tracks)
        # 当前索引越界修正
        if self._current_index >= len(self._playlist):
            self._current_index = max(0, len(self._playlist) - 1) if self._playlist else -1

    def set_active_group(self, group_index: int):
        """设置左侧当前选中的播放组（UI 展示用）"""
        if 0 <= group_index < len(self._groups):
            self._active_group_index = group_index
            self.active_group_changed.emit(group_index)

    def get_active_group_index(self) -> int:
        return self._active_group_index

    def _is_cross_mode(self) -> bool:
        """当前播放模式是否跨全曲库（vs 仅限 active group 内）"""
        return self._play_mode in (PlayMode.CROSS_SEQUENCE, PlayMode.CROSS_LIST_LOOP)

    def _get_play_range(self) -> tuple:
        """返回当前播放范围在 flat playlist 中的 [start, end) 左闭右开。
        跨组模式 → (0, len(_playlist))
        组内模式 → active group 在 flat list 中的区间"""
        if self._is_cross_mode():
            return 0, len(self._playlist)
        if not self._groups or self._active_group_index < 0:
            return 0, len(self._playlist)
        start = 0
        for i in range(self._active_group_index):
            start += len(self._groups[i].tracks)
        end = start + len(self._groups[self._active_group_index].tracks)
        return start, end

    def get_group_count(self) -> int:
        return len(self._groups)

    def get_group_name(self, group_index: int) -> str:
        if 0 <= group_index < len(self._groups):
            g = self._groups[group_index]
            count = len(g.tracks)
            return f"{g.name} ({count})"
        return ""

    def get_group_tracks(self, group_index: int) -> List[str]:
        """返回指定组的曲目列表"""
        if 0 <= group_index < len(self._groups):
            return list(self._groups[group_index].tracks)
        return []

    def get_group_track_count(self, group_index: int) -> int:
        if 0 <= group_index < len(self._groups):
            return len(self._groups[group_index].tracks)
        return 0

    def get_group_of_track(self, flat_index: int) -> int:
        """给定平铺列表索引，返回所属组索引"""
        offset = 0
        for gi, g in enumerate(self._groups):
            if offset <= flat_index < offset + len(g.tracks):
                return gi
            offset += len(g.tracks)
        return -1

    def get_track_index_in_group(self, flat_index: int) -> int:
        """给定平铺列表索引，返回在所属组内的索引"""
        offset = 0
        for gi, g in enumerate(self._groups):
            if offset <= flat_index < offset + len(g.tracks):
                return flat_index - offset
            offset += len(g.tracks)
        return -1

    def get_playlists_for_save(self) -> List[dict]:
        """返回可持久化的组数据（只存 folder_path，启动时重新扫描）"""
        return [g.to_dict() for g in self._groups]

    def restore_groups(self, saved_groups: List[dict]) -> int:
        """从持久化数据恢复组（重新扫描）"""
        self._groups.clear()
        self._playlist.clear()

        total = 0
        for item in saved_groups:
            folder_path = item.get("folder_path", "")
            if folder_path and os.path.isdir(folder_path):
                tracks = self._scan_folder_flat(folder_path)
                if tracks:
                    name = os.path.basename(folder_path) or folder_path
                    self._groups.append(PlaylistGroup(name=name, folder_path=folder_path, tracks=tracks))
                    total += len(tracks)

        self._rebuild_flat_playlist()
        if self._groups:
            self._active_group_index = 0
            if self._current_index < 0:
                self._current_index = 0
        else:
            self._active_group_index = -1
            self._current_index = -1

        self.groups_changed.emit()
        self.playlist_changed.emit()
        if self._groups:
            self.active_group_changed.emit(0)
        return total

    def clear_groups(self):
        """清空所有播放组"""
        self.stop()
        self._groups.clear()
        self._playlist.clear()
        self._active_group_index = -1
        self._current_index = -1
        self.groups_changed.emit()
        self.playlist_changed.emit()
        self.active_group_changed.emit(-1)
        self.track_changed.emit(-1)

    def remove_group(self, group_index: int):
        """移除一个播放组"""
        if not (0 <= group_index < len(self._groups)):
            return

        # 先算出这个组的 track 在 flat list 中的索引范围
        offset = 0
        for gi, g in enumerate(self._groups):
            if gi == group_index:
                group_start = offset
                group_end = offset + len(g.tracks)
                break
            offset += len(g.tracks)
        else:
            return

        was_current_inside = group_start <= self._current_index < group_end

        # 移除组
        self._groups.pop(group_index)
        self._rebuild_flat_playlist()

        if was_current_inside:
            self.stop()

        # 修正 current_index
        if was_current_inside:
            if self._current_index >= len(self._playlist):
                self._current_index = len(self._playlist) - 1
        elif self._current_index >= group_end:
            self._current_index -= (group_end - group_start)

        # 修正 active_group_index
        if group_index < self._active_group_index:
            self._active_group_index -= 1
        elif group_index == self._active_group_index:
            if group_index < len(self._groups):
                self._active_group_index = group_index
            else:
                self._active_group_index = len(self._groups) - 1

        self.groups_changed.emit()
        self.playlist_changed.emit()
        if self._active_group_index >= 0:
            self.active_group_changed.emit(self._active_group_index)
        if self._current_index >= 0:
            self.track_changed.emit(self._current_index)

    def play_track_in_group(self, group_index: int, track_index: int):
        """播放指定组内的指定曲目"""
        # 计算平铺索引
        flat_idx = 0
        for gi in range(group_index):
            if 0 <= gi < len(self._groups):
                flat_idx += len(self._groups[gi].tracks)

        if 0 <= group_index < len(self._groups) and 0 <= track_index < len(self._groups[group_index].tracks):
            self._current_index = flat_idx + track_index
            self._active_group_index = group_index
            self.active_group_changed.emit(group_index)
            self.play()

    def play_active_group_next(self):
        """激活的组内下一首（UI 右键菜单用）"""
        if 0 <= self._active_group_index < len(self._groups):
            self.play_track_in_group(self._active_group_index, 0)

    # ==================== 元数据查询（v1.0 新增，v1.1 加 mtime TTL） ====================

    def get_track_metadata(self, file_path: str) -> Dict[str, Optional[str]]:
        """获取音频文件元数据（带缓存 + 文件 mtime 校验）。
        若缓存有 _mtime 且当前文件 mtime 不匹配，说明标签被重新打过，自动重读。
        若缓存无 _mtime（向后兼容旧版本），直接信任缓存。"""
        cached = self._meta_cache.get(file_path)
        if cached is not None:
            cached_mtime = cached.get("_mtime")
            # 没有 _mtime → 旧缓存条目，直接信任（不触发重读）
            if cached_mtime is None:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
            # 有 _mtime → 校验是否匹配
            try:
                current_mtime = os.path.getmtime(file_path)
                if abs(current_mtime - cached_mtime) < 1.0:
                    return {k: v for k, v in cached.items() if not k.startswith("_")}
            except OSError:
                pass
            # mtime 不匹配或无法获取 → 重读

        result: Dict[str, Optional[str]] = {
            "title": None, "artist": None, "album": None, "duration": None,
        }
        try:
            tag = TinyTag.get(file_path)
            result["title"] = tag.title
            result["artist"] = tag.artist
            result["album"] = tag.album
            if tag.duration is not None:
                result["duration"] = f"{tag.duration:.1f}"
        except Exception:
            pass

        # 缓存 + 记录当前 mtime（用于下次 TTL 校验）
        try:
            result["_mtime"] = os.path.getmtime(file_path)
        except OSError:
            pass
        self._meta_cache[file_path] = result
        return {k: v for k, v in result.items() if not k.startswith("_")}

    def preload_metadata(self, meta_cache: Dict[str, Dict[str, Optional[str]]]):
        """批量预填 metadata 缓存（由 ScanWorker 后台扫描完成）。
        对缺失 _mtime 的旧缓存条目自动补 mtime，保证 TTL 校验链路完整。"""
        for path, meta in meta_cache.items():
            if path not in self._meta_cache:
                entry = dict(meta)  # 拷贝，避免修改调用方 dict
                if "_mtime" not in entry:
                    try:
                        entry["_mtime"] = os.path.getmtime(path)
                    except OSError:
                        pass
                self._meta_cache[path] = entry

    def get_current_track_metadata(self) -> Dict[str, Optional[str]]:
        if 0 <= self._current_index < len(self._playlist):
            return self.get_track_metadata(self._playlist[self._current_index])
        return {"title": None, "artist": None, "album": None, "duration": None}

    # ==================== 曲目排序（v1.0 新增） ====================

    def sort_group_tracks(self, group_index: int, sort_key: str):
        """对指定组内的曲目排序。sort_key: 'filename' / 'title' / 'artist' / 'album'"""
        if not (0 <= group_index < len(self._groups)):
            return

        group = self._groups[group_index]

        # 记住当前正在播放的曲目路径，排序后恢复到同一首（不只是同一位置）
        playing_path = None
        if self._current_index >= 0 and self._current_index < len(self._playlist):
            playing_path = self._playlist[self._current_index]

        def _sort_key(path: str):
            fname = os.path.basename(path)
            # v1.0.2 优化3: filename 排序直接短路，不走 metadata
            if sort_key == "filename":
                return fname.lower()
            meta = self.get_track_metadata(path)
            if sort_key == "title":
                val = meta["title"] or os.path.splitext(fname)[0]
                return val.lower()
            elif sort_key == "artist":
                return (meta["artist"] or "").lower()
            elif sort_key == "album":
                return (meta["album"] or "").lower()
            else:
                return fname.lower()

        group.tracks.sort(key=_sort_key)

        self._rebuild_flat_playlist()

        # 恢复播放曲目
        if playing_path and playing_path in self._playlist:
            self._current_index = self._playlist.index(playing_path)

        self.groups_changed.emit()
        self.playlist_changed.emit()

    # ==================== 播放组移动 / 默认排序（v1.0 新增） ====================

    def move_group_up(self, group_index: int):
        """将指定播放组上移一位"""
        if not (0 < group_index < len(self._groups)):
            return
        self._swap_groups(group_index - 1, group_index)

    def move_group_down(self, group_index: int):
        """将指定播放组下移一位"""
        if not (0 <= group_index < len(self._groups) - 1):
            return
        self._swap_groups(group_index, group_index + 1)

    def reset_group_order(self):
        """按文件夹路径（默认扫描顺序）重排所有播放组"""
        if len(self._groups) <= 1:
            return
        self._groups.sort(key=lambda g: g.folder_path.lower())
        self._rebuild_flat_playlist()
        # 保持当前激活组索引不变（指向同一组）
        self.groups_changed.emit()
        self.playlist_changed.emit()
        if self._active_group_index >= 0:
            self.active_group_changed.emit(self._active_group_index)

    def _swap_groups(self, i: int, j: int):
        """交换两个组的位置，保持播放状态不丢失"""
        playing_path = None
        if self._current_index >= 0 and self._current_index < len(self._playlist):
            playing_path = self._playlist[self._current_index]
        active_path = None
        if 0 <= self._active_group_index < len(self._groups):
            active_path = self._groups[self._active_group_index].folder_path

        self._groups[i], self._groups[j] = self._groups[j], self._groups[i]
        self._rebuild_flat_playlist()

        # 恢复 active_group_index
        if active_path:
            for idx, g in enumerate(self._groups):
                if g.folder_path == active_path:
                    self._active_group_index = idx
                    break

        # 恢复正在播放的曲目
        if playing_path and playing_path in self._playlist:
            self._current_index = self._playlist.index(playing_path)

        self.groups_changed.emit()
        self.playlist_changed.emit()
        if self._active_group_index >= 0:
            self.active_group_changed.emit(self._active_group_index)

    def restore_paused_position(self, position: float) -> bool:
        """加载当前曲目但不播放，并 seek 到指定位置。返回是否成功。
        用于程序重启后恢复上次暂停的播放位置。"""
        if not (0 <= self._current_index < len(self._playlist)):
            return False
        file_path = self._playlist[self._current_index]
        try:
            if self._loaded_path != file_path:
                self._playback.load_file(file_path)
                self._loaded_path = file_path
                self.duration_changed.emit(self._playback.duration)

            # just_playback 仅在 active=True（playing/paused）时 seek 才生效
            # 所以先 play 让底层激活，再 seek，再 pause
            self._playback.play()
            self._playback.seek(max(0.0, min(position, self._playback.duration)))
            self._playback.pause()   # 停在目标位置，不自动播放
            self._was_playing = False
            self.position_changed.emit(self._playback.curr_pos)
            self.state_changed.emit(False)
            return True
        except Exception:
            return False

    # ==================== 平铺播放接口（兼容旧调用） ====================

    def add_folder(self, folder_path: str) -> int:
        """v0.3 兼容：走递归扫描"""
        return self.add_folder_recursive(folder_path)

    def add_files(self, file_paths: List[str]) -> int:
        """兼容旧的 add_files：将文件所在目录作为 PlaylistGroup 合并"""
        # 按父目录分组
        groups: dict[str, List[str]] = {}
        for path in file_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in SUPPORTED_EXTENSIONS and os.path.isfile(path):
                parent = os.path.abspath(os.path.dirname(path))
                groups.setdefault(parent, []).append(os.path.abspath(path))

        total_added = 0
        existing_files = {t for g in self._groups for t in g.tracks}
        for parent, tracks in groups.items():
            # dict.fromkeys 先去重入参里的重复路径，再排除已收录的
            unique = [t for t in dict.fromkeys(sorted(tracks)) if t not in existing_files]
            if unique:
                self._groups.append(PlaylistGroup(
                    name=os.path.basename(parent) or parent,
                    folder_path=parent,
                    tracks=unique,
                ))
                existing_files.update(unique)
                total_added += len(unique)

        if total_added > 0:
            self._rebuild_flat_playlist()
            if self._active_group_index == -1:
                self._active_group_index = len(self._groups) - 1
                self.active_group_changed.emit(self._active_group_index)
            if self._current_index == -1:
                self._current_index = 0

        self.groups_changed.emit()
        self.playlist_changed.emit()
        return total_added

    def clear_playlist(self):
        self.clear_groups()

    def remove_track(self, index: int):
        """从平铺列表中移除一首曲目"""
        if not (0 <= index < len(self._playlist)):
            return

        # 找到所属组和组内索引
        offset = 0
        for gi, g in enumerate(self._groups):
            if offset <= index < offset + len(g.tracks):
                idx_in_group = index - offset
                break
            offset += len(g.tracks)
        else:
            return

        g.tracks.pop(idx_in_group)
        was_current = (index == self._current_index)

        # 如果组空了，移除组
        if not g.tracks:
            self._groups.pop(gi)

        self._rebuild_flat_playlist()

        if was_current:
            self.stop()

        self.groups_changed.emit()
        self.playlist_changed.emit()
        self.active_group_changed.emit(self._active_group_index)

    def get_playlist(self) -> List[str]:
        return list(self._playlist)

    def get_track_count(self) -> int:
        return len(self._playlist)

    def get_track_name(self, index: int) -> str:
        if 0 <= index < len(self._playlist):
            return os.path.basename(self._playlist[index])
        return ""

    # ==================== 播放控制 ====================

    def play(self):
        if self._current_index < 0 or self._current_index >= len(self._playlist):
            self.error_occurred.emit("播放列表为空")
            return

        self.track_changed.emit(self._current_index)

        file_path = self._playlist[self._current_index]
        try:
            if self._loaded_path != file_path:
                self._playback.load_file(file_path)
                self._loaded_path = file_path
                self.duration_changed.emit(self._playback.duration)

            # 切歌时同步激活组
            group_idx = self.get_group_of_track(self._current_index)
            if group_idx >= 0 and group_idx != self._active_group_index:
                self._active_group_index = group_idx
                self.active_group_changed.emit(group_idx)

            self._playback.loop_at_end(self._play_mode == PlayMode.SINGLE_LOOP)
            self._playback.play()
            self._was_playing = True
            self.state_changed.emit(True)
        except Exception as e:
            self.error_occurred.emit(f"播放失败: {e}")

    def pause(self):
        if self._playback.playing:
            self._playback.pause()
            self.state_changed.emit(False)

    def resume(self):
        if self._playback.paused:
            self._playback.resume()
            self.state_changed.emit(True)

    def toggle_play_pause(self):
        if self._playback.playing:
            self.pause()
        elif self._playback.paused:
            self.resume()
        elif self._current_index >= 0:
            self.play()

    def stop(self):
        self._playback.stop()
        self._was_playing = False
        self._loaded_path = None
        self.state_changed.emit(False)
        self.position_changed.emit(0.0)

    def next_track(self):
        if not self._playlist:
            return
        start, end = self._get_play_range()
        if start >= end:
            return
        if self._current_index < 0:
            self._current_index = start
        else:
            self._current_index += 1
            if self._current_index >= end:
                if self._play_mode in (PlayMode.LIST_LOOP, PlayMode.CROSS_LIST_LOOP):
                    self._current_index = start
                else:
                    self._current_index = end - 1
                    self.stop()
                    return
        self.track_changed.emit(self._current_index)
        self.play()

    def prev_track(self):
        if not self._playlist:
            return
        start, end = self._get_play_range()
        if start >= end:
            return
        if self._current_index < 0:
            self._current_index = end - 1
        else:
            self._current_index -= 1
            if self._current_index < start:
                if self._play_mode in (PlayMode.LIST_LOOP, PlayMode.CROSS_LIST_LOOP):
                    self._current_index = end - 1
                else:
                    self._current_index = start
        self.track_changed.emit(self._current_index)
        self.play()

    def play_track_at(self, index: int):
        if 0 <= index < len(self._playlist):
            self._current_index = index
            self.track_changed.emit(self._current_index)
            self.play()

    # ==================== 进度与音量 ====================

    def seek(self, position_sec: float):
        if self._playback.active and self._playback.duration > 0:
            position_sec = max(0.0, min(position_sec, self._playback.duration))
            self._playback.seek(position_sec)
            self.position_changed.emit(position_sec)

    def get_current_position(self) -> float:
        return max(0.0, self._playback.curr_pos) if self._playback.active else 0.0

    def get_duration(self) -> float:
        return self._playback.duration if self._playback.duration > 0 else 0.0

    def set_volume(self, volume: float):
        volume = max(0.0, min(1.0, volume))
        self._playback.set_volume(volume)
        self.volume_changed.emit(volume)

    def get_volume(self) -> float:
        return self._playback.volume

    # ==================== 播放模式 ====================

    def set_play_mode(self, mode: PlayMode):
        self._play_mode = mode
        self._playback.loop_at_end(mode == PlayMode.SINGLE_LOOP)
        self.play_mode_changed.emit(mode)

    def get_play_mode(self) -> PlayMode:
        return self._play_mode

    def get_current_index(self) -> int:
        return self._current_index

    def is_playing(self) -> bool:
        return self._playback.playing

    def is_paused(self) -> bool:
        return self._playback.paused

    # ==================== 内部轮询 ====================

    def _on_tick(self):
        if self._playback.active:
            self.position_changed.emit(max(0.0, self._playback.curr_pos))

        was_playing = self._was_playing
        now_playing = self._playback.playing
        now_paused = self._playback.paused
        self._was_playing = now_playing

        if was_playing and not now_playing and not now_paused:
            self.state_changed.emit(False)
            self.position_changed.emit(0.0)

            if self._play_mode == PlayMode.SINGLE_LOOP:
                if self._current_index >= 0:
                    self.play()
            else:
                QTimer.singleShot(200, self._handle_track_finished)

    def _handle_track_finished(self):
        start, end = self._get_play_range()
        if self._play_mode in (PlayMode.LIST_LOOP, PlayMode.CROSS_LIST_LOOP):
            self.next_track()
        elif self._play_mode in (PlayMode.SEQUENCE, PlayMode.CROSS_SEQUENCE):
            if start <= self._current_index < end - 1:
                self.next_track()
            else:
                self.stop()
