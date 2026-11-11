"""根据 bench_results.json 生成 HTML 报告。"""
import json
import os

D = json.load(open("/tmp/bench_results.json"))
R = D["results"]
M = D["meta"]

OUT_DIR = "/workspace/benchmark"
os.makedirs(OUT_DIR, exist_ok=True)

BATCH = [
    (10, 0, "同数据集"),
    (100, 8, "同数据集"),
    (1000, 528, "同数据集"),
    (10000, 8685, "同数据集"),
    (200, 42, "音源内容各异"),
]


def ms(x):
    return x * 1000


def f1(x):
    return f"{x:.0f}"


def sec(x):
    return f"{x:.2f} s"


def bar(label, value, vmax, color, suffix):
    pct = max(1.5, value / vmax * 100)
    return f"""
    <div class="row">
      <div class="row-label">{label}</div>
      <div class="track"><div class="fill" style="width:{pct:.1f}%;background:{color}"></div>
        <span class="val">{suffix}</span></div>
    </div>"""


e2e = [
    ("当前实现<span class='sub'>tinytag · 4 线程 · 基线</span>", R["e2e_current"]["median_wall_s"], "#f9e2af", ""),
    ("同口径 tinytag 流水线", R["e2e_tinytag"]["median_wall_s"], "#f9e2af", ""),
    ("mutagen-rs 逐文件<span class='sub'>单线程 · 结果正确</span>", R["e2e_mrs_perfile"]["median_wall_s"], "#a6e3a1", ""),
    ("mutagen-rs batch_open<span class='sub'>结果错位，仅作速度上限</span>", R["e2e_mrs_batch"]["median_wall_s"], "#f38ba8", ""),
]
e2e_max = max(v for _, v, _, _ in e2e)
e2e_chart = "".join(
    bar(lbl, v, e2e_max, c, f"{v:.2f} s") for lbl, v, c, _ in e2e
)

thread_scenarios = [
    ("tinytag 1 线程", R["tinytag_1t"]["median_wall_s"], "#f9e2af"),
    ("tinytag 4 线程", R["tinytag_4t"]["median_wall_s"], "#f9e2af"),
    ("tinytag 8 线程", R["tinytag_8t"]["median_wall_s"], "#f9e2af"),
    ("mutagen-rs 逐文件 1 线程", R["mrs_1t"]["median_wall_s"], "#a6e3a1"),
    ("mutagen-rs 逐文件 4 线程", R["mrs_4t"]["median_wall_s"], "#a6e3a1"),
    ("mutagen-rs batch_open", R["mrs_batch"]["median_wall_s"], "#89b4fa"),
]
t_max = max(v for _, v, _ in thread_scenarios)
thread_chart = "".join(
    bar(lbl, v, t_max, c, f"{v*1000:.0f} ms") for lbl, v, c in thread_scenarios
)

batch_chart = "".join(
    bar(f"{n} 个文件" + (f"（{kind}）" if kind != "同数据集" else ""),
        wrong / n * 100, 100, "#f38ba8" if wrong else "#a6e3a1",
        f"{wrong}/{n} 错误（{wrong/n*100:.1f}%）")
    for n, wrong, kind in BATCH
)

rows = []
spec = [
    ("scan_only", "目录遍历（os.scandir，_scan_audio_tree）", "基线"),
    ("tinytag_1t", "tinytag 单线程读标签", "当前库"),
    ("tinytag_4t", "tinytag 4 线程（当前生产配置）", "当前库"),
    ("tinytag_8t", "tinytag 8 线程（旧版配置）", "当前库"),
    ("mrs_1t", "mutagen-rs 逐文件单线程", "候选"),
    ("mrs_4t", "mutagen-rs 逐文件 4 线程", "候选"),
    ("mrs_batch", "mutagen-rs batch_open", "候选（有正确性问题）"),
    ("e2e_current", "端到端：真实 ScanWorker → 建组", "当前实现"),
    ("e2e_tinytag", "端到端：扫描 + tinytag 4 线程 → 建组", "对照"),
    ("e2e_mrs_perfile", "端到端：扫描 + mutagen-rs 逐文件 → 建组", "候选"),
    ("e2e_mrs_batch", "端到端：扫描 + batch_open → 建组", "候选（结果错位）"),
]
for key, label, kind in spec:
    v = R[key]
    cls = "warn" if "错位" in kind else ""
    rows.append(f"""<tr class="{cls}">
      <td class="lbl">{label}</td><td class="kind">{kind}</td>
      <td>{v['median_wall_s']*1000:.0f}</td><td>{v['min_wall_s']*1000:.0f}</td>
      <td>{v['median_wall_s']*1000:.0f}</td><td>{v['median_cpu_s']/v['median_wall_s']*100:.0f}%</td>
      <td>{v['files_per_s']:.0f}</td><td>{v['mb_per_s']:.0f}</td></tr>""")
table_rows = "\n".join(rows)

lat_tt = R["lat_tinytag"]
lat_mrs = R["lat_mrs"]

sp_e2e = R["e2e_current"]["median_wall_s"] / R["e2e_mrs_perfile"]["median_wall_s"]
sp_batch = R["e2e_current"]["median_wall_s"] / R["e2e_mrs_batch"]["median_wall_s"]
sp_meta = R["tinytag_1t"]["median_wall_s"] / R["mrs_1t"]["median_wall_s"]
sp_meta_batch = R["tinytag_1t"]["median_wall_s"] / R["mrs_batch"]["median_wall_s"]

scan_pct = R["scan_only"]["median_wall_s"] / R["e2e_current"]["median_wall_s"] * 100
meta_pct = 100 - scan_pct

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lyrica「添加文件夹」元数据解析性能基准 · v1.1.3</title>
<style>
  :root {{
    --base:#1e1e2e; --mantle:#181825; --surface:#313244; --overlay:#45475a;
    --text:#cdd6f4; --sub:#a6adc8; --blue:#89b4fa; --green:#a6e3a1;
    --yellow:#f9e2af; --red:#f38ba8; --mauve:#cba6f7;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--base); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC",
      "PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; font-size:15px; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:56px 28px 96px; }}
  header {{ border-bottom:1px solid var(--surface); padding-bottom:28px; margin-bottom:40px; }}
  h1 {{ font-size:30px; margin:0 0 10px; letter-spacing:-.3px; }}
  .meta {{ color:var(--sub); font-size:13.5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  h2 {{ font-size:20px; margin:52px 0 14px; padding-left:12px; border-left:3px solid var(--blue); }}
  h3 {{ font-size:15.5px; margin:28px 0 10px; color:var(--mauve); }}
  p {{ margin:10px 0; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin:26px 0 8px; }}
  .card {{ background:var(--mantle); border:1px solid var(--surface); border-radius:12px; padding:16px 18px; }}
  .card .k {{ font-size:12px; color:var(--sub); letter-spacing:.04em; }}
  .card .v {{ font-size:24px; font-weight:700; margin-top:4px; }}
  .card .d {{ font-size:12.5px; color:var(--sub); margin-top:2px; }}
  .g {{ color:var(--green); }} .y {{ color:var(--yellow); }} .r {{ color:var(--red); }} .b {{ color:var(--blue); }}
  .row {{ display:grid; grid-template-columns:250px 1fr; gap:14px; align-items:center; margin:9px 0; }}
  .row-label {{ font-size:13.5px; color:var(--sub); text-align:right; }}
  .row-label .sub {{ display:block; font-size:11px; opacity:.75; }}
  .track {{ position:relative; background:var(--mantle); border-radius:7px; height:26px;
    border:1px solid var(--surface); overflow:hidden; }}
  .fill {{ height:100%; border-radius:6px 0 0 6px; transition:width .3s; }}
  .val {{ position:absolute; right:9px; top:2px; font-size:12.5px;
    font-family:ui-monospace,Menlo,monospace; color:var(--text); }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; margin:14px 0 6px; }}
  th,td {{ text-align:right; padding:8px 9px; border-bottom:1px solid var(--surface); }}
  th {{ color:var(--sub); font-weight:500; font-size:12.5px; }}
  td.lbl,th.lbl {{ text-align:left; }}
  td.kind {{ text-align:left; color:var(--sub); font-size:12.5px; }}
  tr.warn td {{ background:rgba(243,139,168,.08); }}
  code {{ background:var(--mantle); border:1px solid var(--surface); border-radius:5px;
    padding:1px 5px; font-size:12.5px; font-family:ui-monospace,Menlo,monospace; }}
  pre {{ background:var(--mantle); border:1px solid var(--surface); border-radius:10px;
    padding:14px 16px; overflow:auto; font-size:12.5px; line-height:1.6; }}
  .callout {{ border-left:3px solid var(--red); background:rgba(243,139,168,.07);
    padding:14px 18px; border-radius:0 10px 10px 0; margin:18px 0; }}
  .callout.ok {{ border-color:var(--green); background:rgba(166,227,161,.07); }}
  .callout.note {{ border-color:var(--yellow); background:rgba(249,226,175,.06); }}
  ul {{ margin:10px 0 10px 22px; padding:0; }}
  li {{ margin:5px 0; }}
  .tag {{ display:inline-block; font-size:11.5px; padding:1px 8px; border-radius:20px;
    background:var(--surface); color:var(--sub); margin-left:8px; vertical-align:2px; }}
  footer {{ margin-top:64px; padding-top:20px; border-top:1px solid var(--surface);
    color:var(--sub); font-size:12.5px; }}
  .sub {{ color:var(--sub); font-size:11.5px; }}
</style>
</head>
<body><div class="wrap">

<header>
  <h1>Lyrica「添加文件夹」元数据解析性能基准</h1>
  <div class="meta">被测版本：v1.1.3 · 2026-11-11 · 10,000 个 MP3 / 58 个播放组 / 97.0 MiB · 3 次运行取中位数</div>
</header>

<div class="cards">
  <div class="card"><div class="k">当前实现（端到端）</div>
    <div class="v y">{R['e2e_current']['median_wall_s']:.2f} s</div>
    <div class="d">tinytag + 4 线程</div></div>
  <div class="card"><div class="k">mutagen-rs 逐文件</div>
    <div class="v g">{R['e2e_mrs_perfile']['median_wall_s']:.2f} s</div>
    <div class="d">正确 · 提速 {sp_e2e:.2f}×</div></div>
  <div class="card"><div class="k">mutagen-rs batch_open</div>
    <div class="v r">{R['e2e_mrs_batch']['median_wall_s']:.2f} s</div>
    <div class="d">提速 {sp_batch:.1f}×，但 86.9% 条目元数据错位</div></div>
  <div class="card"><div class="k">目录遍历占比</div>
    <div class="v b">{scan_pct:.0f}%</div>
    <div class="d">{R['scan_only']['median_wall_s']*1000:.0f} ms / {R['e2e_current']['median_wall_s']*1000:.0f} ms</div></div>
</div>

<h2>一、结论</h2>
<ul>
  <li><b>时间几乎全部花在元数据解析上，而不是遍历磁盘。</b>目录遍历仅 {R['scan_only']['median_wall_s']*1000:.0f} ms（{scan_pct:.1f}%），
      逐文件读标签约 {meta_pct:.0f}%，建组仅 12–15 ms。</li>
  <li><b>mutagen-rs 的逐文件接口确实更快，但只有 {sp_e2e:.2f}×（端到端），不是数量级。</b>
      单文件解析 p50 从 {lat_tt['p50_ms']:.3f} ms 降到 {lat_mrs['p50_ms']:.3f} ms（约 {(1-lat_mrs['p50_ms']/lat_tt['p50_ms'])*100:.0f}%）。</li>
  <li><b>真正快 7–9× 的是 <code>batch_open()</code>，但它在本次实测中把标签配错到了别的文件上</b>
      （10,000 个文件里 8,685 个错位），属于不可用的“快”。</li>
  <li><b>加线程没有收益。</b>tinytag 从 1 线程到 4/8 线程反而变慢（{R['tinytag_1t']['median_wall_s']*1000:.0f} → {R['tinytag_8t']['median_wall_s']*1000:.0f} ms），
      CPU 时间却涨到 3 倍，是典型的 GIL 争用；mutagen-rs 逐文件同样无收益。</li>
</ul>

<h2>二、端到端结果</h2>
<p>端到端指：<code>_scan_audio_tree()</code> 遍历 → 读 title/artist/album（<code>duration=False</code>）→
<code>MusicPlayer.add_groups_from_scan()</code> 建组。与用户点「添加文件夹」后等待的时间同口径。</p>
{e2e_chart}
<p class="sub">灰字为相对基线的倍数说明；红色条的结果存在元数据错位，仅代表速度上限。</p>

<h2>三、分项数据</h2>
<table>
  <thead><tr><th class="lbl">场景</th><th class="lbl">类别</th><th>中位(ms)</th><th>最快(ms)</th>
  <th>CPU(ms)</th><th>CPU/墙钟</th><th>文件/秒</th><th>MiB/秒</th></tr></thead>
  <tbody>{table_rows}</tbody>
</table>
<p class="sub">CPU/墙钟 &gt;100% 表示确有多线程并行；tinytag 4 线程为 129%，但墙钟反而更慢 —— 并行的是 I/O 等待，解析仍被 GIL 串行化并叠加了调度开销。</p>

<h3>线程扩展</h3>
{thread_chart}

<h2>四、单文件延迟（各测 2,000 个样本）</h2>
<table>
  <thead><tr><th class="lbl">实现</th><th>p50</th><th>p95</th><th>p99</th><th>平均</th></tr></thead>
  <tbody>
    <tr><td class="lbl">tinytag</td><td>{lat_tt['p50_ms']:.3f} ms</td><td>{lat_tt['p95_ms']:.3f} ms</td>
      <td>{lat_tt['p99_ms']:.3f} ms</td><td>{lat_tt['mean_ms']:.3f} ms</td></tr>
    <tr><td class="lbl">mutagen-rs（逐文件）</td><td>{lat_mrs['p50_ms']:.3f} ms</td><td>{lat_mrs['p95_ms']:.3f} ms</td>
      <td>{lat_mrs['p99_ms']:.3f} ms</td><td>{lat_mrs['mean_ms']:.3f} ms</td></tr>
  </tbody>
</table>
<p class="sub">mutagen-rs 的尾延迟更稳：p99 只有 tinytag 的 {lat_mrs['p99_ms']/lat_tt['p99_ms']*100:.0f}%。文件全部命中页缓存，因此这些数字是“解析 + 系统调用”成本，不含磁盘寻道。</p>

<h2>五、关键发现：batch_open 的元数据错位</h2>
<div class="callout">
  <b>mutagen-rs 0.2.7 的 <code>batch_open()</code> 在批量较大时，返回的标签会对应到别的文件。</b>
  以本测试集为例：读取第 100 号文件的 <code>TIT2</code>，得到的是另一个文件的标题；
  10 个文件时 0 错，100 个时 8 错，1,000 个时 528 错，10,000 个时 8,685 错。
  重复运行两次结果完全一致（不是随机竞争），且返回的每个标题确实存在于库中其它文件 —— 是“配错对象”，不是解析错误。
</div>
{batch_chart}
<p class="sub">另外用 200 个音源内容各不相同的文件复测（不同时长/码率），仍有 42 个（21%）错位，排除了“文件内容相同导致缓存串号”的解释。</p>
<p>同一批文件用逐文件接口 <code>mutagen_rs.MP3(path)</code> 读取时，10,000 条记录的 title/artist/album
与 tinytag <b>完全一致（0 差异）</b>；tinytag 自身与 mutagen-rs 逐文件通道的交叉校验同样是 0 差异。
因此可以确认问题范围限定在 <code>batch_open()</code> 这一条并行批量通道上。</p>
<div class="callout note">
  结论：<b>不能用 <code>batch_open()</code> 的结果直接建组</b>，否则界面会出现“张冠李戴”的标题与专辑。
  若要用，必须在写缓存前逐条回读校验（那就抵消了它的性能优势），或等待上游修复。
</div>

<h2>六、时间构成分析</h2>
<ul>
  <li><b>目录遍历 {R['scan_only']['median_wall_s']*1000:.0f} ms</b>：58 个目录 / 10,000 个目录项，
      单次 <code>os.scandir</code>，已接近该磁盘与 CPU 的下限。</li>
  <li><b>元数据约 {R['tinytag_1t']['median_wall_s']*1000:.0f} ms（tinytag）/ {R['mrs_1t']['median_wall_s']*1000:.0f} ms（mutagen-rs）</b>：
      单个文件 0.1–0.2 ms，主要是 open + read + ID3 解析。CPU 时间分别为
      {R['tinytag_1t']['median_cpu_s']*1000:.0f} ms 与 {R['mrs_1t']['median_cpu_s']*1000:.0f} ms，
      其余是等待 I/O —— 即使换成 Rust 实现，单线程也无法绕开这部分等待。</li>
  <li><b>建组 12–15 ms</b>：<code>add_groups_from_scan()</code> 只做字典归并与排序，已不是瓶颈。</li>
  <li><b>进度上报的代价</b>：真实 <code>ScanWorker</code>（{R['e2e_current']['median_wall_s']*1000:.0f} ms）
      比同口径流水线（{R['e2e_tinytag']['median_wall_s']*1000:.0f} ms）慢约
      {(R['e2e_current']['median_wall_s']-R['e2e_tinytag']['median_wall_s'])*1000:.0f} ms，
      主要来自约 1% 节流的跨线程 <code>progress</code> 信号与主线程事件处理。</li>
</ul>

<h2>七、建议</h2>
<h3>1. 短期：保留 tinytag，暂不替换</h3>
<p>逐文件替换只能带来 {sp_e2e:.2f}× 的端到端收益，却要引入一个需要为三端打包编译产物的 Rust 扩展
（本环境即由 sdist 现场编译）。在冷盘、大文件（数 MB）场景下，收益会被磁盘寻道进一步稀释。</p>
<h3>2. 若确实要换：只用逐文件接口，不要用 batch_open</h3>
<p>把 <code>read_tinytag()</code> 换成 <code>mutagen_rs.MP3(p)</code> 读取 <code>TIT2/TPE1/TALB</code>，
线程数保持 1（多线程无收益）。预期端到端约 {sp_e2e:.2f}×，并需要在 CI 中为三大平台构建 wheel。</p>
<h3>3. 真正的瓶颈在冷盘 I/O 与串行等待，换库解决不了</h3>
<p>v1.1.3 文档记录的冷盘 2,379 文件约 44 s，主因是每个文件一次随机寻道。更有效的手段是
<b>渐进式加载</b>：扫描完成即按目录建组并显示，元数据在后台补齐；
再辅以惰性读取（只对当前可见/排序所需字段解析）。这能把“等待 44 秒才有界面”变成“秒级可见、逐步完善”。</p>

<h2>八、测试集与方法</h2>
<table>
  <thead><tr><th class="lbl">项</th><th class="lbl">值</th></tr></thead>
  <tbody>
    <tr><td class="lbl">文件数 / 播放组</td><td class="lbl">10,000 / 58（近似 Zipf 分布，20–300 首/组）</td></tr>
    <tr><td class="lbl">总大小</td><td class="lbl">{M['size_mib']:.1f} MiB（单文件约 8.6 KiB，1 s 静音 64 kbps + ID3 标签）</td></tr>
    <tr><td class="lbl">元数据</td><td class="lbl">ID3v2.3 / v2.4 混合，部分附加 ID3v1；TIT2/TPE1/TALB/TCON/TDRC|TYER/TRCK/TPE2/TCOM/COMM/TXXX×2/TLEN；
      标题含中文、日文、俄文、emoji、多艺术家与长括号说明</td></tr>
    <tr><td class="lbl">异常样本</td><td class="lbl">300 个无标签文件 + 10 个截断损坏文件（两库均能容错返回空标签）</td></tr>
    <tr><td class="lbl">运行环境</td><td class="lbl">{M['cpu_count']} vCPU · Python {M['python']} · tinytag {M['tinytag_version']} ·
      mutagen-rs {M['mutagen_rs_version']}（源码构建）· Linux 容器 · 页缓存预热</td></tr>
    <tr><td class="lbl">方法</td><td class="lbl">同进程交替执行 {M['reps']} 轮取中位数；mutagen-rs 每轮前调用
      <code>clear_all_caches()</code>（否则其进程内缓存会让第二轮降至 1–2 ms，严重失真）；
      端到端使用真实 <code>ScanWorker</code> + <code>QEventLoop</code> 阻塞等待</td></tr>
    <tr><td class="lbl">正确性校验</td><td class="lbl">{M['consistency_checked']} 条记录逐条比对，差异 {M['consistency_mismatches']} 条</td></tr>
  </tbody>
</table>

<h2>九、局限</h2>
<ul>
  <li><b>仅热缓存</b>：容器内 <code>/proc/sys/vm/drop_caches</code> 只读，无法构造冷盘场景；真实冷 HDD 下单文件耗时约高一个数量级，库间差距会被压缩。</li>
  <li><b>文件偏小</b>：8.6 KiB/首，远小于真实 MP3（3–10 MiB）。真实场景中 I/O 占比更高，元数据解析占比更低。</li>
  <li><b>无内嵌封面</b>：未包含 APIC（避免 10,000 张封面占数百 MiB）。含大封面时解析成本会上升，两个库的相对表现可能变化。</li>
  <li><b>未测内存</b>：<code>batch_open</code> 一次性返回 10,000 条结果，峰值内存高于逐文件流式处理，本报告未量化。</li>
  <li><b>沙箱噪声</b>：3 vCPU 共享环境，个别场景轮次间波动可达 ±15%（表中同时给出了最快值与中位数）。</li>
</ul>

<h2>十、复现</h2>
<pre># 1) 安装（mutagen-rs 在默认镜像缺失，需官方源，源码构建约 1–2 分钟）
pip install "PySide6>=6.5.0" "tinytag>=1.8.0" just_playback==1.0.0 -i https://pypi.org/simple
pip install mutagen-rs -i https://pypi.org/simple

# 2) 生成测试集（10000 首，约 11 秒 / 97 MiB）
python benchmark/scripts/gen_mp3.py

# 3) 运行基准（输出 /tmp/bench_results.json）
python benchmark/scripts/bench_mp3.py</pre>

<footer>
  测试脚本与原始数据见同目录 <code>scripts/</code> 与 <code>raw-results.json</code>。
  被测代码：Lyrica v1.1.3（<code>4a5a231</code>）中的 <code>_scan_audio_tree</code>、
  <code>ScanWorker</code> 与 <code>MusicPlayer.add_groups_from_scan</code>。
</footer>

</div></body></html>
"""

path = os.path.join(OUT_DIR, "metadata-benchmark-v1.1.3.html")
with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print("written", path, len(html), "bytes")
