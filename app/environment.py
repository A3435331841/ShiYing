"""Small, local-only checks for dependencies used by download tasks."""
from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .downloader import MediaDownloader


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _starts(executable: str | None) -> bool:
    if not executable:
        return False
    try:
        return subprocess.run(
            [executable, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _chrome_path() -> str | None:
    for name in ("chrome", "google-chrome", "google-chrome-stable"):
        if path := shutil.which(name):
            return path
    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                    candidate, _ = winreg.QueryValueEx(key, None)
                    if Path(candidate).is_file():
                        return candidate
            except OSError:
                pass
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(name):
                candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if candidate.is_file():
                    return str(candidate)
    elif sys.platform == "darwin":
        candidate = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if candidate.is_file():
            return str(candidate)
    return None


def _writable(directory: str) -> bool:
    try:
        with tempfile.TemporaryFile(dir=directory) as handle:
            handle.write(b"check")
            handle.flush()
        return True
    except OSError:
        return False


def check_environment(download_dir: str) -> dict:
    ffmpeg = _starts(MediaDownloader._find_ffmpeg_executable())
    ffprobe = _starts(MediaDownloader._find_ffprobe_executable())
    chrome = bool(_chrome_path())
    writable = _writable(download_dir)
    yt_dlp = _package_version("yt-dlp")
    playwright = _package_version("playwright")
    return {"checks": [
        {"id": "ffmpeg", "ok": ffmpeg, "message": "FFmpeg 可用" if ffmpeg else "FFmpeg 不可用：请安装 FFmpeg 并重启拾影，音视频合并和图片校验需要它。"},
        {"id": "ffprobe", "ok": ffprobe, "message": "FFprobe 可用" if ffprobe else "FFprobe 不可用：请安装包含 FFprobe 的 FFmpeg 并重启拾影，画质和视频校验需要它。"},
        {"id": "chrome", "ok": chrome, "message": "已找到 Chrome" if chrome else "未找到 Chrome：快手和部分主页解析可能无法运行，请安装 Chrome 后重启。"},
        {"id": "download_dir", "ok": writable, "message": "保存目录可写" if writable else "保存目录不可写：请检查磁盘空间和权限，或在设置中选择其他目录。"},
        {"id": "yt_dlp", "ok": bool(yt_dlp), "message": f"yt-dlp {yt_dlp}" if yt_dlp else "缺少 yt-dlp：请重新运行依赖安装程序。"},
        {"id": "playwright", "ok": bool(playwright), "message": f"Playwright {playwright}" if playwright else "缺少 Playwright：请重新运行依赖安装程序。"},
    ]}
