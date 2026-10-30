"""播放组数据模型测试（文件夹 → PlaylistGroup → 平铺列表）。"""

import os

import pytest

from src.player import PlayMode, PlaylistGroup


def capture(signal):
    calls = []
    signal.connect(calls.append)  # 单参数信号：int / object
    return calls


def capture_flag(signal):
    """无参数信号的收集器。"""
    calls = []
    signal.connect(lambda: calls.append(True))
    return calls


def write_album(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(b"")
        paths.append(str(path))
    return paths


# ==================== 扫描 / 添加 ====================


def test_add_folder_recursive_creates_one_group_per_folder(player, make_tree):
    base = make_tree({
        "AlbumA": ["a1.mp3", "a2.flac"],
        "AlbumA/Disc2": ["d1.ogg"],
        "AlbumB": ["b1.wav", "notes.txt"],
    })

    added = player.add_folder_recursive(str(base))

    assert added == 4
    assert player.get_group_count() == 3
    names = [player._groups[i].name for i in range(3)]
    assert names == ["AlbumA", "Disc2", "AlbumB"]
    assert player.get_group_track_count(0) == 2
    assert player.get_group_track_count(1) == 1
    assert player.get_group_track_count(2) == 1
    # 首次添加后自动激活第 0 组并选中第 0 首
    assert player.get_active_group_index() == 0
    assert player.get_current_index() == 0
    assert player.get_track_count() == 4


def test_add_folder_ignores_non_audio(player, tmp_path):
    folder = tmp_path / "docs"
    write_album(folder, ["readme.txt", "cover.jpg"])

    assert player.add_folder_recursive(str(folder)) == 0
    assert player.get_group_count() == 0


def test_add_folder_missing_path(player, tmp_path):
    assert player.add_folder_recursive(str(tmp_path / "nope")) == 0
    assert player.get_group_count() == 0


def test_add_folder_twice_is_idempotent(player, make_tree):
    base = make_tree({"Album": ["a.mp3", "b.mp3"]})

    assert player.add_folder_recursive(str(base)) == 2
    assert player.add_folder_recursive(str(base)) == 0
    assert player.get_group_count() == 1
    assert player.get_track_count() == 2


def test_scan_folder_flat_filters_and_sorts(player, tmp_path):
    folder = tmp_path / "album"
    write_album(folder, ["b.mp3", "A.MP3", "z.txt", "noext"])

    tracks = player._scan_folder_flat(str(folder))

    # 扩展名大小写不敏感，且按文件名排序
    assert [os.path.basename(t) for t in tracks] == ["A.MP3", "b.mp3"]
    assert all(os.path.isabs(t) for t in tracks)


def test_scan_folder_flat_handles_unreadable_dir(player, tmp_path):
    # 不存在的目录：OSError 被吞掉，返回空列表
    assert player._scan_folder_flat(str(tmp_path / "missing")) == []


# ==================== 查询接口 ====================


def test_group_name_includes_track_count(player, make_tree):
    player.add_folder_recursive(str(make_tree({"Album": ["a.mp3", "b.mp3"]})))

    assert player.get_group_name(0) == "Album (2)"
    assert player.get_group_name(9) == ""


def test_get_group_of_track_boundaries(player, make_tree):
    player.add_folder_recursive(str(make_tree({
        "A": ["a1.mp3", "a2.mp3"],
        "B": ["b1.mp3"],
    })))

    assert player.get_group_of_track(0) == 0
    assert player.get_group_of_track(1) == 0
    assert player.get_group_of_track(2) == 1
    assert player.get_group_of_track(3) == -1
    assert player.get_group_of_track(-1) == -1


def test_get_track_index_in_group(player, make_tree):
    player.add_folder_recursive(str(make_tree({
        "A": ["a1.mp3", "a2.mp3"],
        "B": ["b1.mp3"],
    })))

    assert player.get_track_index_in_group(0) == 0
    assert player.get_track_index_in_group(2) == 0
    assert player.get_track_index_in_group(3) == -1


def test_get_group_tracks_returns_copy(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))

    tracks = player.get_group_tracks(0)
    tracks.clear()

    assert player.get_group_track_count(0) == 1  # 副本被清空不影响内部


def test_group_accessors_with_invalid_index(player):
    assert player.get_group_tracks(3) == []
    assert player.get_group_track_count(3) == 0


def test_get_track_name(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["song.mp3"]})))

    assert player.get_track_name(0) == "song.mp3"
    assert player.get_track_name(5) == ""


def test_play_active_group_next(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"], "B": ["b.mp3"]})))
    player.set_active_group(1)

    player.play_active_group_next()

    assert player._playback.path == player.get_playlist()[1]
    assert player._playback.playing


def test_play_track_at(player, three_tracks):
    player.add_files(three_tracks)

    player.play_track_at(2)

    assert player.get_current_index() == 2
    assert player._playback.playing


def test_add_folder_alias(player, make_tree):
    base = make_tree({"A": ["a.mp3"]})

    assert player.add_folder(str(base)) == 1
    assert player.get_group_count() == 1


def test_clear_playlist_alias(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"]})))

    player.clear_playlist()

    assert player.get_group_count() == 0


def test_remove_track_invalid_index(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"]})))

    player.remove_track(99)

    assert player.get_track_count() == 1


def test_reset_group_order_single_group_is_noop(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"]})))
    events = capture_flag(player.groups_changed)

    player.reset_group_order()

    assert events == []


def test_sort_unknown_key_falls_back_to_filename(player, tmp_path):
    paths = write_album(tmp_path / "A", ["b.mp3", "a.mp3"])
    player.add_files(paths)
    player._groups[0].tracks = [paths[0], paths[1]]
    player._rebuild_flat_playlist()

    player.sort_group_tracks(0, "unknown")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["a.mp3", "b.mp3"]


def test_get_playlists_for_save_only_stores_paths(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))

    assert player.get_playlists_for_save() == [{"folder_path": player._groups[0].folder_path}]


# ==================== 激活组 ====================


def test_set_active_group_emits_for_valid_index(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"], "B": ["b1.mp3"]})))
    events = capture(player.active_group_changed)

    player.set_active_group(1)
    assert events == [1]
    assert player.get_active_group_index() == 1

    player.set_active_group(5)
    assert events == [1]  # 越界不改变、不发信号


def test_play_track_in_group_sets_state_and_plays(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3", "a2.mp3"], "B": ["b1.mp3"]})))
    active_events = capture(player.active_group_changed)
    track_events = capture(player.track_changed)

    player.play_track_in_group(1, 0)

    assert player.get_active_group_index() == 1
    assert player.get_current_index() == 2
    assert active_events == [1]
    assert track_events == [2]
    assert player._playback.playing
    assert player._playback.path == player.get_playlist()[2]


def test_play_track_in_group_invalid_is_noop(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))
    player._playback.ops.clear()

    player.play_track_in_group(4, 0)
    player.play_track_in_group(0, 9)

    assert player._playback.ops == []
    assert player.get_current_index() == 0


# ==================== 移除 / 清空 ====================


def test_remove_group_shifts_current_index(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3", "a2.mp3"], "B": ["b1.mp3", "b2.mp3"]})))
    playing_path = player.get_playlist()[3]
    player._current_index = 3
    player.set_active_group(1)
    player._playback.ops.clear()

    player.remove_group(0)

    assert player.get_group_count() == 1
    assert player.get_current_index() == 1
    assert player.get_playlist()[1] == playing_path
    assert player.get_active_group_index() == 0
    # 当前曲目不属于被删的组，不应停止播放
    assert "stop" not in player._playback.ops


def test_remove_group_containing_current_track_stops(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3", "a2.mp3"], "B": ["b1.mp3"]})))
    player._current_index = 0
    player.set_active_group(0)

    player.remove_group(0)

    assert player.get_group_count() == 1
    assert player.get_current_index() == 0
    assert "stop" in player._playback.ops


def test_remove_group_clamps_overflowing_current_index(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"], "B": ["b1.mp3", "b2.mp3"]})))
    player._current_index = 2  # B 的最后一首
    player.set_active_group(1)

    player.remove_group(1)  # 删除当前所在且是最后一组

    assert player.get_group_count() == 1
    assert player.get_active_group_index() == 0  # 越界后回落到最后一组
    assert player.get_current_index() == 0
    assert player.get_track_count() == 1


def test_remove_group_out_of_range(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))

    player.remove_group(3)

    assert player.get_group_count() == 1


def test_clear_groups_resets_everything(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))
    track_events = capture(player.track_changed)

    player.clear_groups()

    assert player.get_group_count() == 0
    assert player.get_track_count() == 0
    assert player.get_active_group_index() == -1
    assert player.get_current_index() == -1
    assert track_events == [-1]


# ==================== 组排序 / 移动 ====================


def _group_paths(player):
    return [g.folder_path for g in player._groups]


def test_move_group_up_preserves_playing_track(player, make_tree):
    player.add_folder_recursive(str(make_tree({
        "A": ["a.mp3"],
        "B": ["b.mp3"],
        "C": ["c.mp3"],
    })))
    player.play_track_in_group(2, 0)  # 播放 C
    playing_path = player.get_playlist()[player.get_current_index()]

    player.move_group_up(2)  # A C B

    assert [os.path.basename(p) for p in _group_paths(player)] == ["A", "C", "B"]
    assert player._active_group_index == 1
    assert player.get_playlist()[player.get_current_index()] == playing_path


def test_move_group_down_preserves_playing_track(player, make_tree):
    player.add_folder_recursive(str(make_tree({
        "A": ["a.mp3"],
        "B": ["b.mp3"],
    })))
    player.play_track_in_group(1, 0)
    playing_path = player.get_playlist()[player.get_current_index()]

    player.move_group_down(0)

    assert [os.path.basename(p) for p in _group_paths(player)] == ["B", "A"]
    assert player._active_group_index == 0
    assert player.get_playlist()[player.get_current_index()] == playing_path


def test_move_group_boundaries_are_noops(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3"], "B": ["b.mp3"]})))
    before = _group_paths(player)

    player.move_group_up(0)
    player.move_group_down(1)

    assert _group_paths(player) == before


def test_reset_group_order_sorts_by_folder_path(player, tmp_path):
    a = write_album(tmp_path / "A", ["a.mp3"])
    b = write_album(tmp_path / "B", ["b.mp3"])
    c = write_album(tmp_path / "C", ["c.mp3"])
    player.add_files([b[0], c[0], a[0]])  # 插入顺序 B C A

    assert [os.path.basename(p) for p in _group_paths(player)] == ["B", "C", "A"]

    group_events = capture_flag(player.groups_changed)
    player.reset_group_order()

    assert [os.path.basename(p) for p in _group_paths(player)] == ["A", "B", "C"]
    assert group_events


# ==================== 曲目排序 ====================


def test_sort_by_filename_is_case_insensitive_and_keeps_current(player, tmp_path):
    a = write_album(tmp_path / "A", ["b.mp3", "A.mp3"])
    player.add_files(a)
    player._groups[0].tracks = [a[0], a[1]]  # 手动打乱：b, A
    player._rebuild_flat_playlist()
    player._current_index = 0
    playing_path = player.get_playlist()[0]

    player.sort_group_tracks(0, "filename")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["A.mp3", "b.mp3"]
    assert player.get_playlist()[player.get_current_index()] == playing_path


def test_sort_by_title_uses_metadata_and_keeps_current(player, tmp_path):
    paths = write_album(tmp_path / "A", ["x.mp3", "y.mp3", "z.mp3"])
    player.add_files(paths)
    titles = {paths[0]: "Bravo", paths[1]: "alpha", paths[2]: "Charlie"}
    player.preload_metadata({p: {"title": t, "artist": None, "album": None, "duration": None}
                             for p, t in titles.items()})
    player._current_index = 0
    playing_path = player.get_playlist()[0]

    player.sort_group_tracks(0, "title")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["y.mp3", "x.mp3", "z.mp3"]
    assert player.get_playlist()[player.get_current_index()] == playing_path


def test_sort_by_title_falls_back_to_filename_stem(player, tmp_path):
    paths = write_album(tmp_path / "A", ["Zeta.mp3", "alpha.mp3"])
    player.add_files(paths)
    player.preload_metadata({p: {"title": None, "artist": None, "album": None, "duration": None}
                             for p in paths})

    player.sort_group_tracks(0, "title")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["alpha.mp3", "Zeta.mp3"]


def test_sort_by_artist_puts_missing_artist_first(player, tmp_path):
    paths = write_album(tmp_path / "A", ["a.mp3", "b.mp3"])
    player.add_files(paths)
    player.preload_metadata({
        paths[0]: {"title": None, "artist": "Zed", "album": None, "duration": None},
        paths[1]: {"title": None, "artist": None, "album": None, "duration": None},
    })

    player.sort_group_tracks(0, "artist")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["b.mp3", "a.mp3"]


def test_sort_by_album(player, tmp_path):
    paths = write_album(tmp_path / "A", ["a.mp3", "b.mp3"])
    player.add_files(paths)
    player.preload_metadata({
        paths[0]: {"title": None, "artist": None, "album": "Zoo", "duration": None},
        paths[1]: {"title": None, "artist": None, "album": "Alpha", "duration": None},
    })

    player.sort_group_tracks(0, "album")

    assert [os.path.basename(t) for t in player.get_playlist()] == ["b.mp3", "a.mp3"]


def test_sort_invalid_group_is_noop(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a.mp3", "b.mp3"]})))
    before = player.get_playlist()

    player.sort_group_tracks(5, "title")

    assert player.get_playlist() == before


# ==================== add_files / remove_track / restore ====================


def test_add_files_groups_by_parent_and_dedupes(player, tmp_path):
    a = write_album(tmp_path / "A", ["a1.mp3", "a2.flac"])
    b = write_album(tmp_path / "B", ["b1.wav"])

    added = player.add_files([a[0], a[1], b[0], a[0]])

    assert added == 3
    assert player.get_group_count() == 2
    assert player.get_active_group_index() == 1  # 首次插入时激活最后一组
    assert player.get_current_index() == 0

    assert player.add_files([a[0], b[0]]) == 0  # 全部去重


def test_add_files_ignores_unsupported_and_missing(player, tmp_path):
    folder = tmp_path / "A"
    folder.mkdir()
    txt = folder / "note.txt"
    txt.write_text("x")

    assert player.add_files([str(txt), str(folder / "ghost.mp3")]) == 0
    assert player.get_group_count() == 0


def test_remove_track_from_group(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3", "a2.mp3"]})))
    player._current_index = 0

    player.remove_track(0)

    assert player.get_track_count() == 1
    assert player.get_group_track_count(0) == 1
    assert "stop" in player._playback.ops


def test_remove_last_track_removes_group(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3"]})))

    player.remove_track(0)

    assert player.get_group_count() == 0
    assert player.get_track_count() == 0


def test_rebuild_flat_playlist_clamps_current_index(player, make_tree):
    player.add_folder_recursive(str(make_tree({"A": ["a1.mp3", "a2.mp3"]})))
    player._current_index = 99

    player._rebuild_flat_playlist()

    assert player.get_current_index() == 1


def test_rebuild_flat_playlist_empty(player):
    player._current_index = 3

    player._rebuild_flat_playlist()

    assert player.get_current_index() == -1


def test_restore_groups_rescans_and_skips_missing(player, tmp_path):
    a = write_album(tmp_path / "A", ["a1.mp3", "a2.mp3"])
    b = write_album(tmp_path / "B", ["b1.mp3"])
    saved = [
        {"folder_path": str(tmp_path / "A")},
        {"folder_path": str(tmp_path / "gone")},
        {"folder_path": str(tmp_path / "B")},
    ]

    total = player.restore_groups(saved)

    assert total == 3
    assert player.get_group_count() == 2
    assert player.get_active_group_index() == 0
    assert player.get_current_index() == 0
    assert player.get_playlist()[0] == a[0]
    assert player.get_playlist()[2] == b[0]


def test_restore_groups_empty_resets(player):
    player.add_files([str(__file__)])  # 不支持的类型，保持空

    assert player.restore_groups([]) == 0
    assert player.get_group_count() == 0
    assert player.get_active_group_index() == -1
    assert player.get_current_index() == -1


def test_restore_groups_emits_groups_changed(player, tmp_path):
    write_album(tmp_path / "A", ["a.mp3"])
    events = capture_flag(player.groups_changed)

    player.restore_groups([{"folder_path": str(tmp_path / "A")}])

    assert events


def test_playlist_group_to_dict():
    group = PlaylistGroup(name="A", folder_path="/music/A", tracks=["/music/A/a.mp3"])

    assert group.to_dict() == {"folder_path": "/music/A"}


def test_play_mode_values():
    assert PlayMode.SEQUENCE.value == "组内顺序"
    assert PlayMode.LIST_LOOP.value == "组内循环"
    assert PlayMode.SINGLE_LOOP.value == "单曲循环"
    assert PlayMode.CROSS_SEQUENCE.value == "跨组顺序"
    assert PlayMode.CROSS_LIST_LOOP.value == "跨组循环"
