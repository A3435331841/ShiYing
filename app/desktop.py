"""Open saved media and folders using the local desktop."""
import ctypes
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


OPENABLE_MEDIA_SUFFIXES = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav", ".opus",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif", ".bmp",
})


def can_open_saved_media(path: Path) -> bool:
    return (
        path.suffix.lower() in OPENABLE_MEDIA_SUFFIXES
        and path.resolve().suffix.lower() in OPENABLE_MEDIA_SUFFIXES
    )


def saved_file_facts(paths: list[str]) -> list[dict]:
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
    recorded = Path(path)
    if not recorded.is_absolute():
        raise ValueError("文件没有有效的绝对路径。")
    target = recorded.resolve(strict=True)
    if not target.is_file():
        raise ValueError("任务记录指向的不是文件。")
    if reveal:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
        return
    # Saved outputs are untrusted downloads, never launch scripts or executables.
    if not can_open_saved_media(recorded):
        raise ValueError("此文件类型不能直接打开，请在文件夹中查看。")
    if sys.platform == "win32":
        os.startfile(str(target))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])


def open_folder_foreground(path: str) -> bool:
    target = Path(path).resolve()
    if not Path(path).is_absolute() or not target.is_dir():
        raise ValueError("下载目录不存在或不是有效的绝对路径。")
    # 前台激活依赖 Explorer/pythonnet（Windows 专属）；macOS/Linux 用系统
    # 打开命令直接弹出新窗口，返回 False 表示无前台激活语义。
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
