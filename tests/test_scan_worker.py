"""后台扫描线程 ScanWorker 测试。"""

import os
import types

import pytest

from src.main_window import ScanWorker


def run_worker(worker):
    """在当前线程同步执行 run()，并收集信号。"""
    result = {"ok": [], "failed": []}
    worker.finished_ok.connect(lambda folder, count, cache: result["ok"].append((folder, count, cache)))
    worker.failed.connect(lambda msg: result["failed"].append(msg))
    worker.run()
    return result


def tag(duration=10.0):
    return types.SimpleNamespace(title="T", artist="A", album="B", duration=duration)


@pytest.mark.parametrize("name", ["a.mp3", "b.wav", "c.flac", "d.ogg", "e.m4a", "f.aac", "g.wma", "h.opus"])
def test_scan_worker_supports_each_extension(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"")

    result = run_worker(ScanWorker(str(tmp_path)))

    assert len(result["ok"]) == 1
    folder, count, cache = result["ok"][0]
    assert folder == os.path.abspath(str(tmp_path))
    assert count == 1
    assert list(cache) == [str(path)]


def test_scan_worker_recurses_and_skips_non_audio(make_tree):
    base = make_tree({"Album": ["a.mp3", "cover.jpg"], "Album/Disc2": ["b.flac"]})

    result = run_worker(ScanWorker(str(base)))

    _, count, cache = result["ok"][0]
    assert count == 2
    assert sorted(cache) == sorted([
        os.path.abspath(str(base / "Album" / "a.mp3")),
        os.path.abspath(str(base / "Album" / "Disc2" / "b.flac")),
    ])


def _strip_meta(meta: dict) -> dict:
    """去掉 ScanWorker 返回的 _mtime 内部字段，只保留 metadata 内容。"""
    return {k: v for k, v in meta.items() if not k.startswith("_")}


def test_scan_worker_reuses_existing_cache(tmp_path, monkeypatch):
    paths = []
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        path = tmp_path / name
        path.write_bytes(b"")
        paths.append(str(path))

    cached_meta = {"title": "Cached", "artist": None, "album": None, "duration": None}
    existing = {paths[0]: cached_meta}

    read = []

    def fake_get(path):
        read.append(path)
        return tag(11.11)

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(fake_get))

    result = run_worker(ScanWorker(str(tmp_path), existing))

    _, count, cache = result["ok"][0]
    assert count == 3
    assert _strip_meta(cache[paths[0]]) == cached_meta  # 命中缓存，metadata 内容原样保留
    assert sorted(read) == sorted(paths[1:])


def test_scan_worker_formats_duration(tmp_path, monkeypatch):
    (tmp_path / "a.mp3").write_bytes(b"")
    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(lambda _p: tag(12.34)))

    result = run_worker(ScanWorker(str(tmp_path)))

    _, _, cache = result["ok"][0]
    meta = next(iter(cache.values()))
    assert _strip_meta(meta) == {"title": "T", "artist": "A", "album": "B", "duration": "12.3"}


def test_scan_worker_handles_unreadable_tags(tmp_path, monkeypatch):
    (tmp_path / "a.mp3").write_bytes(b"")

    def boom(_path):
        raise RuntimeError("broken tag")

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(boom))

    result = run_worker(ScanWorker(str(tmp_path)))

    _, count, cache = result["ok"][0]
    assert count == 1
    assert cache[str(tmp_path / "a.mp3")] == {
        "title": None, "artist": None, "album": None, "duration": None,
    }


def test_scan_worker_empty_folder(tmp_path):
    result = run_worker(ScanWorker(str(tmp_path)))

    folder, count, cache = result["ok"][0]
    assert count == 0
    assert cache == {}
    assert folder == os.path.abspath(str(tmp_path))


def test_scan_worker_emits_failed_on_error(tmp_path, monkeypatch):
    def boom(_path):
        raise OSError("permission denied")

    monkeypatch.setattr("src.main_window.os.walk", boom)

    result = run_worker(ScanWorker(str(tmp_path)))

    assert result["ok"] == []
    assert result["failed"] == ["permission denied"]


def test_scan_worker_runs_as_real_qthread(qapp, tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"")
    (tmp_path / "b.wav").write_bytes(b"")

    worker = ScanWorker(str(tmp_path))
    result = {"ok": [], "failed": []}
    worker.finished_ok.connect(lambda folder, count, cache: result["ok"].append((folder, count, cache)))
    worker.failed.connect(lambda msg: result["failed"].append(msg))

    worker.start()
    assert worker.wait(10000)
    qapp.processEvents()

    assert len(result["ok"]) == 1
    assert result["ok"][0][1] == 2
    assert result["failed"] == []
