# Lyrica 架构文档

> 轻量级本地音乐播放器 · Python + PySide6 + just_playback
> 
> 版本历史：v0.1 → v0.2 → v0.3 → v1.0 → v1.0.1 → v1.0.2 → v1.0.3 → v1.0.4 → v1.0.5 → v1.1.0 → v1.1.1 → v1.1.2 → **v1.1.3**

---

## 一、项目结构

```
lyrica/
├── main.py                入口 + Catppuccin Mocha QSS 样式
├── Lyrica.spec            PyInstaller 单文件打包配置
├── requirements.txt       运行依赖
├── requirements-dev.txt   开发依赖（pytest / pytest-cov）
├── pytest.ini             pytest 配置（testpaths=tests）
├── .github/workflows/     ci.yml（测试）+ release.yml（tag 触发三端打包发布）
├── tests/                 pytest 测试套件（conftest 提供假音频后端）
│   ├── conftest.py            FakePlayback / 离屏 QApplication / 公共夹具
│   ├── test_player_groups.py  播放组模型：扫描、去重、排序、移动、恢复
│   ├── test_player_playback.py 播放状态机：循环、切歌、seek、恢复位置
│   ├── test_metadata.py       元数据读取与缓存
│   ├── test_scan_worker.py    ScanWorker 增量并行扫描
│   ├── test_main_window.py    主窗口、托盘、右键菜单、持久化
│   └── test_main.py           程序入口与 QSS
└── src/
    ├── __init__.py
    ├── player.py          播放核心（MusicPlayer + PlaylistGroup + metadata 缓存）
    └── main_window.py     UI 全量（双面板 + 托盘 + ScanWorker + 持久化）
```

---

## 二、依赖关系

```
                    ┌──────────────────┐
                    │    main.py       │  入口 + QSS 样式表
                    └────────┬─────────┘
                             │ 实例化
                    ┌────────▼─────────┐
                    │   MainWindow     │  UI 控制层（src/main_window.py）
                    └────────┬─────────┘
                             │ 持有
                    ┌────────▼─────────┐
                    │   MusicPlayer    │  播放引擎（src/player.py）
                    └────┬──────┬──────┘
                         │      │
             ┌───────────▼──┐  ┌▼────────────┐
             │ just_playback │  │   tinytag    │
             │ (miniaudio)  │  │  (元数据读)    │
             └──────────────┘  └───────────────┘

后台线程：
  ScanWorker(QThread)  ──►  Scan 文件夹 + ThreadPoolExecutor 预读 metadata
```

| 依赖 | 版本 | 用途 |
|------|------|------|
| PySide6 | ≥ 6.5.0 | Qt6 Python 绑定（UI + 信号槽） |
| just_playback | == 1.0.0 | 音频播放后端（基于 miniaudio C 库，cffi 绑定；`SUPPORTED_AUDIO_FORMATS` 从此版本起公开） |
| tinytag | ≥ 1.8.0 | 读取音频文件元数据（标题/艺术家/专辑/时长） |

---

## 三、核心数据模型

```python
@dataclass
class PlaylistGroup:
    name: str                     # 显示名（文件夹名）
    folder_path: str              # 该文件夹的绝对路径
    tracks: List[str]             # 该文件夹下音乐文件绝对路径（排序后）

class MusicPlayer(QObject):
    # 组数据模型（v0.3 引入）
    _groups: List[PlaylistGroup]          # 左侧"播放组"
    _active_group_index: int = -1         # 当前激活的组索引

    # 平铺数据模型（播放控制用）
    _playlist: List[str]                  # 所有组的 tracks 顺序拼接
    _current_index: int = -1              # 当前曲目在平铺列表中的索引

    # 播放引擎
    _playback: Playback                   # just_playback 实例

    # 缓存
    _meta_cache: Dict[str, Dict[str, Optional[str]]]   # 元数据 → 持久化到 JSON
```

**为什么有两套列表？**
- `_groups`：给 UI 展示用，左侧面板一个 PlaylistGroup = 一个文件夹
- `_playlist`（平铺）：给播放控制用，上一曲 / 下一曲 / 各种循环模式只需要操作这一个一维列表

组内曲目变化 → 调 `_rebuild_flat_playlist()` 重新平铺。

---

## 四、关键类与信号槽

### MusicPlayer 信号

```python
position_changed  # float，当前播放位置（秒）— 驱动进度条
duration_changed   # float，曲目总时长
state_changed      # bool，True=正在播放 / False=暂停
track_changed      # int，平铺列表索引 — 高亮当前曲目
playlist_changed   # 无参数，平铺列表变了 → 刷新右侧曲目面板
groups_changed     # 无参数，组列表变了 → 刷新左侧播放组面板
active_group_changed  # int，当前组索引
play_mode_changed  # PlayMode，播放模式切换
volume_changed     # float
error_occurred     # str，错误消息
```

### MainWindow 核心职责

| 职责 | 实现 |
|------|------|
| UI 布局 | QSplitter 左（QListWidget 播放组）+ 右（QListWidget 曲目）+ 底部控制栏 |
| 播放控制 | 持有 MusicPlayer 实例，按钮点击调 player.play()/pause() 等 |
| 右键菜单 | 标题栏（托盘开关 / 关于 / 退出）、播放组（添加文件夹 / 排序 / 上移下移）、曲目（排序） |
| 系统托盘 | QSystemTrayIcon + 程序化 QPixmap 音符图标 |
| 后台扫描 | ScanWorker(QThread) — 一次 `os.scandir` 递归 + ThreadPoolExecutor 4 线程预读 metadata |
| 持久化 | `lyrica_settings.json` — 窗口大小 / 播放状态 / 播放列表 / meta_cache |

### ScanWorker 算法（v1.0.3 增量并行，v1.1.3 合并遍历）

```
Step 1: 一次 os.scandir 递归，同时产出 {音频绝对路径: mtime}
        （替代旧的 os.walk + 逐目录 listdir/isfile + 逐文件 getmtime）
Step 2: 已有 _meta_cache 的文件 → 直接复用，跳过 TinyTag；缺 _mtime 的用 Step 1 结果补齐
Step 3: need_read 的 → ThreadPoolExecutor(max_workers=4) 并行读，
        TinyTag.get(p, duration=False) 跳过逐帧时长扫描
Step 4: finished_ok 携带 meta_cache（其 key 即全部音频路径）→
        player.add_groups_from_scan() 直接按 dirname 建组，不再二次遍历磁盘
```

---

## 五、持久化 JSON 结构

> 下方为便于阅读的缩进示意；实际 `lyrica_settings.json` 是紧凑单行 JSON（无缩进、无多余空格），仅供程序读写。写入时先整体序列化到内存、单次写入同目录 `lyrica_settings.json.tmp`，再用 `os.replace()` 原子替换正本，崩溃不会留下半截坏 JSON；异常时静默降级并清理临时文件（v1.1.3）。

```json
{
  "version": "1.1.3",
  "play_mode": "LIST_LOOP",
  "volume": 0.7,
  "window_size": {"w": 800, "h": 500},
  "groups": [
    {"folder_path": "/path/to/music/A"},
    {"folder_path": "/path/to/music/B"}
  ],
  "active_group_index": 0,
  "current_index": 5,
  "position": 124.3,
  "minimize_to_tray": true,
  "meta_cache": {
    "/path/to/music/A/song1.mp3": {
      "title": "Song One",
      "artist": "Alice",
      "album": "Demo",
      "duration": "240.5"
    }
  }
}
```

> `duration` 由 `MusicPlayer.get_track_metadata()` 在缓存未命中时写入；`ScanWorker` 预读的条目为 `null`，表示「未解析」。界面时长一律取自播放后端，该字段目前只写不读。

**重启恢复链路**：
```
QApplication 启动
  → MainWindow.__init__
    → _load_settings() 读 JSON
      → player.restore_groups(groups)      重建 PlaylistGroup
      → player._meta_cache = meta_cache     还原 metadata 缓存
      → player.restore_paused_position(pos) 恢复播放位置（内部 play→seek→pause 三步曲）
```

---

## 六、版本演进

### v0.1（初版）
> 基础播放能力
- 播放 / 暂停 / 停止
- 上一曲 / 下一曲
- 音量调节（QSlider）
- 进度条 + 拖动跳转
- 三种播放模式：顺序 / 单曲循环 / 列表循环
- 添加文件夹 → 单一播放列表

### v0.2
> 持久化 + UI 收敛
- JSON 配置文件持久化：窗口大小 / 播放状态 / 播放列表 / position
- position 恢复：关闭时存当前曲目位置，下次启动 seek 回来
- 右键菜单化：移除播放列表区域按钮，功能移到右键菜单
- 整体 UI 紧凑化

### v0.3
> 播放组模型 + 元数据
- **引入 PlaylistGroup**：每个含音乐文件的子文件夹 = 一个播放组
- 双面板 UI：左侧播放组列表，右侧曲目列表
- 音频元数据展示（标题 / 艺术家 / 专辑）
- 曲目排序：按文件名 / 标题 / 艺术家 / 专辑（右键菜单）
- 播放组排序：按默认 / 上移 / 下移（右键菜单）

### v1.0
> 系统托盘 + 关于对话框
- QSystemTrayIcon 程序化生成音符图标
- 关闭窗口 → 最小化到托盘（hide() + 托盘通知）
- 托盘开关配置（标题右键菜单可切换）
- 关于对话框展示版本号
- ScanWorker(QThread)：添加文件夹时后台扫描，主线程不卡

### v1.0.1
> Bug 修复
- **托盘图标空**：之前 `windowIcon()` 返回空 QIcon，改为 QPixmap + QPainter 程序化生成
- **position 恢复无效**：just_playback 的 seek 仅在 `active=True` 时生效，必须 play → seek → pause 三步
- **QApplication 未导入**：_tray_quit 里 `QApplication.quit()` NameError

### v1.0.2
> UI 批量刷新 + metadata 缓存
- **QListWidget 批量刷新**：`setUpdatesEnabled(False)` + `addItems()` + `setUpdatesEnabled(True)`，10-30x 加速
- **filename 排序跳过 TinyTag**：直接用 basename.lower()，零 IO
- **ScanWorker 预取 metadata**：扫描时顺便读 TinyTag，摊进等待时间里

### v1.0.3
> ScanWorker 增量并行 + meta_cache 持久化 + 强制退出
- **ScanWorker 增量模式**：传入已有 `_meta_cache` → 跳过已缓存文件
- **ThreadPoolExecutor 并行**：need_read 的文件 8 线程 TinyTag.get()，IO 密集并行加速（v1.1.3 起收敛为 4 线程，实测 4 线程即饱和）
- **meta_cache 持久化到 JSON**：重启后排序直接命中热缓存
- **强制退出**：标题右键菜单「退出」+ Ctrl+Q，绕过最小化到托盘开关
- 托盘通知缩短为 800ms

### v1.0.4
> 视图跳转修复 + 列表视觉收敛
- **组上移/下移不再跳转视图**：`_swap_groups` 末尾移除 `track_changed.emit` —— 组移动是结构性变更，不是切歌，不应触发"高亮播放组/播放曲目"的切歌语义 UI 动作
- **曲目排序同理**：`sort_group_tracks` 末尾移除 `track_changed.emit`
- **`_refresh_tracks_list` 补回高亮**：列表重建后，若正在播放的曲目属于当前显示组，恢复 `currentRow`（此前只有切歌才会恢复）
- **高亮条件收紧**：仅当 `is_playing() or is_paused()` 成立时才恢复高亮，避免"仅添加文件、从未播放"时误高亮第 0 行
- **QSS 选中项改圆角蓝底**：`QListWidget::item:selected` 从淡粉实心改为 `#89b4fa` 实心 + 深色字，左右圆角框
- **列表紧凑化**：item `padding` 4→2、垂直 `margin` 归零，同高度可视行数 +4~6
- **滑块手柄改白**：`QSlider::handle:horizontal` 从 `#f5c2e7` 改为 `#ffffff`（进度条与音量条共用）
- **播放组文字贴左**：移除 `_refresh_groups_list` 里的两个前导空格，QSS 左侧 padding 6→3

### v1.0.5
> 布局解耦 + UI 细节打磨 + 文档完善
- **控制栏彻底居中**：底部控制区从 `QHBoxLayout` 重构为 `QGridLayout`。将左（模式选择）、中（播放控制）、右（音量）分别放入同一个 Grid 的 `(0,0)` 格子并通过 `Qt.Align` 独立对齐。彻底解决了左侧下拉框宽度变化导致中间播放按钮偏移的问题。
- **下拉框自适应宽度**：`cmb_mode` 移除硬编码的 `120px`，改为根据 `fontMetrics().horizontalAdvance()` 动态计算最长文本宽度，视觉更紧凑。
- **列表长文本防溢出**：`groups_list` 和 `tracks_list` 设置 `ScrollBarAlwaysOff` 和 `ElideRight`，防止文件名过长出现横向滚动条破坏 UI。
- **关于对话框升级**：增加项目 GitHub 主页链接展示。
- **代码风格全面规范化**：清理了全项目约 50 处使用分号 `;` 连接的多语句单行代码，重构为标准换行，大幅提升代码可读性。
- **README 补全**：新增详细的「🎮 使用说明」章节，涵盖添加歌曲、播放控制、托盘驻留及状态保存机制。
- **版本管理**：清理了 `main.py` 中硬编码的版本号，统一由 `main_window.py` 的 `APP_VERSION` 控制。

### v1.1.0
> 播放范围语义重构 + 元数据 TTL + 快捷键 + 视觉定位 + 跨平台自动发布
- **播放模式拆为五种**：`SEQUENCE`（组内顺序）/ `LIST_LOOP`（组内循环）/ `CROSS_SEQUENCE`（跨组顺序）/ `CROSS_LIST_LOOP`（跨组循环）/ `SINGLE_LOOP`（单曲循环）。新增 `_is_cross_mode()` 与 `_get_play_range()`，上一曲/下一曲/自动续播统一按当前模式的播放范围判断，修正了此前"组内模式实际作用于全曲库"的语义错位
- **meta_cache TTL**：缓存条目记录文件 `mtime`，查询时比对；文件被重新打标签（mtime 变化）自动重读 TinyTag，旧缓存条目（无 `_mtime`）自动补齐，保证失效链路完整
- **窗口内全局快捷键**：空格播放暂停、←/→ 快退快进 5s、↑/↓ 音量 ±5%、`,`/`.` 切换上/下一曲（`QShortcut` 绑定在主窗口）
- **正在播放视觉标记**：自定义 `PlayingItemDelegate` 为播放中的组和曲目叠加空心圆角矩形，与普通选中态区分
- **标题栏定位**：左键单击标题栏 → 自动切回播放中的播放组、滚动并高亮当前曲目（走 `player.set_active_group()` 保证数据与视觉同步）
- **超长文本滚动**：新增 `_MarqueeLabel`，标题栏、曲目名、艺术家 · 专辑、曲目列表行在文本超宽时横向循环滚动，窗口拉宽后自动恢复居中静态
- **扩展名来源收敛**：音频格式以 `just_playback.SUPPORTED_AUDIO_FORMATS`（1.0.0 起公开）为唯一来源，经格式名→扩展名映射得到 `.mp3 .wav .flac .ogg .opus`；扫描与播放过滤共用 `SUPPORTED_EXTENSIONS`，不再收录后端无法解码的 `.m4a .aac .wma`
- **依赖收紧**：`just_playback` 固定为 `== 1.0.0`，Python 最低版本提升到 3.9
- **跨平台构建 + 自动发布**：`Lyrica.spec` 适配 Windows / macOS / Linux（macOS 生成 `.app` 且关闭 UPX），新增 tag 触发的 GitHub Actions 流水线，三端产物并行打包并自动创建 Release

### v1.1.1
> Bug 修复：播放组"正在播放"圆角框更新滞后
- **问题**：切换播放组后，左侧播放组面板的"正在播放"空心圆角框不即时跟随，需滚动/悬停/缩放窗口后才跳到正确位置
- **成因**：圆角框由 `PlayingItemDelegate.paint()` 现查 `player` 状态绘制，属被动绘制；播放行变化时全项目无任何 `viewport().update()` 主动失效，此前仅依赖 `_on_track_changed` 中 `groups_list.setCurrentRow()` 的选中态副作用触发重绘。当目标行**已是选中行**时（用户先点选组再双击其中曲目，或 `play_track_in_group()` 先 emit `active_group_changed` 已同步选中），`setCurrentRow` 为 no-op，旧行残留 / 新行缺失。右侧曲目面板因 `active_group_changed` 触发整表 `_refresh_tracks_list()` 重建而总能即时刷新，故只有左侧出现滞后
- **修复**：在 `_connect_signals()` 中把 `track_changed` / `active_group_changed` 直连到两个列表 `viewport().update()`，信号到达即排队重绘并自动合并，千行量级仍是毫秒级开销
- **回归测试**：新增 `test_playing_marker_viewport_update_on_track_change`，用事件过滤器统计 viewport 收到的 `UpdateRequest`，精确复现"目标行即选中行"场景（修复前收到 0 个重绘请求），测试总数 176 → 178

### v1.1.2
> 性能与交互修复：大播放组切换卡顿、双击播放后列表重建/高亮沉底
- **卡顿根因（三处叠加）**：① `_on_group_selected()` 在 `set_active_group()` 触发 `active_group_changed → _on_active_group_external` 刷新之后**又手动刷新一次**，每次切组重建两遍；② 曲目行用 `setItemWidget(_MarqueeLabel)`，208 行 = 208 个子控件 + 207 个 40ms 独立 `QTimer`，常驻事件循环（实测 1 秒内 10ms 探针只能触发 49 次，约 50% 饱和）；③ `_sync_track_row_colors()` 对每行调用 `setStyleSheet()`，新行全部触发样式重新 polish
- **列表重建根因**：`play_track_in_group()` 无条件 emit `active_group_changed`，于是**每次双击播放同组曲目都会整表 clear + 重建**，滚动位置随之归零，`setCurrentRow()` 再把目标行滚到视口底部
- **修复一：快照短路**。`_refresh_tracks_list()` 以 `(组索引, 曲目路径元组)` 作为快照 key，内容未变直接 return —— 同组播放/重复点选零重建，滚动位置与选中态保持；同组内的结构性变化（排序等）重建后恢复原滚动值
- **修复二：去 itemWidget**。曲目行改为纯文本 `QListWidgetItem`（路径存 `UserRole`），文字、选中色、超长 marquee、播放圆角描边全部由 `PlayingItemDelegate` 绘制；`_sync_track_row_colors()` 删除（QSS `::item:selected` 直接生效）
- **修复三：单一时钟**。曲目列表共享一个 `_marquee_offset` + 一个 50ms `QTimer`，仅当存在超宽文本且窗口可见时启动，隐藏到托盘自动停止；`groups_list` 不启用 marquee
- **修复四：去重复刷新**。`_on_group_selected()` 只调 `set_active_group()`，统一由 `_on_active_group_external()` 刷新
- **修复五：叠加文本消除**。`QStyledItemDelegate.paint()` 内部会再次调用 `initStyleOption()`，外部清空 `opt.text` 会被重新填回，导致静止文本与滚动文本重叠；改为在 delegate 内覆写 `initStyleOption()`，超宽行在源头清空默认文本，只保留 marquee 绘制
- **实测收益（离屏环境）**：切到 208 首长文件名播放组 250ms → **5.5ms**；常驻定时器 207 → **0**（按需 1 个）；事件循环探针 49/100 → **99/100**；同组双击重建 1 次 → **0 次**，滚动位置不变
- **测试**：删除基于 itemWidget 的取色测试，新增 4 个回归用例（文本 item 无控件、超宽行默认文本清空、内容未变不重建、同组播放不滚动不重建），总数 178 → 181

### v1.1.3（当前）
> 「添加文件夹」扫描性能 + 配置写入健壮性 + 扫描进度/取消 + 发布前一致性收敛
- **P0 去掉 duration 解析**：`ScanWorker` 改用 `TinyTag.get(p, duration=False)`。默认的 `duration=True` 会让 TinyTag 逐 4 字节、逐帧扫描整个 MP3（纯 Python 循环，GIL 下多线程几乎无收益），实测占首次添加耗时的绝大部分；而 `meta_cache["duration"]` 在生产代码中只写不读（界面时长来自 `just_playback`），属纯浪费。全量 2379 文件按路径比对确认 title/artist/album 零差异。`null` 定义为「未解析」，将来若要做时长排序再惰性回填。
- **P1 合并重复遍历**：旧流程 `ScanWorker` 的 `os.walk` 结果只用于读 metadata，路径被丢弃；`on_ok` 又调 `add_folder_recursive()` 二次 walk + 逐目录 `listdir`/`isfile`，并因旧缓存缺 `_mtime` 而逐文件 `getmtime`。现改为一次 `os.scandir` 递归同时拿到路径 / `is_file` / `mtime`，`finished_ok` 携带的 `meta_cache` 的 key 直接供新增的 `MusicPlayer.add_groups_from_scan()` 按 `dirname` 建组（信号签名不变）。`add_folder_recursive()` 保留为公开 API，两者共用 `_append_groups_from_folder_map()`。
- **P2 并发度 8 → 4**：2379 文件实测 1/2/4/8/16 线程 = 1880/1045/1022/1037/1044 ms，4 线程即饱和；更少的并发读也降低了冷盘 + 高并发读时非必现崩溃的概率。
- **P3 进度反馈与可中断取消**：`ScanWorker` 新增 `progress = Signal(int, int)`，缓存命中的文件直接计入「已就绪」，Step3 按约 1% 节流上报（2379 文件实测 105 次而非 2380 次）；等待对话框加状态文本与 `QProgressBar`。取消按钮从「只忽略结果」改为调用 `QThread.requestInterruption()`，Step3 的 `as_completed` 循环检查中断、`cancel()` 未开始的 futures 并立即返回，不再 emit 完成信号（真实曲库实测取消后 0.1–0.4 s 退出，而非跑满 44 s 冷读）。对象清理改由 `worker.finished.connect(worker.deleteLater)` 负责，避免线程仍在退出过程中被销毁。**坑**：Qt 的 `isInterruptionRequested()` 仅在 QThread 真正 `start()` 后有效，同步直接调 `run()` 恒为 False，中断相关测试必须起真实线程。
- **配置写入：紧凑 + 单次写 + 原子替换**：`_save_settings()` 原用 `json.dump(data, f, indent=2)` 直接写文件对象，600 KB 配置会产生上万次小 write；改为 `json.dumps(..., separators=(",", ":"))` 一次成型、单次 `write()` 到同目录 `.tmp`、再 `os.replace()` 原子替换，`finally` 清理临时文件，保留 `except OSError` 静默降级。数据内容与 `_load_settings()` 完全兼容、无需迁移；写盘 47 ms → **14 ms（3.4x）**、体积 582 KB → **479 KB（-17.7%）**，并获得「不会写坏正本」的原子性（未做 fsync，不保证断电持久性）。`.gitignore` 增加 `lyrica_settings.json.tmp`。注意此改动**没有**解决「明文可编辑」与「每次退出全量重写」——这两项仍留待冷热拆分 / SQLite 方案。
- **文件格式对齐**：`_on_add_files()` 的文件对话框过滤器改为由 `SUPPORTED_EXTENSIONS` 动态生成，不再硬编码 `.m4a / .aac / .wma`（旧版会让用户选中后被 `add_files()` 静默丢弃），与 README 的支持格式说明及扫描过滤逻辑统一。
- **发布一致性收敛**：`APP_VERSION`、macOS bundle 的 4 处版本号、第五节 JSON 示例统一为 `1.1.3`；修正 `release.yml` 的 Release 正文与第九节打包说明中残留的 `ubuntu-20.04 / glibc 2.31 / strip=True + upx=True` 描述——实际为 `ubuntu-22.04 / glibc 2.35`、Windows 关 strip、三端全关 UPX。
- **实测**（`D:\01MUSIC`，58 目录 / 2379 音频 / 11 GiB，同进程交替对照取中位）：重复添加已缓存曲库 274 ms → **99 ms**（2.78x）；目录遍历 222 ms → **30 ms**；首次添加稳态 6.2 s → **1.7 s**（3.75x）。分组结果（55 组 / 2379 首，组序与组内曲目顺序）与旧实现完全一致。
- **冷盘说明**：`D:` 为 5400rpm HDD，首次打开 2379 个文件需约 **44 s**（每文件一次随机寻道 ~10 ms，TinyTag 每文件多次 IO），上述 1.7 s 是文件已进入系统缓存后的稳态；两者相差 33x。机器可用内存仅 0.65 GB，缓存易被回收，冷态会反复出现。这是物理 IO 下限，不是解析开销；根本解法是渐进式加载（扫描完成即建组显示、metadata 后台补全），另可按需惰性读标签、将曲库迁至 SSD。另注：P2 的「4 线程饱和」结论来自热缓存 / CPU 密集场景，冷盘 IO 密集下 8 线程吞吐反而高约 1.7x。
- **测试**：`test_scan_worker.py` 更新 duration / mtime / 异常路径契约并新增 1 例，`test_player_groups.py` 新增 4 例覆盖新入口的等价性、幂等、去重与空输入，另补 1 例「目录被删除仍静默按空处理」防回退；P3 再新增 6 例覆盖进度上报、缓存计入进度、真实线程中断、进度条更新、取消触发中断与晚到结果忽略；发布前补 1 例锁定「文件对话框过滤器与 `SUPPORTED_EXTENSIONS` 一致」。总数 181 → 194。

---

## 七、性能数据

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 1000 首歌 filename 排序 | 3-8s（N 次 TinyTag） | **<10ms**（短路返回） |
| 1000 首歌 title 首次排序 | 3-8s（冷缓存） | 摊进 ScanWorker 等待时间 |
| 重启后排序 | 3-8s（空 cache） | **<10ms**（JSON 还原 cache） |
| ScanWorker 重启后加同文件夹 | 串行重读全部 | **0.3ms**（全跳过） |
| ScanWorker 首次（HDD） | 串行 TinyTag | **4 线程并行 + 跳过 duration** |
| 重复添加已缓存曲库（2379 首 / 11GiB） | 274ms（两次 walk + 逐条 stat） | **99ms**（一次 scandir + 复用结果建组） |
| 首次添加 2379 首（稳态） | 6.2s（含逐帧时长解析） | **1.7s**（duration=False） |
| QListWidget 1000 首刷新 | 500-1500ms | **<50ms**（setUpdatesEnabled） |
| 切到 208 首长文件名播放组 | 250ms（双重建 + 208 控件） | **5.5ms**（纯 item + delegate） |
| 208 行常驻定时器 | 207 个（40ms，事件循环 ~50% 饱和） | **0 个**（按需 1 个共享时钟） |
| 同组双击播放 | 整表重建，滚动位置归零 | **0 次重建**，滚动位置不变 |

---

## 八、已知局限 / 未来方向

| 方向 | 现状 | 可能 |
|------|------|------|
| 跨组拖拽曲目 | 不支持 | 模糊"文件夹=组"不变量，暂不做 |
| 均衡器 / 音效 | 依赖 just_playback（miniaudio 无 DSP API） | 需换 sounddevice 回调引擎，v2.0 级别 |
| LRC 歌词 | 未做 | 同目录同名 .lrc 文件自动匹配，实现不难 |
| Model/View 架构 | QListWidget + 自定义 delegate（已无 itemWidget） | 曲目超 5000 首时建议换成 QAbstractListModel |
| m4a / aac / wma 播放 | 后端（miniaudio）无对应解码器，扫描阶段已排除 | 需引入 ffmpeg 转码层或换 QtMultimedia / PyAV，会牺牲"轻量"定位 |
| 系统级全局热键 | 已有窗口内 `QShortcut` | 窗口失焦时仍需 QHotkey 或各平台 RegisterHotKey 封装 |
| `reset_group_order` 索引未重映射 | 重排后 `_current_index` 仍指向旧位置 | 照抄 `_swap_groups` 的 `playing_path → index()` 恢复逻辑 |

---

## 九、PyInstaller 打包

```bash
# 三端通用（在目标平台上执行，PyInstaller 不支持交叉编译）
pip install pyinstaller
pyinstaller Lyrica.spec --clean --noconfirm

# 产物
dist/Lyrica.exe            Windows 单文件可执行程序
dist/Lyrica.app            macOS 应用包（同时产出 dist/Lyrica 裸二进制）
dist/Lyrica                Linux 可执行文件
# lyrica_settings.json 自动保存在可执行文件同目录
# （main_window.settings_path 已处理 sys.frozen → sys.executable 所在目录）
```

自动化：推送 `v*.*.*` tag 触发 `.github/workflows/release.yml`，在
`windows-latest` / `macos-latest` / `ubuntu-22.04` 三端并行打包，
自动创建 GitHub Release 并上传 `Lyrica-Windows-x64.exe`、
`Lyrica-macOS-arm64.zip`、`Lyrica-Linux-x86_64.tar.gz`。

spec 关键配置：
- `strip`：Windows 关闭（PyInstaller 6.x 的 strip 会破坏打包内的 `python3xx.dll` / Qt 插件，CI 上的 MinGW strip 处理 PE 文件有已知问题），Linux/macOS 保留
- `upx`：三端一律关闭（被压缩的 Python DLL / Qt 插件易触发杀软误报和加载失败，且 PyInstaller 仅在检测到 UPX 时才压缩，结果不可预期）
- `excludes=[...]`：排除 QtWebEngine / matplotlib / numpy 等无关模块
- `hiddenimports=[..., "_cffi_backend"]`：just_playback 的 miniaudio 走 cffi，需显式声明
- `console=False`：无控制台黑框（调试时改 True 看堆栈）

---

## 十、测试

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest            # 194 个用例
QT_QPA_PLATFORM=offscreen python -m pytest --cov=src --cov=main   # 覆盖率 ~99%
```

设计要点：
- `tests/conftest.py` 在导入 `src.player` **之前**把假的 `just_playback` 模块注入
  `sys.modules`（`FakePlayback` 记录 load/play/pause/seek 调用序列），因此 CI 无声卡也能跑
- `QT_QPA_PLATFORM=offscreen` + `QSystemTrayIcon.isSystemTrayAvailable()` 固定为 False，
  保证无显示器环境下 UI 测试稳定
- 配置文件路径通过 monkeypatch `main_window.settings_path` 指向 `tmp_path`，不污染项目目录
- 右键菜单用 `MenuRecorder` 代填 `QMenu`，直接触发 QAction 而不真正弹窗阻塞

## 十一、设计决策记录

- **频谱图暂不做**：`just_playback` 不暴露 PCM；方案 A（状态驱动伪频谱）天花板是"看起来像"，
  方案 B 需 numpy + 文件 I/O + MP3 解码环境风险，方案 C 需侵入 miniaudio 回调、破坏
  `FakePlayback` 测试契约。收益撑不起代价。
- **暂不换播放引擎**：`QMediaPlayer` 依赖系统后端、延迟不可控、PCM 黑盒、打包需处理插件生态；
  `pyminiaudio` 可原生拦截 PCM 但需重写 `MusicPlayer` 内部状态机。若未来做 EQ/DSP/可视化，
  选 `pyminiaudio` 而非 fork `just_playback`。