# Lyrica 架构文档

> 轻量级本地音乐播放器 · Python + PySide6 + just_playback
> 
> 版本历史：v0.1 → v0.2 → v0.3 → v1.0 → v1.0.1 → v1.0.2 → v1.0.3 → v1.0.4 → v1.0.5 → **v1.1.0**

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
| 后台扫描 | ScanWorker(QThread) — 递归 walk + ThreadPoolExecutor 8线程读 metadata |
| 持久化 | `lyrica_settings.json` — 窗口大小 / 播放状态 / 播放列表 / meta_cache |

### ScanWorker 算法（v1.0.3 优化）

```
Step 1: os.walk 快速收集所有音频文件路径
Step 2: 已有 _meta_cache 的文件 → 直接复用，跳过 TinyTag
Step 3: need_read 的 → ThreadPoolExecutor(max_workers=8) 并行读
```

---

## 五、持久化 JSON 结构

```json
{
  "version": "1.1.0",
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
- **ThreadPoolExecutor 并行**：need_read 的文件 8 线程 TinyTag.get()，IO 密集并行加速
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

### v1.1.0（当前）
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

---

## 七、性能数据

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 1000 首歌 filename 排序 | 3-8s（N 次 TinyTag） | **<10ms**（短路返回） |
| 1000 首歌 title 首次排序 | 3-8s（冷缓存） | 摊进 ScanWorker 等待时间 |
| 重启后排序 | 3-8s（空 cache） | **<10ms**（JSON 还原 cache） |
| ScanWorker 重启后加同文件夹 | 串行重读全部 | **0.3ms**（全跳过） |
| ScanWorker 首次（HDD） | 串行 TinyTag | **8 线程并行** |
| QListWidget 1000 首刷新 | 500-1500ms | **<50ms**（setUpdatesEnabled） |

---

## 八、已知局限 / 未来方向

| 方向 | 现状 | 可能 |
|------|------|------|
| 跨组拖拽曲目 | 不支持 | 模糊"文件夹=组"不变量，暂不做 |
| 均衡器 / 音效 | 依赖 just_playback（miniaudio 无 DSP API） | 需换 sounddevice 回调引擎，v2.0 级别 |
| LRC 歌词 | 未做 | 同目录同名 .lrc 文件自动匹配，实现不难 |
| Model/View 架构 | QListWidget（Widget 模式） | 曲目超 5000 首时建议换成 QAbstractListModel |
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
`windows-latest` / `macos-latest` / `ubuntu-20.04` 三端并行打包，
自动创建 GitHub Release 并上传 `Lyrica-Windows-x64.exe`、
`Lyrica-macOS-arm64.zip`、`Lyrica-Linux-x86_64.tar.gz`。

spec 关键配置：
- `strip=True` + Windows/Linux `upx=True`：体积最小化（-60% ~ -80%）；macOS 关闭 UPX，避免 Gatekeeper 拦截压缩后的二进制
- `excludes=[...]`：排除 QtWebEngine / matplotlib / numpy 等无关模块
- `hiddenimports=[..., "_cffi_backend"]`：just_playback 的 miniaudio 走 cffi，需显式声明
- `console=False`：无控制台黑框（调试时改 True 看堆栈）

---

## 十、测试

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest            # 176 个用例
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