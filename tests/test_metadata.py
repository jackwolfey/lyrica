"""元数据读取与缓存测试。"""

import types
import wave

import pytest

import src.player as player_mod


def tag(title=None, artist=None, album=None, duration=None):
    return types.SimpleNamespace(title=title, artist=artist, album=album, duration=duration)


def patch_tags(monkeypatch, fn):
    monkeypatch.setattr(player_mod.TinyTag, "get", staticmethod(fn))


def test_get_track_metadata_reads_and_caches(player, monkeypatch):
    calls = []

    def fake_get(path):
        calls.append(path)
        return tag("Song", "Alice", "Demo", 240.5)

    patch_tags(monkeypatch, fake_get)

    first = player.get_track_metadata("/music/a.mp3")
    second = player.get_track_metadata("/music/a.mp3")

    assert first == {"title": "Song", "artist": "Alice", "album": "Demo", "duration": "240.5"}
    assert second is first
    assert calls == ["/music/a.mp3"]  # 第二次命中缓存，不再 IO


def test_missing_duration_stays_none(player, monkeypatch):
    patch_tags(monkeypatch, lambda _path: tag("Song", None, None, None))

    meta = player.get_track_metadata("/music/a.mp3")

    assert meta["duration"] is None
    assert meta["artist"] is None


def test_read_failure_returns_empty_meta_and_caches(player, monkeypatch):
    calls = []

    def boom(path):
        calls.append(path)
        raise OSError("bad file")

    patch_tags(monkeypatch, boom)

    meta = player.get_track_metadata("/music/broken.mp3")

    assert meta == {"title": None, "artist": None, "album": None, "duration": None}
    # 失败结果同样缓存，避免反复重试坏文件
    player.get_track_metadata("/music/broken.mp3")
    assert calls == ["/music/broken.mp3"]


def test_preload_does_not_overwrite_existing(player):
    player._meta_cache["/music/a.mp3"] = {"title": "Cached", "artist": None, "album": None, "duration": None}

    player.preload_metadata({
        "/music/a.mp3": {"title": "New", "artist": None, "album": None, "duration": None},
        "/music/b.mp3": {"title": "B", "artist": None, "album": None, "duration": None},
    })

    assert player.get_track_metadata("/music/a.mp3")["title"] == "Cached"
    assert player.get_track_metadata("/music/b.mp3")["title"] == "B"


def test_get_current_track_metadata(player, three_tracks, monkeypatch):
    player.add_files(three_tracks)
    patch_tags(monkeypatch, lambda path: tag("T", "A", "B", 1.0))
    player._meta_cache.clear()

    meta = player.get_current_track_metadata()

    assert meta["title"] == "T"


def test_get_current_track_metadata_without_playlist(player):
    assert player.get_current_track_metadata() == {
        "title": None, "artist": None, "album": None, "duration": None,
    }


def test_sort_by_title_uses_preloaded_cache_without_touching_disk(player, tmp_path, monkeypatch):
    paths = []
    for name in ("x.mp3", "y.mp3"):
        path = tmp_path / name
        path.write_bytes(b"")
        paths.append(str(path))
    player.add_files(paths)
    player.preload_metadata({
        paths[0]: {"title": "Zulu", "artist": None, "album": None, "duration": None},
        paths[1]: {"title": "Alpha", "artist": None, "album": None, "duration": None},
    })

    def must_not_call(_path):
        raise AssertionError("排序不应触发 TinyTag IO")

    patch_tags(monkeypatch, must_not_call)

    player.sort_group_tracks(0, "title")

    assert [p for p in player.get_playlist()] == [paths[1], paths[0]]


def test_real_tinytag_reads_wav_duration(player, tmp_path):
    wav = tmp_path / "sample.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 4000)  # 0.5 秒静音

    meta = player.get_track_metadata(str(wav))

    assert meta["duration"] is not None
    assert 0.4 <= float(meta["duration"]) <= 0.6
    assert meta["title"] is None  # 未打标签的 WAV
