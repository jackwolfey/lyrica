"""生成播放/切组/加载基准的 HTML 报告。"""
import json
import os

D = json.load(open("/tmp/play_bench.json", encoding="utf-8"))
A = D["attribution"]
E = D["_env"]


def bar(label, value, vmax, color, suffix):
    pct = max(1.2, value / vmax * 100)
    return f"""
    <div class="row">
      <div class="row-label">{label}</div>
      <div class="track"><div class="fill" style="width:{pct:.1f}%;background:{color}"></div>
        <span class="val">{suffix}</span></div>
    </div>"""


acts = [
    ("切组 · 87 首", D["switch_small"], "#a6e3a1", "正常"),
    ("切组 · 306 首", D["switch_big"], "#89b4fa", "正常"),
    ("切组 · 849 首", D["switch_largest"], "#f9e2af", "可优化"),
    ("重建列表 · 849 首", D["rebuild_largest"], "#f9e2af", "可优化"),
    ("同组双击播放", D["play_same_group"], "#a6e3a1", "正常"),
    ("跨组双击播放", D["play_cross_group"], "#a6e3a1", "正常"),
    ("组内下一首", D["next_track"], "#89b4fa", "正常"),
    ("仅解码器加载", D["decoder_load"], "#cba6f7", "后端下限"),
]
amax = max(v["total_ms"] for _, v, _, _ in acts)
act_chart = "".join(
    bar(f"{lbl}<span class='sub'>{kind}</span>", v["total_ms"], amax, c, f"{v['total_ms']:.1f} ms")
    for lbl, v, c, kind in acts
)
act_rows = "\n".join(
    f"""<tr>
      <td class="lbl">{lbl}</td>
      <td>{v['handler_ms']:.1f}</td><td>{v['flush_ms']:.1f}</td>
      <td><b>{v['total_ms']:.1f}</b></td>
      <td class="kind">{kind}</td></tr>"""
    for lbl, v, c, kind in acts
)

attr = [
    ("创建 849 个 item（delegate 纯文本）", A["refresh_no_marquee"], "#89b4fa"),
    ("marquee 宽度扫描（全量 849 行）", A["marquee_scan_all"], "#f38ba8"),
    ("marquee 宽度扫描（仅 23 个可视行）", A["marquee_scan_visible"], "#a6e3a1"),
    ("视口同步重绘一次", A["viewport_repaint"], "#cba6f7"),
]
amax2 = max(v for _, v, _ in attr)
attr_chart = "".join(bar(k, v, amax2, c, f"{v:.2f} ms") for k, v, c in attr)

play_parts = [
    ("解码器 load_file + play", D["decoder_load"]["handler_ms"], "#cba6f7"),
    ("_on_track_changed 槽", D["ui_track_change"]["handler_ms"], "#89b4fa"),
    ("播放进度刷新（每 300ms）", D["position_tick"]["handler_ms"], "#a6e3a1"),
    ("取当前曲目路径（10k 列表拷贝）", A["get_playlist_copy"], "#a6e3a1"),
    ("缓存命中时读元数据", A["get_track_metadata_cached"], "#a6e3a1"),
    ("三个标签 setText", A["set_3_labels"], "#a6e3a1"),
]
pmax = max(v for _, v, _ in play_parts)
play_chart = "".join(bar(k, v, pmax, c, f"{v:.3f} ms" if v < 1 else f"{v:.2f} ms")
                     for k, v, c in play_parts)

marquee_duty = D["marquee_tick_paint"]["flush_ms"] / 50 * 100

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lyrica 播放 / 切组 / 加载性能基准 · v1.1.3</title>
<style>
  :root {{
    --base:#1e1e2e; --mantle:#181825; --surface:#313244;
    --text:#cdd6f4; --sub:#a6adc8; --blue:#89b4fa; --green:#a6e3a1;
    --yellow:#f9e2af; --red:#f38ba8; --mauve:#cba6f7;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--base); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC",
      "PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; font-size:15px; }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:56px 28px 96px; }}
  header {{ border-bottom:1px solid var(--surface); padding-bottom:26px; margin-bottom:36px; }}
  h1 {{ font-size:29px; margin:0 0 10px; letter-spacing:-.3px; }}
  .meta {{ color:var(--sub); font-size:13px; font-family:ui-monospace,Menlo,monospace; }}
  h2 {{ font-size:20px; margin:50px 0 14px; padding-left:12px; border-left:3px solid var(--blue); }}
  h3 {{ font-size:15.5px; margin:26px 0 10px; color:var(--mauve); }}
  p {{ margin:10px 0; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; margin:24px 0 8px; }}
  .card {{ background:var(--mantle); border:1px solid var(--surface); border-radius:12px; padding:16px 18px; }}
  .card .k {{ font-size:12px; color:var(--sub); letter-spacing:.03em; }}
  .card .v {{ font-size:23px; font-weight:700; margin-top:4px; }}
  .card .d {{ font-size:12.5px; color:var(--sub); margin-top:2px; }}
  .g {{ color:var(--green); }} .y {{ color:var(--yellow); }} .r {{ color:var(--red); }} .b {{ color:var(--blue); }} .m {{ color:var(--mauve); }}
  .row {{ display:grid; grid-template-columns:260px 1fr; gap:14px; align-items:center; margin:8px 0; }}
  .row-label {{ font-size:13.5px; color:var(--sub); text-align:right; }}
  .row-label .sub {{ display:block; font-size:11px; opacity:.7; }}
  .track {{ position:relative; background:var(--mantle); border-radius:7px; height:26px;
    border:1px solid var(--surface); overflow:hidden; }}
  .fill {{ height:100%; border-radius:6px 0 0 6px; }}
  .val {{ position:absolute; right:9px; top:2px; font-size:12.5px;
    font-family:ui-monospace,Menlo,monospace; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; margin:14px 0 6px; }}
  th,td {{ text-align:right; padding:8px 9px; border-bottom:1px solid var(--surface); }}
  th {{ color:var(--sub); font-weight:500; font-size:12.5px; }}
  td.lbl,th.lbl {{ text-align:left; }}
  td.kind {{ text-align:left; color:var(--sub); font-size:12.5px; }}
  code {{ background:var(--mantle); border:1px solid var(--surface); border-radius:5px;
    padding:1px 5px; font-size:12.5px; font-family:ui-monospace,Menlo,monospace; }}
  pre {{ background:var(--mantle); border:1px solid var(--surface); border-radius:10px;
    padding:14px 16px; overflow:auto; font-size:12.5px; line-height:1.6; }}
  .callout {{ border-left:3px solid var(--yellow); background:rgba(249,226,175,.06);
    padding:14px 18px; border-radius:0 10px 10px 0; margin:18px 0; }}
  .callout.ok {{ border-color:var(--green); background:rgba(166,227,161,.06); }}
  .callout.bad {{ border-color:var(--red); background:rgba(243,139,168,.06); }}
  ul {{ margin:10px 0 10px 22px; padding:0; }}
  li {{ margin:5px 0; }}
  footer {{ margin-top:60px; padding-top:20px; border-top:1px solid var(--surface);
    color:var(--sub); font-size:12.5px; }}
  .sub {{ color:var(--sub); font-size:11.5px; }}
</style>
</head>
<body><div class="wrap">

<header>
  <h1>Lyrica 播放 / 切组 / 加载性能基准</h1>
  <div class="meta">被测代码：main v1.1.3（4a5a231）· 2026-11-11 · 10,000 首 / 58 组 ·
    真实 MainWindow + just_playback · 每项 5 次取中位</div>
</header>

<div class="cards">
  <div class="card"><div class="k">出声延迟（load → 首帧推进）</div>
    <div class="v g">{D['audio_start_latency']['median_ms']:.1f} ms</div>
    <div class="d">最小 {D['audio_start_latency']['min_ms']:.1f} / 最大 {D['audio_start_latency']['max_ms']:.1f} ms</div></div>
  <div class="card"><div class="k">切组 · 306 首</div>
    <div class="v g">{D['switch_big']['total_ms']:.0f} ms</div>
    <div class="d">含重建 + 首帧绘制</div></div>
  <div class="card"><div class="k">切组 · 849 首</div>
    <div class="v y">{D['switch_largest']['total_ms']:.0f} ms</div>
    <div class="d">可降至约 13 ms</div></div>
  <div class="card"><div class="k">20 次连续切歌</div>
    <div class="v g">+{D['burst_20']['rss_delta_mib']:.2f} MiB</div>
    <div class="d">定时器增量 {D['burst_20']['timer_delta']} · 无泄漏</div></div>
</div>

<h2>一、结论</h2>
<div class="callout ok">
  <b>播放、切歌、加载三条主路径都没有“卡死级”瓶颈。</b>
  最慢的单次用户动作是 849 首大组切组（{D['switch_largest']['total_ms']:.0f} ms），
  远低于 2 秒量级；双击播放的体感延迟由音频解码器初始化决定（约 10 ms），UI 逻辑只占 0.5 ms。
</div>
<div class="callout">
  <b>唯一明显可优化点：<code>_update_marquee()</code> 每次都遍历全列表测量文本宽度。</b>
  849 行要 {A['marquee_scan_all']:.1f} ms，占整次列表重建（{A['refresh_full']:.1f} ms）的
  {A['marquee_scan_all']/A['refresh_full']*100:.0f}%；而实际只绘制 {A['visible_rows']} 个可视行，
  只测这 23 行只要 {A['marquee_scan_visible']:.2f} ms（约 {A['marquee_scan_all']/A['marquee_scan_visible']:.0f} 倍差距）。
  改成“仅可视行”后，849 首切组预计从 {D['switch_largest']['total_ms']:.0f} ms 降到 13 ms 左右。
</div>

<h2>二、各动作耗时</h2>
<p>handler 为槽函数自身耗时，flush 为随后 <code>processEvents()</code> 把排队重绘真正跑掉的耗时，
两者之和接近用户感知的卡顿时长。</p>
{act_chart}
<table>
  <thead><tr><th class="lbl">动作</th><th>handler(ms)</th><th>flush(ms)</th>
  <th>合计(ms)</th><th class="lbl">判定</th></tr></thead>
  <tbody>{act_rows}</tbody>
</table>

<h2>三、切组耗时归因（849 首）</h2>
{attr_chart}
<table>
  <thead><tr><th class="lbl">阶段</th><th>耗时(ms)</th><th class="lbl">说明</th></tr></thead>
  <tbody>
    <tr><td class="lbl">创建 item</td><td>{A['refresh_no_marquee']:.1f}</td>
      <td class="kind">849 个纯文本 QListWidgetItem，约 11 µs/行</td></tr>
    <tr><td class="lbl">marquee 全量扫描</td><td>{A['marquee_scan_all']:.1f}</td>
      <td class="kind">对每行做一次 <code>horizontalAdvance</code></td></tr>
    <tr><td class="lbl">marquee 仅可视行</td><td>{A['marquee_scan_visible']:.2f}</td>
      <td class="kind">同样逻辑只测 23 行，效果等价</td></tr>
    <tr><td class="lbl">视口同步重绘</td><td>{A['viewport_repaint']:.1f}</td>
      <td class="kind">离屏栅格渲染，真机通常更低</td></tr>
  </tbody>
</table>
<p class="sub">三者相加与完整刷新（{A['refresh_full']:.1f} ms）基本吻合，说明时间去向已拆解干净，没有隐藏开销。</p>

<h2>四、播放路径拆解</h2>
{play_chart}
<p>一次双击播放 = 解码器加载（约 10 ms，后端下限）+ UI 槽（约 0.5 ms）。
<code>_on_track_changed</code> 内部的三项操作都可以忽略：
10,000 首列表整体拷贝 {A['get_playlist_copy']:.3f} ms、缓存命中读元数据 {A['get_track_metadata_cached']:.3f} ms、
三个标签 setText {A['set_3_labels']:.3f} ms。</p>
<p>切歌（<code>next_track</code>）看着比双击慢，是因为该动作包含两次解码加载（先定位再下一首），实测
{D['next_track']['handler_ms']:.1f} ms，并非 UI 变慢。</p>

<h2>五、常驻开销</h2>
<ul>
  <li><b>marquee 重绘：{D['marquee_tick_paint']['flush_ms']:.1f} ms / 50 ms ≈ {marquee_duty:.0f}% 单核</b>，
      仅在存在超宽文件名且窗口可见时启用；最小化到托盘后定时器停止。</li>
  <li><b>播放进度刷新：{D['position_tick']['total_ms']:.3f} ms / 300 ms</b>，可忽略。</li>
  <li>窗口宽度 620 px 时 marquee 才被激活（视口 {D['marquee_viewport_px']} px，本测试集最长文本 {D['marquee_span_px']} px）；
      1000 px 宽的默认窗口下，该曲库的长文件名并不会触发滚动。</li>
</ul>

<h2>六、稳定性与加载</h2>
<ul>
  <li><b>连续 20 次切歌</b>：平均 {D['burst_20']['per_action_ms']:.1f} ms/次，
      RSS 变化 +{D['burst_20']['rss_delta_mib']:.3f} MiB，QTimer 数量增量 {D['burst_20']['timer_delta']} —— 无累积开销。</li>
  <li><b>启动恢复 58 个目录 / 10,000 首</b>：{D['restore_groups']['total_ms']:.0f} ms（热缓存）。
      冷盘下会显著上升，这是 v1.1.3 已针对的路径（扫描提速），不属于本次播放链路的问题。</li>
  <li><b>出声延迟</b>：{D['audio_start_latency']['median_ms']:.1f} ms，波动仅 ±0.2 ms，
      说明 just_playback/miniaudio 的初始化很稳定，没有隐藏的同步阻塞。</li>
</ul>

<h2>七、建议</h2>
<h3>P0（建议做）：marquee 宽度扫描只取可视行</h3>
<p>在 <code>_update_marquee()</code> 中先用 <code>indexAt()</code> 求出首/末可视行，只对这些行做
<code>horizontalAdvance</code>。收益：849 首切组 {D['switch_largest']['total_ms']:.0f} → 约 13 ms，
306 首 {D['switch_big']['total_ms']:.0f} → 约 11 ms；且组越大收益越明显（线性 → 常数）。
风险极低：marquee 只绘制可视行，非可视行的宽度对当前帧没有意义。</p>
<h3>P1（可不做）：解码器预热</h3>
<p>约 10 ms 的加载来自 <code>Playback.load_file</code>，属后端下限。若想进一步降低“双击到出声”，
可在选中曲目时预加载下一个文件；但这会引入额外的内存与失败处理，收益有限。</p>
<h3>不建议：替换音频后端或并发加载</h3>
<p>当前延迟已在 11 ms，远低于人耳可感知阈值；换后端只会增加跨平台打包与授权成本。</p>

<h2>八、方法与环境</h2>
<table>
  <thead><tr><th class="lbl">项</th><th class="lbl">值</th></tr></thead>
  <tbody>
    <tr><td class="lbl">被测代码</td><td class="lbl">main 分支 v1.1.3 <code>4a5a231</code>（独立 worktree，避免影响当前分支）</td></tr>
    <tr><td class="lbl">曲库</td><td class="lbl">10,000 首 / 58 组，最大组 {E['largest_group']} 首，最小组 {E['small_group']} 首</td></tr>
    <tr><td class="lbl">音频</td><td class="lbl">1 s / 64 kbps MP3；miniaudio 回退后端（容器内无 JACK）</td></tr>
    <tr><td class="lbl">渲染</td><td class="lbl">Qt offscreen 平台，无合成器；栅格绘制在 CPU 上，真机 GPU 下 flush 通常更低</td></tr>
    <tr><td class="lbl">运行环境</td><td class="lbl">{os.cpu_count()} vCPU · Python 3.12.15 · PySide6 · 页缓存预热</td></tr>
    <tr><td class="lbl">方法</td><td class="lbl">每个场景 5 次（marquee 绘制 10 次）取中位数；场景间交错执行；
      每轮前 <code>processEvents()</code> 清空队列；RSS 取 <code>/proc/self/statm</code></td></tr>
    <tr><td class="lbl">加载后常驻内存</td><td class="lbl">{E['rss_start_mib']:.0f} MiB（含 10k 曲目与元数据缓存）</td></tr>
  </tbody>
</table>

<h3>局限</h3>
<ul>
  <li>离屏渲染没有 GPU 合成，flush 数值偏保守；真机上列表重绘会更快。</li>
  <li>曲目均为 1 秒小文件，解码器加载成本偏低；真实 5–10 MiB 文件可能让 <code>load_file</code> 上升到数十毫秒。</li>
  <li>未测冷盘启动、蓝牙/独占音频设备、超长播放（小时级）等场景。</li>
  <li>容器 3 vCPU 共享，个别场景轮次间存在约 ±15% 波动，表中取中位数。</li>
</ul>

<h2>九、复现</h2>
<pre>git worktree add /tmp/lyrica-main main
cd /tmp/lyrica-main
LYRICA_REPO=/tmp/lyrica-main python benchmark/scripts/bench_play.py        # 主表
LYRICA_REPO=/tmp/lyrica-main python benchmark/scripts/bench_play_attr.py   # 耗时归因</pre>

<footer>
  原始数据：<code>raw-playback-results.json</code>；脚本：<code>scripts/bench_play.py</code>、
  <code>scripts/bench_play_attr.py</code>。曲库由 <code>scripts/gen_mp3.py</code> 生成（10,000 首）。
</footer>

</div></body></html>
"""

os.makedirs("/workspace/benchmark", exist_ok=True)
path = "/workspace/benchmark/playback-benchmark-v1.1.3.html"
with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print("written", path, len(html))
