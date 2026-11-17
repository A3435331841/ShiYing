"""PyInstaller 运行时钩子：让冻结后的进程找到包内捆绑的命令行工具。

包内 bin/ 目录包含 ffmpeg.exe / ffprobe.exe / deno.exe（由 build_exe.py 放入）。
把它们前置到 PATH 后，engine 的 shutil.which（_find_ffmpeg_executable）与
yt-dlp 的 deno 调用都无需任何代码改动即可命中捆绑版本。
"""
import os
import sys
from pathlib import Path

_BIN_DIR = Path(getattr(sys, "_MEIPASS", "")) / "bin"
if _BIN_DIR.is_dir():
    os.environ["PATH"] = str(_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
