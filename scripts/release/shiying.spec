# -*- mode: python ; coding: utf-8 -*-
"""拾影 Windows 便携包 PyInstaller 规格（onedir）。

产出（scripts/release/build_exe.py 驱动，distpath=build/pyinstaller/dist）：
- 拾影/拾影.exe        双模式：默认桌面壳；`--serve` 作为后端进程（run.main）
- 拾影/停止拾影.exe    控制台工具：冻结 stop.py，按身份校验安全停机
- 拾影/_internal/      依赖、静态资源、包内 bin/（ffmpeg/ffprobe/deno）

注意：
- app/main.py 的 STATIC_DIR 基于 __file__（冻结时位于 _internal/app/static），
  因此静态资源必须按 'app/static' 原路径收集。
- environment.py 用 importlib.metadata 判断 yt-dlp/playwright/deno，
  必须显式 collect_metadata，否则自检三项会全红。
- playwright 使用 channel="chrome"（用户系统 Chrome），不需要捆绑浏览器。
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# SPECPATH = spec 文件所在目录（scripts/release）；仓库根是它的上一级。
REPO_ROOT = Path(SPECPATH).resolve().parents[1]

datas = [
    # 构建期源码指纹（build_exe.py 生成）：冻结模式下 /api/health 的 build_id。
    (str(REPO_ROOT / "build" / "pyinstaller" / "build_id.txt"), "."),
    # 前端静态资源：main.py 以 _internal/app/static 解析（见模块注释）。
    (str(REPO_ROOT / "app" / "static"), "app/static"),
    # 包内工具目录：运行时钩子会把 <bundle>/bin 前置到 PATH。
    (str(REPO_ROOT / "build" / "pyinstaller" / "bin" / "deno.exe"), "bin"),
    (str(REPO_ROOT / "build" / "pyinstaller" / "bin" / "ffmpeg.exe"), "bin"),
    (str(REPO_ROOT / "build" / "pyinstaller" / "bin" / "ffprobe.exe"), "bin"),
]

# playwright 的 node driver（node.exe + package/）必须原样随包分发。
datas += collect_data_files("playwright")
# yt-dlp-ejs 的 YouTube 签名挑战脚本属于包数据。
datas += collect_data_files("yt_dlp_ejs")
# pywebview 的 WebView2 运行库（WebView2Loader.dll、Microsoft.Web.WebView2.*.dll）
# 与 pythonnet/clr_loader 的运行时配置属于包数据，静态分析不收集。
datas += collect_data_files("webview")
datas += collect_data_files("pythonnet")
datas += collect_data_files("clr_loader")

# environment.py 自检依赖 importlib.metadata；PyInstaller 默认丢弃 dist-info。
for _dist in ("yt_dlp", "yt_dlp_ejs", "playwright", "pywebview", "deno", "pydantic", "fastapi"):
    datas += copy_metadata(_dist)

hiddenimports = [
    # uvicorn 以 import 字符串加载 ASGI 应用，静态分析看不到。
    "app.main",
    "app.wechat",
    "app.desktop",
    "app.downloader",
    "app.task_manager",
    # pywebview Windows 后端（edgechromium 依赖 pythonnet/clr）。
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.cef",
    "clr",
    "clr_loader",
    "pythonnet",
    # run.py / stop.py 仅在函数内被动态导入，显式声明防漏。
    "run",
    "stop",
]

a_shell = Analysis(
    [str(REPO_ROOT / "launch_shiying.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(REPO_ROOT / "scripts" / "release" / "frozen_runtime_hook.py")],
    excludes=["tkinter", "matplotlib", "numpy", "PyQt5", "pytest"],
    noarchive=False,
)

a_stop = Analysis(
    [str(REPO_ROOT / "stop.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=["app.build_info", "app.runtime", "app.launcher_control"],
    hookspath=[],
    runtime_hooks=[str(REPO_ROOT / "scripts" / "release" / "frozen_runtime_hook.py")],
    excludes=["tkinter", "matplotlib", "numpy", "PyQt5", "pytest"],
    noarchive=False,
)

pyz_shell = PYZ(a_shell.pure)
pyz_stop = PYZ(a_stop.pure)

exe_shell = EXE(
    pyz_shell,
    a_shell.scripts,
    [],
    exclude_binaries=True,
    name="拾影",
    debug=False,
    console=False,
    icon=str(REPO_ROOT / "app" / "static" / "brand.ico"),
)

exe_stop = EXE(
    pyz_stop,
    a_stop.scripts,
    [],
    exclude_binaries=True,
    name="停止拾影",
    debug=False,
    console=True,
    icon=str(REPO_ROOT / "app" / "static" / "brand.ico"),
)

coll = COLLECT(
    exe_shell,
    exe_stop,
    a_shell.binaries,
    a_shell.datas,
    a_stop.binaries,
    a_stop.datas,
    strip=False,
    upx=False,
    name="拾影",
)
