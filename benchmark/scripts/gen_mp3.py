"""生成 10000 首带复杂随机元数据的 MP3，用于「添加文件夹」性能基准。

结构：58 个播放组目录（模拟专辑），元数据混合 ID3v2.3 / v2.4 / ID3v1，
包含长中文、日文、俄文、emoji、多艺术家、多行注释、TXXX 自定义帧等。
另有少量无标签文件 + 少量损坏文件，用于覆盖真实曲库的异常路径。
"""
import os
import random
import shutil
import subprocess
import sys
import time

BASE = "/tmp/mp3bench/music"
TOTAL = 10000
DIRS = 58
NO_TAG_FILES = 300      # 3% 无标签
CORRUPT_FILES = 10      # 0.1% 损坏

rng = random.Random(20261111)

WORDS_EN = ["Midnight", "Crimson", "Echo", "Velvet", "Neon", "Fracture", "Aurora",
            "Static", "Ocean", "Paradox", "Solstice", "Gravity", "Ashes", "Prism",
            "Hollow", "Tide", "Luminous", "Requiem", "Cascade", "Ember"]
WORDS_CN = ["夜航", "潮汐", "山鬼", "盲盒", "光年", "荒原", "回声", "霓虹", "陷落", "长夜"]
WORDS_JA = ["夜明け", "花火", "境界線", "残響", "深海", "追憶", "流星", "静寂"]
WORDS_RU = ["Полночь", "Тишина", "Огонь", "Ветер", "Снег", "Рассвет"]
EMOJI = ["🌙", "🌊", "🔥", "✨", "🎧", "🌌", "🕯️", "🎹"]
ARTISTS = ["Aurora Vale", "陈默", "Hikari", "Nocturne Society", "Мира",
           "The Paper Kites", "林深见鹿", "Solar Fields", "Echo & The Void",
           "kuro.", "Violet Hour", "叶隙"]
ALBUMS = ["Afterglow", "夜航西飞", "Fragments", "第二人生", "Solaris",
          "城市漫游", "White Noise", "潮汐記", "Pale Blue Dot", "无声的雨"]
GENRES = ["Ambient", "电子", "Post-Rock", "民谣", "Jazz", "Lo-Fi", "独立摇滚", "Classical"]
COMPOSERS = ["A. Vale", "陈小舟", "H. Tanaka", "М. Волков", "", "L. Park"]


def rand_title(i: int) -> str:
    style = rng.randrange(6)
    if style == 0:
        return f"{rng.choice(WORDS_EN)} {rng.choice(WORDS_EN)} (feat. {rng.choice(ARTISTS)}) [{2020 + i % 6} Remaster]"
    if style == 1:
        return f"{rng.choice(WORDS_CN)}·{rng.choice(WORDS_CN)} {rng.choice(EMOJI)}"
    if style == 2:
        return f"{rng.choice(WORDS_JA)} — {rng.choice(WORDS_EN)} Mix"
    if style == 3:
        return f"{rng.choice(WORDS_RU)} ({i:04d} Version)"
    if style == 4:
        return f"{rng.choice(WORDS_EN)} / {rng.choice(WORDS_CN)} {rng.choice(EMOJI)} " \
               f"Extended Club Mix — Live at Budōkan 2024"
    return f"Track {i:05d} {rng.choice(EMOJI)}"


def rand_artist() -> str:
    n = rng.choice([1, 1, 1, 2, 3])
    return " / ".join(rng.sample(ARTISTS, n))


def rand_comment() -> str:
    return ("Recorded at a small studio; mixed with analog outboard gear.\n"
            + rng.choice(["感谢收听。", "Special thanks to everyone.", "録音：2024 年春。"])
            + "\n" + "x" * rng.randrange(10, 120))


def write_tags(path: str, idx: int, version: str) -> None:
    from mutagen.id3 import (ID3, TIT2, TPE1, TALB, TCON, TDRC, TYER, TRCK,
                             TPE2, TCOM, COMM, TXXX, TLEN)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=[rand_title(idx)]))
    tags.add(TPE1(encoding=3, text=[rand_artist()]))
    tags.add(TALB(encoding=3, text=[rng.choice(ALBUMS) + rng.choice(["", " (Deluxe)", " [24bit]"])]))
    tags.add(TCON(encoding=3, text=[rng.choice(GENRES)]))
    if version.startswith("2.3"):
        tags.add(TYER(encoding=3, text=[str(rng.randrange(1970, 2026))]))
    else:
        tags.add(TDRC(encoding=3, text=[f"{rng.randrange(1970, 2026)}-{rng.randrange(1, 13):02d}-"
                                        f"{rng.randrange(1, 29):02d}"]))
    tags.add(TRCK(encoding=3, text=[f"{rng.randrange(1, 30)}/{rng.randrange(30, 40)}"]))
    tags.add(TPE2(encoding=3, text=[rng.choice(ARTISTS)]))
    tags.add(TCOM(encoding=3, text=[rng.choice(COMPOSERS)]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=[rand_comment()]))
    tags.add(TXXX(encoding=3, desc="MUSICBRAINZ_ALBUMID",
                  text=[f"{rng.getrandbits(128):032x}"]))
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN",
                  text=[f"{rng.uniform(-12, 3):+.2f} dB"]))
    tags.add(TLEN(encoding=3, text=["1000"]))
    tags.save(path, v2_version=3 if version.startswith("2.3") else 4)
    if version.endswith("+v1"):
        tags.save(path, v1=2, v2_version=3)


def make_base_mp3(dst: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "1",
         "-c:a", "libmp3lame", "-b:a", "64k", dst],
        check=True,
    )


def main() -> None:
    if os.path.isdir(BASE):
        shutil.rmtree(BASE)
    os.makedirs(BASE)
    base = "/tmp/mp3bench/base.mp3"
    make_base_mp3(base)
    base_size = os.path.getsize(base)

    # 按近似 Zipf 分布切分到 58 个专辑目录，最少 20 首，最多约 300 首
    weights = [1.0 / (i + 1) ** 0.55 for i in range(DIRS)]
    s = sum(weights)
    counts = [max(20, int(TOTAL * w / s)) for w in weights]
    diff = TOTAL - sum(counts)
    counts[0] += diff

    t0 = time.time()
    idx = 0
    made = 0
    for d in range(DIRS):
        album_dir = os.path.join(BASE, f"{rng.choice(ALBUMS)} {1960 + d:04d}")
        os.makedirs(album_dir, exist_ok=True)
        for k in range(counts[d]):
            idx += 1
            if idx > TOTAL:
                break
            # 文件名：混合长英文 / 中文 / 括号 / 空格
            if k % 5 == 0:
                name = f"{rng.choice(WORDS_CN)} {rng.choice(WORDS_EN)} - {rand_title(idx)[:90]} [{idx:05d}].mp3"
            else:
                name = f"{idx:05d} - {rng.choice(ARTISTS)} - {rand_title(idx)[:70]}.mp3"
            path = os.path.join(album_dir, name.replace("/", "_"))
            shutil.copyfile(base, path)

            if CORRUPT_FILES and made < CORRUPT_FILES:
                # 损坏：截断到 200 字节，保留 ID3 头但帧不完整
                with open(path, "r+b") as f:
                    f.truncate(200)
                made += 1
                continue

            if made >= CORRUPT_FILES and idx % 33 == 0:
                pass  # 无标签文件（保持 ffmpeg 输出的裸 MP3）
            elif made < CORRUPT_FILES + NO_TAG_FILES:
                pass
            else:
                version = rng.choice(["2.3", "2.4", "2.4", "2.3+v1", "2.4+v1"])
                try:
                    write_tags(path, idx, version)
                except Exception as e:
                    print("tag fail", path, e, file=sys.stderr)
            made += 1
            if idx % 2000 == 0:
                print(f"  {idx}/{TOTAL} files, {time.time() - t0:.1f}s", flush=True)

    total_bytes = sum(
        os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(BASE) for f in fs
    )
    n_dirs = sum(1 for _, ds, _ in os.walk(BASE) for _ in ds)
    print(f"done: {TOTAL} files, {n_dirs} dirs, {total_bytes / 1024 / 1024:.1f} MiB, "
          f"base={base_size}B, build={time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
