"""播放 / 切组 / 加载 性能基准（真实 MainWindow + just_playback 音频后端）。

测量口径：
  handler  = 触发动作的槽函数执行时间（不含排队重绘）
  flush    = 紧随其后的 processEvents() 耗时（把 paint 事件真正跑掉）
两者相加 ≈ 用户感知的卡顿时长。每个场景 5 次，取中位数。
"""
import gc
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/benchhome"
os.environ["XDG_CONFIG_HOME"] = "/tmp/benchhome/.config"
os.makedirs("/tmp/benchhome/.config", exist_ok=True)
sys.path.insert(0, os.environ.get("LYRICA_REPO", "/workspace"))

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow, _scan_audio_tree
from src.player import PlayMode
from tinytag import TinyTag

MUSIC = "/tmp/mp3bench/music"
REPS = 5
OUT = "/tmp/play_bench.json"

app = QApplication([])


def rss_mib():
    with open("/proc/self/statm") as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024


def load_library(window):
    paths = []
    for root, _, files in os.walk(MUSIC):
        paths += [os.path.join(root, f) for f in files]
    t0 = time.perf_counter()

    def read(p):
        t = TinyTag.get(p, duration=False)
        return {"title": t.title, "artist": t.artist, "album": t.album}

    with ThreadPoolExecutor(max_workers=4) as pool:
        recs = list(pool.map(read, paths))
    meta = dict(zip(paths, recs))
    window.player.preload_metadata(meta)
    added = window.player.add_groups_from_scan(meta)
    return added, time.perf_counter() - t0, paths


def measure(fn, reps=REPS):
    hs, fs = [], []
    for _ in range(reps):
        app.processEvents()
        t = time.perf_counter()
        fn()
        h = time.perf_counter() - t
        t = time.perf_counter()
        app.processEvents()
        f = time.perf_counter() - t
        hs.append(h)
        fs.append(f)
    return {
        "handler_ms": statistics.median(hs) * 1000,
        "flush_ms": statistics.median(fs) * 1000,
        "total_ms": (statistics.median(hs) + statistics.median(fs)) * 1000,
    }


def main():
    window = MainWindow()
    window.resize(1000, 640)
    window.show()
    app.processEvents()

    window.player.clear_groups()
    added, load_s, all_paths = load_library(window)
    app.processEvents()
    sizes = [window.player.get_group_track_count(i) for i in range(window.player.get_group_count())]
    print(f"library: {added} tracks / {len(sizes)} groups / load {load_s*1000:.0f}ms", flush=True)
    print("sizes:", sizes[:8], "... min", min(sizes), "max", max(sizes), flush=True)

    # 选规模接近 300 与 20 的组
    big = sorted(range(len(sizes)), key=lambda i: abs(sizes[i] - 300))[:3]
    small = sorted(range(len(sizes)), key=lambda i: abs(sizes[i] - 20))[:2]
    print("big groups:", [(i, sizes[i]) for i in big], "small:", [(i, sizes[i]) for i in small], flush=True)

    window.player.set_play_mode(PlayMode.SEQUENCE)
    window._on_group_selected(big[0])
    app.processEvents()

    results = {}
    rss0 = rss_mib()
    timers0 = len(window.findChildren(QTimer))

    # 1. 切组（大组，交替）
    state = {"i": 0}

    def switch_big():
        state["i"] ^= 1
        window.groups_list.setCurrentRow(big[state["i"]])

    results["switch_big"] = measure(switch_big)

    def switch_small():
        state["i"] ^= 1
        window.groups_list.setCurrentRow(small[state["i"]])

    results["switch_small"] = measure(switch_small)

    largest = sorted(range(len(sizes)), key=lambda i: -sizes[i])[:2]
    print("largest groups:", [(i, sizes[i]) for i in largest], flush=True)

    def switch_largest():
        state["i"] ^= 1
        window.groups_list.setCurrentRow(largest[state["i"]])

    results["switch_largest"] = measure(switch_largest)

    def rebuild_largest():
        window._rendered_tracks_key = None
        window._refresh_tracks_list()

    results["rebuild_largest"] = measure(rebuild_largest)

    def scan_largest():
        window._update_marquee()

    window._on_group_selected(largest[0])
    app.processEvents()
    results["marquee_scan_largest"] = measure(scan_largest)

    # 2. 强制重建 300 行（排除快照短路）
    window._on_group_selected(big[0])
    app.processEvents()

    def rebuild():
        window._rendered_tracks_key = None
        window._refresh_tracks_list()

    results["rebuild_300"] = measure(rebuild)

    # 3. 重建后 marquee 判定（O(n) 文本测宽）
    results["marquee_scan_300"] = measure(lambda: window._update_marquee())

    # 4. 纯 UI 轨道切换（不含解码加载）
    window._on_group_selected(big[0])
    app.processEvents()
    cur = {"ti": 0}

    def ui_change():
        cur["ti"] = (cur["ti"] + 7) % 50
        flat = window.player.get_group_of_track(0)  # noqa
        flat_idx = sum(sizes[: big[0]]) + cur["ti"]
        window._on_track_changed(flat_idx)

    results["ui_track_change"] = measure(ui_change)

    # 5. 同组双击播放（含 load_file + play）
    rows = {"ti": 0}

    def play_same():
        rows["ti"] = (rows["ti"] + 1) % min(60, sizes[big[0]])
        item = window.tracks_list.item(rows["ti"])
        window._on_track_double_clicked(item)
        window.player.stop()

    results["play_same_group"] = measure(play_same)

    # 6. 跨组双击播放（触发列表重建 + 加载）
    cross = {"i": 0}

    def play_cross():
        other = big[1 + (cross["i"] % 2)]
        cross["i"] += 1
        window.player.play_track_in_group(other, 0)
        window.player.stop()

    results["play_cross_group"] = measure(play_cross)

    # 7. 下一首（组内切歌）
    nxt = {"n": 0}

    def next_track():
        window.player.play_track_in_group(big[0], nxt["n"] % 30)
        nxt["n"] += 1
        window.player.next_track()
        window.player.stop()

    results["next_track"] = measure(next_track)

    # 8. 仅解码器加载（绕过 UI）
    files = window.player.get_group_tracks(big[0])[:20]
    lf = {"i": 0}

    def load_only():
        p = files[lf["i"] % len(files)]
        lf["i"] += 1
        window.player._playback.load_file(p)
        window.player._playback.play()
        window.player._playback.stop()

    results["decoder_load"] = measure(load_only)

    # 9. 播放进度刷新（300ms 一次）
    results["position_tick"] = measure(lambda: window._on_position_changed(12.345))

    # 10. marquee tick + 真实重绘（收窄窗口，确保长文件名确实超宽）
    window.resize(620, 640)
    window._on_group_selected(largest[0])
    app.processEvents()
    window._update_marquee()
    results["marquee_active_narrow"] = window._marquee_timer.isActive()
    results["marquee_span_px"] = window._marquee_span
    results["marquee_tick_paint"] = measure(window._on_marquee_tick, reps=10)
    results["marquee_viewport_px"] = window.tracks_list.viewport().width()

    # 11. 启动恢复：重新扫描 58 个目录
    saved = window.player.get_playlists_for_save()

    def restore():
        window.player.restore_groups(saved)

    results["restore_groups"] = measure(restore, reps=3)
    app.processEvents()

    # 12. 连续 20 次切歌（累积/泄漏检查）
    timers_before = len(window.findChildren(QTimer))
    rss_before = rss_mib()
    t0 = time.perf_counter()
    for k in range(20):
        window.player.play_track_in_group(big[k % 3], k % 25)
        window.player.stop()
        app.processEvents()
    burst = time.perf_counter() - t0
    results["burst_20"] = {
        "total_ms": burst * 1000,
        "per_action_ms": burst * 1000 / 20,
        "rss_delta_mib": rss_mib() - rss_before,
        "timer_delta": len(window.findChildren(QTimer)) - timers_before,
    }

    # 13. 实际出声延迟：load_file → curr_pos 首次推进
    lat = []
    for k in range(5):
        window.player.stop()
        app.processEvents()
        p = files[k]
        t0 = time.perf_counter()
        window.player._playback.load_file(p)
        window.player._playback.play()
        deadline = t0 + 3.0
        while time.perf_counter() < deadline:
            if window.player._playback.curr_pos > 0:
                break
            time.sleep(0.001)
        lat.append((time.perf_counter() - t0) * 1000)
        window.player._playback.stop()
    results["audio_start_latency"] = {
        "median_ms": statistics.median(lat),
        "min_ms": min(lat),
        "max_ms": max(lat),
    }

    results["_env"] = {
        "tracks": added,
        "groups": len(sizes),
        "big_group": sizes[big[0]],
        "largest_group": sizes[largest[0]],
        "small_group": sizes[small[0]],
        "rss_start_mib": rss0,
        "timers_start": timers0,
        "reps": REPS,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    for k, v in results.items():
        print(f"{k:22s} {v}", flush=True)


if __name__ == "__main__":
    main()
