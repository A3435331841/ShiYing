"""Shiying desktop shell. Reuses the one local service started by any entry point."""
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

from app.build_info import project_root_for

# 数据根（data/downloads/cache）：源码模式=仓库根；EXE 包=exe 所在目录（便携语义）。
ROOT = project_root_for(Path(__file__).resolve().parent)
# 静态资源根（app/static）：源码模式=仓库根；EXE 包=解包目录 _internal。
SRC_ROOT = Path(__file__).resolve().parent
# One port for every entry point (start.bat, start.command, start.sh, run.py and
# this desktop shell). A second port made the desktop shell unable to see, or
# reuse, a service that was already running.
PORT = 8766
URL = f'http://127.0.0.1:{PORT}'
TITLE = '拾影 · 视频下载工具'


def _find_shiying_window():
    if sys.platform != 'win32':
        return None
    user32 = ctypes.windll.user32
    target = None

    def enum_proc(hwnd, lParam):
        nonlocal target
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == TITLE:
                    class_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buf, 256)
                    # Exclude Windows 11 Explorer dummy TabProxyWindow
                    if 'TabProxyWindow' not in class_buf.value:
                        target = hwnd
                        return False
        return True

    from ctypes import wintypes
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    return target


def healthy():
    try:
        with urlopen(URL + '/api/health', timeout=1) as response:
            health = json.load(response)
        return (health.get('app_id') == 'shiying-local-downloader'
                and Path(health.get('project_root', '')).resolve() == ROOT
                and health.get('build_id') == health.get('source_build_id')
                and not health.get('restart_required'))
    except Exception:
        return False


class DesktopAPI:
    _window = None

    def open_folder(self, path):
        from app.desktop import open_folder_foreground
        return {"foreground": open_folder_foreground(path)}

    def choose_directory(self):
        import webview
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(ROOT / 'downloads'))
        return result[0] if result else None

    def quit_app(self):
        """完全退出：经 stop.py 的身份校验停掉本机服务，再关闭桌面窗口。

        关窗不停服务是有意设计；这里给用户一个显式的图形化出口。
        stop_backend 无法证明目标是本项目的实例时会抛 StopRefusedError 拒绝动手。
        """
        from stop import StopRefusedError, stop_backend
        try:
            stop_backend(port=PORT, timeout=8.0)
        except StopRefusedError as exc:
            return {"ok": False, "message": f"无法确认本机服务身份，已取消退出：{exc}"}
        except Exception as exc:
            return {"ok": False, "message": f"停止服务失败：{type(exc).__name__}: {exc}"}
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        return {"ok": True}


_HELD_MUTEX = None


def launch():
    global _HELD_MUTEX
    if sys.platform == 'win32':
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        _HELD_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, 'Local\\ShiyingDesktop-D')
        if ctypes.windll.kernel32.GetLastError() == 183:
            handle = _find_shiying_window()
            if handle:
                ctypes.windll.user32.ShowWindow(handle, 9)
                ctypes.windll.user32.SetForegroundWindow(handle)
                return
    if not healthy():
        # Check if an existing Shiying service is running but needs code reload
        try:
            with urlopen(URL + '/api/health', timeout=1) as response:
                health = json.load(response)
            if (health.get('app_id') == 'shiying-local-downloader'
                    and Path(health.get('project_root', '')).resolve() == ROOT
                    and health.get('restart_required')):
                from stop import stop_backend
                try:
                    stop_backend(port=PORT, timeout=5.0)
                    time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass

        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', PORT)) == 0:
                raise RuntimeError(
                    f'{PORT} 端口已有其他程序或另一份拾影实例在运行。'
                    '请先运行“停止拾影.bat”，再重新启动。'
                )
        logdir = ROOT / 'data'
        logdir.mkdir(exist_ok=True)
        if getattr(sys, 'frozen', False):
            # EXE 便携包：包内没有 Python 解释器，后端由同一个 exe 的 --serve 分支承载。
            cmd = [sys.executable, '--serve', '--no-browser', '--port', str(PORT)]
        else:
            cmd = [str(ROOT / '.venv/Scripts/python.exe'), str(ROOT / 'run.py'), '--no-browser', '--port', str(PORT)]
        with (logdir / 'server.log').open('ab') as log:
            subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(100):
            if healthy():
                break
            time.sleep(.25)
        else:
            raise RuntimeError(f'启动失败，请查看 {ROOT}\\data\\server.log。首次使用请运行 安装依赖.bat。')
    try:
        import webview
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        api = DesktopAPI()
        window = webview.create_window(TITLE, URL, js_api=api, width=1380, height=900, min_size=(740, 620), background_color='#F5F7FA', text_select=True)
        api._window = window
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(ROOT / 'data/webview'), icon=str(SRC_ROOT / 'app/static/brand.ico'))
    except Exception as exc:
        with (ROOT / 'data/desktop.log').open('a', encoding='utf-8') as log:
            log.write(f'{type(exc).__name__}: {exc}\n')
        webbrowser.open(URL)


if __name__ == '__main__':
    # 冻结运行（EXE 便携包）：`拾影.exe --serve ...` 作为后端进程入口，
    # 由桌面壳以无窗口方式派生；源码模式仍直接进入桌面壳。
    if '--serve' in sys.argv[1:]:
        from run import main as run_main
        raise SystemExit(run_main([arg for arg in sys.argv[1:] if arg != '--serve']))
    try:
        os.chdir(ROOT)
        launch()
    except Exception as exc:
        if sys.platform == 'win32':
            # MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(None, str(exc), '拾影启动失败', 0x10 | 0x40000 | 0x10000)
        else:
            raise
