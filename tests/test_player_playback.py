"""播放控制状态机测试（上一曲/下一曲/循环/进度/音量/恢复位置）。"""

import pytest

import src.player as player_mod
from src.player import PlayMode


class TimerStub:
    """替换 QTimer，仅提供 singleShot 并记录回调（不真正延时）。"""

    calls = []

    @staticmethod
    def singleShot(msec, callback):
        TimerStub.calls.append((msec, callback))


def capture(signal):
    calls = []
    signal.connect(calls.append)
    return calls


@pytest.fixture
def loaded(player, three_tracks):
    player.add_files(three_tracks)
    return player


# ==================== 播放 / 暂停 / 停止 ====================


def test_play_with_empty_playlist_emits_error(player):
    errors = capture(player.error_occurred)

    player.play()

    assert errors == ["播放列表为空"]


def test_play_loads_file_and_emits_signals(loaded):
    durations = capture(loaded.duration_changed)
    states = capture(loaded.state_changed)
    tracks = capture(loaded.track_changed)

    loaded.play()

    assert loaded._playback.path == loaded.get_playlist()[0]
    assert durations == [180.0]
    assert states == [True]
    assert tracks == [0]
    assert loaded._playback.playing


def test_play_same_file_does_not_reload(loaded):
    loaded.play()
    loaded.pause()
    loaded.play()

    assert loaded._playback.ops.count("load") == 1


def test_play_switches_active_group(loaded, make_tree):
    # loaded 只有一个组；再加一个组后播放其曲目，验证激活组跟随
    base = make_tree({"B": ["d.mp3"]})
    loaded.add_folder_recursive(str(base))
    active = capture(loaded.active_group_changed)

    loaded.play_track_in_group(loaded.get_group_count() - 1, 0)

    assert active == [loaded.get_group_count() - 1]
    assert loaded.get_active_group_index() == loaded.get_group_count() - 1


def test_play_syncs_active_group_when_index_moved_externally(loaded, make_tree):
    base = make_tree({"B": ["d.mp3"]})
    loaded.add_folder_recursive(str(base))
    loaded.set_active_group(0)
    loaded._current_index = loaded.get_track_count() - 1  # 直接指向 B 的曲目
    active = capture(loaded.active_group_changed)

    loaded.play()

    assert active == [loaded.get_group_count() - 1]
    assert loaded.get_active_group_index() == loaded.get_group_count() - 1


def test_pause_requires_playing(loaded):
    states = capture(loaded.state_changed)

    loaded.pause()  # 未播放时不做任何事
    assert states == []

    loaded.play()
    loaded.pause()
    assert states == [True, False]
    assert loaded._playback.paused


def test_resume_only_when_paused(loaded):
    loaded.resume()  # 未暂停 → 无操作
    assert loaded._playback.ops == []

    loaded.play()
    loaded.pause()
    loaded.resume()
    assert loaded._playback.playing
    assert not loaded._playback.paused


def test_toggle_play_pause_three_states(loaded):
    loaded.toggle_play_pause()  # 停止 → 播放
    assert loaded._playback.playing

    loaded.toggle_play_pause()  # 播放 → 暂停
    assert loaded._playback.paused

    loaded.toggle_play_pause()  # 暂停 → 继续
    assert loaded._playback.playing


def test_play_load_error_emits_error(loaded, monkeypatch):
    def boom(_path):
        raise RuntimeError("unsupported codec")

    monkeypatch.setattr(loaded._playback, "load_file", boom)
    errors = capture(loaded.error_occurred)

    loaded.play()

    assert errors == ["播放失败: unsupported codec"]


def test_is_playing_and_is_paused(loaded):
    assert loaded.is_playing() is False
    assert loaded.is_paused() is False

    loaded.play()
    assert loaded.is_playing() is True

    loaded.pause()
    assert loaded.is_paused() is True


def test_stop_resets_playback_state(loaded):
    states = capture(loaded.state_changed)
    positions = capture(loaded.position_changed)
    loaded.play()

    loaded.stop()

    assert states[-1] is False
    assert positions[-1] == 0.0
    assert not loaded._playback.active
    assert loaded._loaded_path is None


# ==================== 上一曲 / 下一曲 ====================


def test_next_track_advances(loaded):
    loaded.play()
    events = capture(loaded.track_changed)

    loaded.next_track()

    assert loaded.get_current_index() == 1
    assert events and events[-1] == 1


def test_next_track_from_invalid_index_starts_at_zero(loaded):
    loaded._current_index = -1

    loaded.next_track()

    assert loaded.get_current_index() == 0


def test_next_track_at_end_sequence_stops(loaded):
    loaded._current_index = loaded.get_track_count() - 1
    loaded.play()

    loaded.next_track()

    assert loaded.get_current_index() == loaded.get_track_count() - 1
    assert not loaded._playback.playing
    assert "stop" in loaded._playback.ops


def test_next_track_at_end_list_loop_wraps(loaded):
    loaded.set_play_mode(PlayMode.LIST_LOOP)
    loaded._current_index = loaded.get_track_count() - 1

    loaded.next_track()

    assert loaded.get_current_index() == 0
    assert loaded._playback.playing


def test_prev_track_at_start_sequence_stays(loaded):
    loaded._current_index = 0

    loaded.prev_track()

    assert loaded.get_current_index() == 0
    assert loaded._playback.playing


def test_prev_track_at_start_list_loop_wraps(loaded):
    loaded.set_play_mode(PlayMode.LIST_LOOP)
    loaded._current_index = 0

    loaded.prev_track()

    assert loaded.get_current_index() == loaded.get_track_count() - 1


def test_prev_track_from_invalid_index_starts_at_end_of_range(loaded):
    """prev_track 从无效索引(-1) 起，应该落到当前播放范围的最后一首。
    loaded fixture 有 3 首、1 个组、active_group=0 → 范围 (0,3) → end-1=2"""
    loaded._current_index = -1

    loaded.prev_track()

    assert loaded.get_current_index() == 2


def test_prev_track_decrements(loaded):
    loaded._current_index = 2

    loaded.prev_track()

    assert loaded.get_current_index() == 1


def test_next_prev_with_empty_playlist(player):
    player.next_track()
    player.prev_track()

    assert player.get_current_index() == -1


# ==================== 进度 ====================


def test_seek_requires_active_playback(loaded):
    positions = capture(loaded.position_changed)

    loaded.seek(10.0)

    assert positions == []
    assert loaded._playback.curr_pos == 0.0


def test_seek_clamps_into_track(loaded):
    loaded.play()
    positions = capture(loaded.position_changed)

    loaded.seek(-5.0)
    assert loaded._playback.curr_pos == 0.0

    loaded.seek(9999.0)
    assert loaded._playback.curr_pos == 180.0
    assert positions[-1] == 180.0


def test_get_current_position_active_and_inactive(loaded):
    assert loaded.get_current_position() == 0.0  # 未加载

    loaded.play()
    loaded._playback._curr_pos = 33.5
    assert loaded.get_current_position() == 33.5

    loaded._playback._curr_pos = -2.0
    assert loaded.get_current_position() == 0.0  # 负值被夹到 0


def test_get_duration(loaded):
    assert loaded.get_duration() == 0.0
    loaded.play()
    assert loaded.get_duration() == 180.0


# ==================== 音量 / 播放模式 ====================


@pytest.mark.parametrize("raw,expected", [(1.5, 1.0), (-0.2, 0.0), (0.42, 0.42)])
def test_set_volume_clamps(loaded, raw, expected):
    volumes = capture(loaded.volume_changed)

    loaded.set_volume(raw)

    assert loaded.get_volume() == expected
    assert volumes == [expected]


def test_set_play_mode_updates_loop_flag(loaded):
    events = capture(loaded.play_mode_changed)

    loaded.set_play_mode(PlayMode.SINGLE_LOOP)
    assert loaded._playback.loop_enabled is True
    assert events[-1] is PlayMode.SINGLE_LOOP

    loaded.set_play_mode(PlayMode.LIST_LOOP)
    assert loaded._playback.loop_enabled is False
    assert loaded.get_play_mode() == PlayMode.LIST_LOOP


# ==================== 轮询 / 曲目结束 ====================


def test_tick_emits_position_while_active(loaded):
    loaded.play()
    loaded._playback._curr_pos = 42.0
    positions = capture(loaded.position_changed)

    loaded._on_tick()

    assert positions == [42.0]


def test_tick_does_nothing_when_inactive(player):
    positions = capture(player.position_changed)
    states = capture(player.state_changed)

    player._on_tick()

    assert positions == []
    assert states == []


def test_tick_single_loop_restarts_same_track(loaded):
    loaded.set_play_mode(PlayMode.SINGLE_LOOP)
    loaded.play()
    loaded._playback.finish()
    states = capture(loaded.state_changed)

    loaded._on_tick()

    assert states[0] is False
    assert states[-1] is True  # 单曲循环重新开始播放
    assert loaded.get_current_index() == 0
    assert loaded._playback.playing
    assert loaded._playback.ops.count("play") >= 2


def test_tick_end_sequence_schedules_advance(loaded, monkeypatch):
    TimerStub.calls = []
    monkeypatch.setattr(player_mod, "QTimer", TimerStub)
    loaded.play()
    loaded._playback.finish()

    loaded._on_tick()

    assert len(TimerStub.calls) == 1
    assert TimerStub.calls[0][0] == 200
    assert loaded.get_current_index() == 0  # 回调尚未执行

    TimerStub.calls[0][1]()

    assert loaded.get_current_index() == 1
    assert loaded._playback.playing


def test_handle_track_finished_at_last_track_stops(loaded):
    loaded._current_index = loaded.get_track_count() - 1
    loaded.play()

    loaded._handle_track_finished()

    assert not loaded._playback.playing
    assert "stop" in loaded._playback.ops


def test_handle_track_finished_list_loop_wraps(loaded):
    loaded.set_play_mode(PlayMode.LIST_LOOP)
    loaded._current_index = loaded.get_track_count() - 1
    loaded.play()

    loaded._handle_track_finished()

    assert loaded.get_current_index() == 0


# ==================== 重启恢复位置 ====================


def test_restore_paused_position_runs_play_seek_pause(loaded):
    states = capture(loaded.state_changed)
    positions = capture(loaded.position_changed)

    assert loaded.restore_paused_position(50.0) is True

    assert loaded._playback.ops == ["load", "play", "seek", "pause"]
    assert loaded._playback.curr_pos == 50.0
    assert loaded._playback.paused
    assert states == [False]
    assert positions[-1] == 50.0


def test_restore_paused_position_clamps(loaded):
    loaded.restore_paused_position(9999.0)

    assert loaded._playback.curr_pos == 180.0


def test_restore_paused_position_without_track(player):
    assert player.restore_paused_position(10.0) is False


def test_restore_paused_position_skips_reload_when_same_file(loaded):
    loaded.play()
    loaded._playback.ops.clear()

    loaded.restore_paused_position(10.0)

    assert "load" not in loaded._playback.ops
    assert loaded._playback.curr_pos == 10.0


def test_restore_paused_position_handles_backend_error(loaded, monkeypatch):
    def boom(_path):
        raise RuntimeError("device busy")

    monkeypatch.setattr(loaded._playback, "load_file", boom)

    assert loaded.restore_paused_position(10.0) is False
