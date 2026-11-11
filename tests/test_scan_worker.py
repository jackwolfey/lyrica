"""后台扫描线程 ScanWorker 测试。"""

import os
import threading
import time
import types

import pytest

from src.main_window import ScanWorker


def run_worker(worker):
    """在当前线程同步执行 run()，并收集信号。"""
    result = {"ok": [], "failed": [], "progress": []}
    worker.finished_ok.connect(lambda folder, count, cache: result["ok"].append((folder, count, cache)))
    worker.failed.connect(lambda msg: result["failed"].append(msg))
    worker.progress.connect(lambda done, total: result["progress"].append((done, total)))
    worker.run()
    return result


def tag(duration=10.0):
    return types.SimpleNamespace(title="T", artist="A", album="B", duration=duration)


@pytest.mark.parametrize("name", ["a.mp3", "b.wav", "c.flac", "d.ogg", "e.opus"])
def test_scan_worker_supports_each_extension(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"")

    result = run_worker(ScanWorker(str(tmp_path)))

    assert len(result["ok"]) == 1
    folder, count, cache = result["ok"][0]
    assert folder == os.path.abspath(str(tmp_path))
    assert count == 1
    assert list(cache) == [str(path)]


def test_scan_worker_emits_scanned_before_reading_tags(tmp_path):
    """v1.1.4 渐进式：路径要在标签读完之前发出，UI 才能先建组。"""
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"")
    b.write_bytes(b"")

    order = []
    worker = ScanWorker(str(tmp_path))
    worker.scanned.connect(
        lambda folder, paths: order.append(("scanned", folder, sorted(paths))))
    worker.finished_ok.connect(
        lambda folder, count, cache: order.append(("ok", folder, count)))

    worker.run()

    assert [item[0] for item in order] == ["scanned", "ok"]
    _, folder, paths = order[0]
    assert folder == os.path.abspath(str(tmp_path))
    assert paths == sorted([str(a), str(b)])


@pytest.mark.parametrize("name", ["a.m4a", "b.aac", "c.wma"])
def test_scan_worker_skips_formats_backend_cannot_play(tmp_path, name):
    """m4a/aac/wma 不在 just_playback 的支持集合里，扫描阶段就应排除。"""
    path = tmp_path / name
    path.write_bytes(b"")

    result = run_worker(ScanWorker(str(tmp_path)))

    assert len(result["ok"]) == 1
    assert result["ok"][0][1] == 0


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

    def fake_get(path, **kwargs):
        read.append(path)
        return tag(11.11)

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(fake_get))

    result = run_worker(ScanWorker(str(tmp_path), existing))

    _, count, cache = result["ok"][0]
    assert count == 3
    assert _strip_meta(cache[paths[0]]) == cached_meta  # 命中缓存，metadata 内容原样保留
    assert cache[paths[0]]["_mtime"] is not None      # 老缓存缺的 _mtime 由扫描结果补齐
    assert sorted(read) == sorted(paths[1:])


def test_scan_worker_skips_duration_parsing(tmp_path, monkeypatch):
    """duration 只写不读却要逐帧扫整个 MP3，扫描阶段必须显式关掉。"""
    (tmp_path / "a.mp3").write_bytes(b"")
    seen = []

    def fake_get(path, **kwargs):
        seen.append(kwargs)
        return tag(12.34)

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(fake_get))

    result = run_worker(ScanWorker(str(tmp_path)))

    assert seen == [{"duration": False}]
    _, _, cache = result["ok"][0]
    meta = next(iter(cache.values()))
    assert _strip_meta(meta) == {"title": "T", "artist": "A", "album": "B", "duration": None}


def test_scan_worker_handles_unreadable_tags(tmp_path, monkeypatch):
    (tmp_path / "a.mp3").write_bytes(b"")

    def boom(_path, **kwargs):
        raise RuntimeError("broken tag")

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(boom))

    result = run_worker(ScanWorker(str(tmp_path)))

    _, count, cache = result["ok"][0]
    assert count == 1
    assert _strip_meta(cache[str(tmp_path / "a.mp3")]) == {
        "title": None, "artist": None, "album": None, "duration": None,
    }


def test_scan_worker_uses_scandir_mtime_without_extra_stat(tmp_path, monkeypatch):
    """一次 scandir 应同时产出路径和 _mtime，不再逐文件 getmtime。"""
    (tmp_path / "a.mp3").write_bytes(b"")
    (tmp_path / "Disc2").mkdir()
    (tmp_path / "Disc2" / "b.flac").write_bytes(b"")

    calls = []
    real_getmtime = os.path.getmtime

    def counting_getmtime(path):
        calls.append(path)
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", counting_getmtime)
    monkeypatch.setattr("tinytag.TinyTag.get",
                        staticmethod(lambda _p, **kw: tag(0.0)))

    result = run_worker(ScanWorker(str(tmp_path)))

    _, count, cache = result["ok"][0]
    assert count == 2
    assert not any(str(tmp_path) in p for p in calls), "扫描阶段不应再单独 stat 音频文件"
    assert all("_mtime" in meta for meta in cache.values())


def test_scan_worker_empty_folder(tmp_path):
    result = run_worker(ScanWorker(str(tmp_path)))

    folder, count, cache = result["ok"][0]
    assert count == 0
    assert cache == {}
    assert folder == os.path.abspath(str(tmp_path))
    assert result["progress"] == [(0, 0)]


def test_scan_worker_missing_folder_is_treated_as_empty(tmp_path):
    """目录被删除时按空结果处理，不弹「扫描失败」（与旧 os.walk 行为一致）。"""
    result = run_worker(ScanWorker(str(tmp_path / "nope")))

    assert result["failed"] == []
    assert len(result["ok"]) == 1
    assert result["ok"][0][1] == 0
    assert result["ok"][0][2] == {}


def test_scan_worker_emits_failed_on_error(tmp_path, monkeypatch):
    def boom(_path):
        raise OSError("permission denied")

    monkeypatch.setattr("src.main_window.os.scandir", boom)

    result = run_worker(ScanWorker(str(tmp_path)))

    assert result["ok"] == []
    assert result["failed"] == ["permission denied"]


def test_scan_worker_reports_progress(tmp_path, monkeypatch):
    for i in range(23):
        (tmp_path / ("%03d.mp3" % i)).write_bytes(b"")
    monkeypatch.setattr("tinytag.TinyTag.get",
                        staticmethod(lambda _p, **kw: tag(0.0)))

    result = run_worker(ScanWorker(str(tmp_path)))

    prog = result["progress"]
    assert prog, "扫描过程应上报进度"
    assert prog[0] == (0, 23)
    assert prog[-1] == (23, 23)
    assert all(t == 23 and 0 <= d <= t for d, t in prog)
    assert [d for d, _ in prog] == sorted(d for d, _ in prog), "进度必须单调递增"


def test_scan_worker_progress_counts_cached_files(tmp_path, monkeypatch):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).write_bytes(b"")
    cached_path = str(tmp_path / "a.mp3")
    existing = {cached_path: {"title": "Cached", "artist": None, "album": None,
                              "duration": None, "_mtime": 1.0}}
    monkeypatch.setattr("tinytag.TinyTag.get",
                        staticmethod(lambda _p, **kw: tag(0.0)))

    result = run_worker(ScanWorker(str(tmp_path), existing))

    assert result["progress"][0] == (1, 3), "缓存命中的文件应直接计入已就绪"
    assert result["progress"][-1] == (3, 3)


def test_scan_worker_interruption_aborts_metadata_read(qapp, tmp_path, monkeypatch):
    """用真实 QThread 验证中断：Qt 的 isInterruptionRequested() 仅在线程
    start() 之后才有效，同步直接调 run() 时恒为 False，测不出中断行为。"""
    for i in range(60):
        (tmp_path / ("%03d.mp3" % i)).write_bytes(b"")

    worker = ScanWorker(str(tmp_path))
    calls = []
    lock = threading.Lock()

    def fake_get(path, **kwargs):
        with lock:
            calls.append(path)
        time.sleep(0.01)  # 模拟冷盘读，让其余任务仍排在线程池里
        worker.requestInterruption()  # 第一个文件读到时模拟用户点「取消」
        return tag(0.0)

    monkeypatch.setattr("tinytag.TinyTag.get", staticmethod(fake_get))

    result = {"ok": [], "failed": []}
    worker.finished_ok.connect(lambda f, c, m: result["ok"].append((f, c, m)))
    worker.failed.connect(lambda msg: result["failed"].append(msg))
    worker.start()
    assert worker.wait(10000)
    qapp.processEvents()

    assert result["ok"] == [], "取消后不应再上报完成结果"
    assert result["failed"] == []
    assert 0 < len(calls) < 60, "中断后不应继续读完全部文件"


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
