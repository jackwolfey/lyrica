"""切组耗时归因：把 _refresh_tracks_list 拆成 建 item / marquee 宽度扫描 / 绘制。"""
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/benchhome"
os.environ["XDG_CONFIG_HOME"] = "/tmp/benchhome/.config"
sys.path.insert(0, os.environ.get("LYRICA_REPO", "/workspace"))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow
from tinytag import TinyTag

MUSIC = "/tmp/mp3bench/music"
app = QApplication([])


def med(fn, reps=7):
    vals = []
    for _ in range(reps):
        app.processEvents()
        t = time.perf_counter()
        fn()
        vals.append(time.perf_counter() - t)
    return statistics.median(vals) * 1000


def main():
    w = MainWindow()
    w.resize(1000, 640)
    w.show()
    app.processEvents()

    paths = [os.path.join(r, f) for r, _, fs in os.walk(MUSIC) for f in fs]

    def read(p):
        t = TinyTag.get(p, duration=False)
        return {"title": t.title, "artist": t.artist, "album": t.album}

    with ThreadPoolExecutor(max_workers=4) as pool:
        recs = list(pool.map(read, paths))
    meta = dict(zip(paths, recs))
    w.player.preload_metadata(meta)
    w.player.add_groups_from_scan(meta)
    app.processEvents()

    sizes = [w.player.get_group_track_count(i) for i in range(w.player.get_group_count())]
    gi = max(range(len(sizes)), key=lambda i: sizes[i])
    w._on_group_selected(gi)
    app.processEvents()
    n = w.tracks_list.count()
    print("group", gi, "rows", n, flush=True)

    out = {"rows": n}

    # 1. 完整重建（含 marquee 扫描）
    out["refresh_full"] = med(lambda: (setattr(w, "_rendered_tracks_key", None), w._refresh_tracks_list()))

    # 2. 重建但不做 marquee 扫描
    orig = w._update_marquee
    w._update_marquee = lambda: None
    out["refresh_no_marquee"] = med(lambda: (setattr(w, "_rendered_tracks_key", None), w._refresh_tracks_list()))
    w._update_marquee = orig

    # 3. marquee 全量扫描 vs 仅可视行
    fm = w.tracks_list.fontMetrics()
    out["marquee_scan_all"] = med(lambda: w._update_marquee())

    def scan_visible():
        top = w.tracks_list.indexAt(QPoint(4, 2)).row()
        bottom = w.tracks_list.indexAt(QPoint(4, w.tracks_list.viewport().height() - 2)).row()
        if top < 0:
            top = 0
        if bottom < 0:
            bottom = w.tracks_list.count() - 1
        for i in range(top, bottom + 1):
            fm.horizontalAdvance(w.tracks_list.item(i).text())

    out["marquee_scan_visible"] = med(scan_visible)
    out["visible_rows"] = (
        w.tracks_list.indexAt(QPoint(4, w.tracks_list.viewport().height() - 2)).row()
        - w.tracks_list.indexAt(QPoint(4, 2)).row() + 1
    )

    # 4. 同步重绘一次视口
    out["viewport_repaint"] = med(lambda: w.tracks_list.viewport().repaint())

    # 5. _on_track_changed 组成部分
    out["get_playlist_copy"] = med(lambda: w.player.get_playlist())
    p = w.player.get_group_tracks(gi)[0]
    out["get_track_metadata_cached"] = med(lambda: w.player.get_track_metadata(p))
    long_text = "某首非常非常长的歌曲名称 " * 4
    out["set_3_labels"] = med(lambda: (
        w.lbl_title.setText(long_text), w.lbl_meta_title.setText(long_text),
        w.lbl_meta_detail.setText(long_text)))

    # 6. 建 item 本身（不触发 layout/信号）
    def add_items():
        w.tracks_list.setUpdatesEnabled(False)
        w.tracks_list.clear()
        for t in w.player.get_group_tracks(gi):
            from PySide6.QtWidgets import QListWidgetItem
            from PySide6.QtCore import Qt
            it = QListWidgetItem(os.path.basename(t))
            it.setData(Qt.UserRole, t)
            w.tracks_list.addItem(it)
        w.tracks_list.setUpdatesEnabled(True)

    out["add_items_only"] = med(add_items)
    w._rendered_tracks_key = None
    w._refresh_tracks_list()
    app.processEvents()

    with open("/tmp/play_bench.json", encoding="utf-8") as f:
        base = json.load(f)
    base["attribution"] = out
    with open("/tmp/play_bench.json", "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    for k, v in out.items():
        print(f"{k:26s} {v}")


if __name__ == "__main__":
    main()
