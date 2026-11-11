"""桌面系统集成与本地多媒体资产交互桥梁。

本模块负责处理与操作系统 Shell 的深度原生交互，包含以下核心职责与安全防线：
1. 媒体打开白名单校验：基于 OPENABLE_MEDIA_SUFFIXES 强校验，严禁执行可执行脚本与二进制；
2. 跨平台文件定位：支持在 Windows Explorer、macOS Finder、Linux 文件管理器中定位选中目标文件；
3. Windows 资源管理器智能置顶：基于 pythonnet (clr) STA 单线程单元反射调度 Shell.Application，
   精准遍历已打开的 Explorer 窗口，复用并将其置顶激活，避免重复弹窗堆叠。
"""
import ctypes
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


# 允许直接调起系统关联软件打开的媒体文件扩展名白名单（防投毒攻击）
OPENABLE_MEDIA_SUFFIXES = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav", ".opus",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif", ".bmp",
})


def can_open_saved_media(path: Path) -> bool:
    """检查文件是否符合可信媒体白名单后缀规范。

    执行双层扩展名校验（原始路径后缀与符号链接解析后的真实物理文件后缀），杜绝通过畸变
    扩展名或软链接指向可执行文件（如 .exe、.bat、.vbs）的投毒行为。

    Args:
        path: 待校验的目标文件路径。

    Returns:
        如果双层扩展名均在白名单内返回 True，否则返回 False。
    """
    return (
        path.suffix.lower() in OPENABLE_MEDIA_SUFFIXES
        and path.resolve().suffix.lower() in OPENABLE_MEDIA_SUFFIXES
    )


def saved_file_facts(paths: list[str]) -> list[dict]:
    """批量检索并统计已保存文件的物理状态（是否存在、文件字节大小、是否可打开）。

    Args:
        paths: 文件绝对路径列表。

    Returns:
        包含文件索引、文件名、绝对路径、物理存在状态、字节大小及可打开标记的字典列表。
    """
    files = []
    for index, raw in enumerate(paths):
        path = Path(raw)
        entry = {
            "index": index, "name": path.name, "path": raw,
            "exists": None, "size_bytes": None, "can_open": False,
        }
        try:
            info = path.stat() if path.is_absolute() else None
            entry["exists"] = bool(info and stat.S_ISREG(info.st_mode))
            if entry["exists"]:
                entry["size_bytes"] = info.st_size
                entry["can_open"] = can_open_saved_media(path)
        except (FileNotFoundError, NotADirectoryError):
            entry["exists"] = False
        except (OSError, ValueError):
            pass
        files.append(entry)
    return files


def open_saved_file(path: str, *, reveal: bool = False) -> None:
    """打开已保存的媒体文件，或在操作系统文件资源管理器中定位并选中该文件。

    Args:
        path: 文件的绝对路径字符串。
        reveal: 若为 True，则打开父目录并在 UI 中高亮选中该文件（不直接播放）；
                若为 False，则调用系统默认关联应用程序打开该媒体。

    Raises:
        ValueError: 路径非绝对路径、物理文件不存在、或文件类型不在安全白名单内时抛出。
    """
    recorded = Path(path)
    if not recorded.is_absolute():
        raise ValueError("文件没有有效的绝对路径。")
    target = recorded.resolve(strict=True)
    if not target.is_file():
        raise ValueError("任务记录指向的不是文件。")
    if reveal:
        # 跨平台高亮定位：Windows 使用 explorer /select, macOS 使用 open -R
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
        return
    # 安全红线：下载的文件属于不可信外部输入，严禁以任何方式执行脚本或可执行二进制
    if not can_open_saved_media(recorded):
        raise ValueError("此文件类型不能直接打开，请在文件夹中查看。")
    if sys.platform == "win32":
        os.startfile(str(target))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])


def open_folder_foreground(path: str) -> bool:
    """打开指定下载目录，并在 Windows 上智能将已打开的同一窗口置顶前台激活。

    在 Windows 桌面体验中，直接使用 explorer.exe 每次都会打开新的文件窗口，造成窗口混乱。
    本实现通过 Python.NET 运行时调度 COM STA 单线程单元，反射调用 Shell.Application：
    1. 遍历当前已有的 Explorer 窗口，通过 file:// URI 精准匹配目标文件夹物理路径；
    2. 若窗口已存在，调用 Win32 SetForegroundWindow / ShowWindow(SW_RESTORE) 直接置顶前台；
    3. 若窗口不存在，则唤起新 Explorer 并在数秒有界时间内轮询捕获并置顶。

    Args:
        path: 目标文件夹的绝对路径。

    Returns:
        Windows 平台下若成功完成前台激活置顶则返回 True；macOS/Linux 无此 API 语义返回 False。

    Raises:
        ValueError: 路径非绝对路径或目标物理目录不存在。
    """
    target = Path(path).resolve()
    if not Path(path).is_absolute() or not target.is_dir():
        raise ValueError("下载目录不存在或不是有效的绝对路径。")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
        return False
    if os.name != "nt":
        subprocess.Popen(["xdg-open", str(target)])
        return False
    activated = [False]
    opened = [False]
    found = [False]
    open_error = [None]

    # Explorer can reuse an existing window: match its full path, not its title.
    import clr  # noqa: F401 - initializes the already installed pythonnet runtime
    from System import Activator, Array, Int32, Object, Type
    from System.Reflection import BindingFlags
    from System.Threading import ApartmentState, Thread, ThreadStart

    def invoke(obj, name, flags, args=()):
        return obj.GetType().InvokeMember(name, flags, None, obj, Array[Object](args))

    def activate():
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        try:
            shell = Activator.CreateInstance(Type.GetTypeFromProgID("Shell.Application"))
            windows = invoke(shell, "Windows", BindingFlags.InvokeMethod)
            deadline = None
            while deadline is None or time.monotonic() < deadline:
                for index in range(int(invoke(windows, "Count", BindingFlags.GetProperty))):
                    try:
                        window = invoke(windows, "Item", BindingFlags.InvokeMethod, (Int32(index),))
                        location = urlsplit(str(invoke(window, "LocationURL", BindingFlags.GetProperty)))
                        if location.scheme != "file":
                            continue
                        candidate = url2pathname(location.path)
                        if location.netloc and location.netloc != "localhost":
                            candidate = "\\\\" + location.netloc + candidate
                        if Path(candidate).resolve() != target:
                            continue
                        found[0] = True
                        handle = int(invoke(window, "HWND", BindingFlags.GetProperty))
                        user32.ShowWindow(handle, 9 if user32.IsIconic(handle) else 5)
                        user32.SetForegroundWindow(handle)
                        if user32.GetForegroundWindow() == handle:
                            activated[0] = True
                            return
                        # A matching window already exists even if Windows refuses focus.
                        return
                    except Exception:
                        continue  # Explorer tabs/windows can close during enumeration.
                if not opened[0]:
                    try:
                        os.startfile(str(target), "explore", show_cmd=1)
                    except OSError as exc:
                        open_error[0] = exc
                        return
                    opened[0] = True
                    deadline = time.monotonic() + 4
                time.sleep(.1)
        except Exception:
            return  # Folder opened; the caller will show an explicit fallback hint.

    thread = Thread(ThreadStart(activate))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    thread.Join()
    if open_error[0] is not None:
        raise open_error[0]
    if not opened[0] and not found[0]:
        os.startfile(str(target), "explore", show_cmd=1)
    return activated[0]
