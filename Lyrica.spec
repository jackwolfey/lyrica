# -*- mode: python ; coding: utf-8 -*-
"""
Lyrica PyInstaller spec — 单文件、最小化体积
用法（Windows）：
    pyinstaller Lyrica.spec --clean --noconfirm

产物：
    dist/Lyrica.exe   单文件可执行程序
    lyrica_settings.json 自动保存在 exe 同目录（main_window.settings_path 已处理 sys.frozen）
"""

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ---------- 需要显式 collect 的隐式依赖 ----------
hiddenimports = [
    # just_playback 运行时动态加载
    "just_playback",
    # tinytag 纯 Python 但 PyInstaller 偶有漏
    "tinytag",
    # concurrent.futures — ScanWorker 并行用
    "concurrent.futures",
    # cffi 后端 — just_playback 依赖的 miniaudio 绑定走 cffi，PyInstaller 静态扫描不到
    "_cffi_backend",
]

# PySide6 可能漏的平台插件（Windows 下）
if sys.platform.startswith("win"):
    hiddenimports += [
        "PySide6.QtSvg",          # 图标渲染
        "PySide6.QtNetwork",      # 某些音频后端可能触发
        "PySide6.QtMultimedia",   # just_playback 不依赖 QtMultimedia，但防缺
    ]

# ---------- 要排除的模块（大幅减小体积） ----------
excludes = [
    # Qt 没用到的模块
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
    "PySide6.QtWebChannel", "PySide6.QtPositioning",
    "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtTest",
    # Python 标准库没用到
    "tkinter", "unittest", "pydoc", "doctest", "difflib",
    "pilib", "IPython", "jupyter", "notebook",
    "matplotlib", "numpy", "pandas", "scipy",
    "PIL", "Pillow", "imageio",
    "pytz", "zoneinfo",
    "xmlrpc", "ctypes.test", "distutils", "lib2to3",
    "ensurepip", "venv", "py_compile",
    # PyInstaller 自带的分析工具
    "PyInstaller.utils.cliutils",
]

# ---------- 图标（Windows 用 .ico，其余平台由系统/desktop 文件处理） ----------
_icon = None
if sys.platform.startswith("win"):
    _ico = os.path.join(os.path.dirname(SPEC), "lyrica.ico")
    if os.path.exists(_ico):
        _icon = _ico

# ---------- 构建 ----------
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Lyrica",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,              # ← strip 掉符号表，体积 -30%~50%（Windows 下 PyInstaller 会走 UPX 或自带 strip）
    upx=True,                # ← 启用 UPX 压缩（系统需有 upx 命令；Windows 下会自动下载）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # ← 无控制台窗口（正式版）；调试时改 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # ← 有 .ico 的话填 "resources/lyrica.ico"
)
