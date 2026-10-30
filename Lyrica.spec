# -*- mode: python ; coding: utf-8 -*-
"""
Lyrica PyInstaller spec — 单文件、最小化体积、跨平台
用法：
    Windows:  pyinstaller Lyrica.spec --clean --noconfirm  → dist/Lyrica.exe
    macOS:    pyinstaller Lyrica.spec --clean --noconfirm  → dist/Lyrica + dist/Lyrica.app
    Linux:    pyinstaller Lyrica.spec --clean --noconfirm  → dist/Lyrica

Windows 原有逻辑 100% 不变，所有跨平台改动都在 sys.platform 条件分支里。
"""

import os
import sys

block_cipher = None

# ---------- 三端共用 hiddenimports ----------
hiddenimports = [
    # just_playback 运行时动态加载（基于 Rust miniaudio 的 cffi 绑定）
    "just_playback",
    # tinytag 纯 Python 但 PyInstaller 偶有漏
    "tinytag",
    # concurrent.futures — ScanWorker 并行用
    "concurrent.futures",
    # cffi 后端 — PyInstaller 静态扫描不到 cffi 动态加载
    "_cffi_backend",
]

# Windows 额外补几个 PySide6 插件（原有逻辑不变）
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

# ---------- 平台条件 ----------
_is_windows = sys.platform.startswith("win")
_is_macos = sys.platform == "darwin"
_is_linux = sys.platform.startswith("linux")

# 图标（只有 Windows 需要 .ico）
_icon = None
if _is_windows:
    _ico = os.path.join(os.path.dirname(SPEC), "lyrica.ico")
    if os.path.exists(_ico):
        _icon = _ico

# strip: Windows 必须关闭。
# PyInstaller 6.x 会把 strip 应用到打包内的 DLL（python312.dll、Qt 插件等），
# 而 CI runner 上的 strip 来自 MinGW/MSYS，处理 PE 文件会把它们破坏，
# 运行时表现就是 "Failed to load Python DLL ... 内存位置无效"。
_strip = not _is_windows

# UPX: 一律关闭。
# 被 UPX 压缩的 python DLL / Qt 插件极易触发杀软误报和加载失败，
# 且 PyInstaller 只在 UPX 存在时才真正压缩，结果不可预期，干脆关掉。
_use_upx = False

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
    strip=_strip,            # Windows=False（见上方说明），Linux/macOS 保留
    upx=_use_upx,            # 三端均为 False
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # 无控制台窗口；macOS/Linux 无害保留
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

# ---------- macOS: 额外生成 .app bundle ----------
# PyInstaller 会把单文件 EXE 包进 .app/Contents/MacOS/Lyrica
# 方便用户双击运行，绕过 Gatekeeper 裸二进制问题
if _is_macos:
    app = BUNDLE(
        exe,
        name="Lyrica.app",
        icon=None,
        bundle_identifier="com.lyrica.player",
        bundle_short_version="1.1.1",
        bundle_version="1.1.1",
        info_plist={
            "CFBundleName": "Lyrica",
            "CFBundleDisplayName": "Lyrica",
            "CFBundleIdentifier": "com.lyrica.player",
            "CFBundleShortVersionString": "1.1.1",
            "CFBundleVersion": "1.1.1",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSUIElement": False,
        },
    )
