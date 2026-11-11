"""「添加文件夹」性能基准：现有实现（tinytag）vs mutagen-rs。

场景：
  A 纯目录遍历（_scan_audio_tree）
  B tinytag 单线程 / 4 线程 / 8 线程
  C mutagen-rs 单文件单线程 / 4 线程 / batch_open
  D 端到端：当前真实 ScanWorker(QThread) → 建组
  E 端到端：扫描 + mutagen-rs batch_open → 建组
  F 端到端：扫描 + tinytag 4 线程 → 建组（与 E 同口径）

所有场景在同一进程内交替执行 3 轮，取中位数。数据已预热进页缓存。
"""
import gc
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/workspace")

import mutagen_rs
from PySide6.QtCore import QCoreApplication, QEventLoop
from tinytag import TinyTag

from src.main_window import ScanWorker, _scan_audio_tree
from src.player import MusicPlayer

MUSIC = "/tmp/mp3bench/music"
REPS = 3
RESULT_PATH = "/tmp/bench_results.json"


def collect_paths():
    paths = []
    for root, _, files in os.walk(MUSIC):
        paths += [os.path.join(root, f) for f in files if f.lower().endswith(".mp3")]
    return sorted(paths)


def read_tinytag(p):
    t = TinyTag.get(p, duration=False)
    return {"title": t.title, "artist": t.artist, "album": t.album}


def read_mrs(p):
    f = mutagen_rs.MP3(p)
    tags = f.tags or {}
    return {
        "title": str(tags["TIT2"]) if "TIT2" in tags else None,
        "artist": str(tags["TPE1"]) if "TPE1" in tags else None,
        "album": str(tags["TALB"]) if "TALB" in tags else None,
    }


def pool_read(fn, paths, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, paths))


def main():
    app = QCoreApplication([])
    paths = collect_paths()
    total_bytes = sum(os.path.getsize(p) for p in paths)
    print(f"files={len(paths)} size={total_bytes/1024/1024:.1f}MiB", flush=True)

    # ---------- 一致性校验：两个库读出的三元组必须一致 ----------
    mismatches = 0
    checked = 0
    for p in paths:
        try:
            a = read_tinytag(p)
        except Exception:
            a = None
        try:
            b = read_mrs(p)
        except Exception:
            b = None
        if a is not None and b is not None:
            checked += 1
            if any((a[k] or None) != (b[k] or None) for k in ("title", "artist", "album")):
                mismatches += 1
                if mismatches <= 3:
                    print("MISMATCH", os.path.basename(p), a, b, flush=True)
    print(f"consistency: checked={checked} mismatches={mismatches}", flush=True)

    player = MusicPlayer()
    results = {}

    def clear_mrs_cache():
        # mutagen-rs 有进程内缓存：不清空的话第二次起只有 1~2ms，完全失真
        mutagen_rs.clear_all_caches()

    def run_scan():
        return _scan_audio_tree(MUSIC)

    def run_tinytag(n):
        return pool_read(read_tinytag, paths, n) if n > 1 else [read_tinytag(p) for p in paths]

    def run_mrs(n):
        return pool_read(read_mrs, paths, n) if n > 1 else [read_mrs(p) for p in paths]

    def run_mrs_batch():
        raw = mutagen_rs.batch_open(paths)
        out = {}
        for p, r in raw.items():
            tags = (r or {}).get("tags") or {}
            out[p] = {
                "title": str(tags["TIT2"]) if "TIT2" in tags else None,
                "artist": str(tags["TPE1"]) if "TPE1" in tags else None,
                "album": str(tags["TALB"]) if "TALB" in tags else None,
            }
        return out

    def run_scanworker_e2e():
        state = {}
        worker = ScanWorker(MUSIC, {}, None)
        local_player = MusicPlayer()
        loop = QEventLoop()

        def on_ok(folder, count, meta):
            state["meta"] = meta
            state["count"] = count
            loop.quit()

        worker.finished_ok.connect(on_ok)
        worker.finished.connect(loop.quit)
        worker.finished.connect(worker.deleteLater)
        t0 = time.perf_counter()
        worker.start()
        loop.exec()          # C++ 阻塞等待，不空转抢 GIL
        worker.wait()
        scan_t = time.perf_counter() - t0
        t1 = time.perf_counter()
        added = local_player.add_groups_from_scan(state["meta"])
        build_t = time.perf_counter() - t1
        return scan_t, build_t, added, len(state["meta"])

    def run_pipeline_mrs():
        clear_mrs_cache()
        local_player = MusicPlayer()
        t0 = time.perf_counter()
        _scan_audio_tree(MUSIC)
        meta = run_mrs_batch()
        meta_t = time.perf_counter() - t0
        t1 = time.perf_counter()
        added = local_player.add_groups_from_scan(meta)
        build_t = time.perf_counter() - t1
        return meta_t, build_t, added, len(meta)

    def run_pipeline_tinytag():
        local_player = MusicPlayer()
        t0 = time.perf_counter()
        _scan_audio_tree(MUSIC)
        recs = run_tinytag(4)
        meta = {p: r for p, r in zip(paths, recs)}
        meta_t = time.perf_counter() - t0
        t1 = time.perf_counter()
        added = local_player.add_groups_from_scan(meta)
        build_t = time.perf_counter() - t1
        return meta_t, build_t, added, len(meta)

    def run_pipeline_mrs_perfile():
        clear_mrs_cache()
        local_player = MusicPlayer()
        t0 = time.perf_counter()
        _scan_audio_tree(MUSIC)
        recs = [read_mrs(p) for p in paths]
        meta = {p: r for p, r in zip(paths, recs)}
        meta_t = time.perf_counter() - t0
        t1 = time.perf_counter()
        added = local_player.add_groups_from_scan(meta)
        build_t = time.perf_counter() - t1
        return meta_t, build_t, added, len(meta)

    scenarios = {
        "scan_only": run_scan,
        "tinytag_1t": lambda: run_tinytag(1),
        "tinytag_4t": lambda: run_tinytag(4),
        "tinytag_8t": lambda: run_tinytag(8),
        "mrs_1t": lambda: (clear_mrs_cache(), run_mrs(1))[1],
        "mrs_4t": lambda: (clear_mrs_cache(), run_mrs(4))[1],
        "mrs_batch": lambda: (clear_mrs_cache(), run_mrs_batch())[1],
    }

    raw = {k: [] for k in scenarios}
    raw.update({"e2e_current": [], "e2e_mrs_batch": [], "e2e_mrs_perfile": [],
                "e2e_tinytag": []})

    for rep in range(REPS):
        for name, fn in scenarios.items():
            gc.collect()
            cpu0 = time.process_time()
            t0 = time.perf_counter()
            out = fn()
            wall = time.perf_counter() - t0
            cpu = time.process_time() - cpu0
            raw[name].append({"wall": wall, "cpu": cpu, "n": len(out)})
            print(f"  rep{rep} {name:14s} {wall*1000:9.1f}ms cpu={cpu*1000:8.1f}ms", flush=True)

        for name, fn in (("e2e_current", run_scanworker_e2e),
                         ("e2e_mrs_batch", run_pipeline_mrs),
                         ("e2e_mrs_perfile", run_pipeline_mrs_perfile),
                         ("e2e_tinytag", run_pipeline_tinytag)):
            gc.collect()
            cpu0 = time.process_time()
            t0 = time.perf_counter()
            res = fn()
            wall = time.perf_counter() - t0
            cpu = time.process_time() - cpu0
            raw[name].append({"wall": wall, "cpu": cpu, "result": list(res)})
            print(f"  rep{rep} {name:14s} {wall*1000:9.1f}ms {res}", flush=True)

    for k, v in raw.items():
        walls = [x["wall"] for x in v]
        cpus = [x["cpu"] for x in v]
        results[k] = {
            "median_wall_s": statistics.median(walls),
            "min_wall_s": min(walls),
            "max_wall_s": max(walls),
            "median_cpu_s": statistics.median(cpus),
            "samples": v,
            "files_per_s": len(paths) / statistics.median(walls),
            "mb_per_s": (total_bytes / 1024 / 1024) / statistics.median(walls),
        }

    # ---------- 单文件延迟分布（各测 2000 个，测量前清空库缓存） ----------
    per_file = []
    clear_mrs_cache()
    for p in paths[:2000]:
        t0 = time.perf_counter()
        read_tinytag(p)
        a = time.perf_counter() - t0
        t0 = time.perf_counter()
        read_mrs(p)
        b = time.perf_counter() - t0
        per_file.append((a, b))
    for idx, key in ((0, "lat_tinytag"), (1, "lat_mrs")):
        vals = sorted(x[idx] for x in per_file)
        results[key] = {
            "p50_ms": vals[len(vals) // 2] * 1000,
            "p95_ms": vals[int(len(vals) * 0.95)] * 1000,
            "p99_ms": vals[int(len(vals) * 0.99)] * 1000,
            "mean_ms": statistics.mean(vals) * 1000,
        }

    meta_info = {
        "files": len(paths),
        "dirs": 58,
        "size_mib": total_bytes / 1024 / 1024,
        "reps": REPS,
        "consistency_checked": checked,
        "consistency_mismatches": mismatches,
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "tinytag_version": __import__("tinytag").__version__,
        "mutagen_rs_version": mutagen_rs.version_string,
    }
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({"meta": meta_info, "results": results}, f, ensure_ascii=False, indent=2)
    print("saved", RESULT_PATH, flush=True)


if __name__ == "__main__":
    main()
