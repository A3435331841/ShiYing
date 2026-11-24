#!/usr/bin/env python3
"""Exercise real HTTPS Range semantics, FFmpeg HLS timeout and Windows process lifetime."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.common import DownloaderConfig
from app.engine.core import MediaDownloader
from app.errors import MediaDownloadError
from app.models import MediaType, Platform
from app.xiaohongshu import RemoteAsset
from yt_dlp import YoutubeDL

RESULT = ROOT / "data" / "acceptance-artifacts" / "runtime-contract-smoke.json"
SAMPLE = "https://www.bilibili.com/video/BV1xx411c7mD?p=1"
TERMINAL_WAIT = 15.0


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            payload = json.load(response)
        return payload if isinstance(payload, dict) else None
    except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError):
        return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_health(port: int, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = health(port)
        if payload and payload.get("status") == "ok":
            return payload
        time.sleep(0.2)
    raise AssertionError(f"Service on port {port} did not become healthy")


def wait_stopped(port: int, pid: int, timeout: float = TERMINAL_WAIT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = health(port)
        if not payload or payload.get("server_pid") != pid:
            return True
        time.sleep(0.2)
    return False


def terminate_owned(port: int, pid: int) -> None:
    payload = health(port)
    if payload and payload.get("server_pid") == pid:
        os.kill(pid, signal.SIGTERM)
    if not wait_stopped(port, pid):
        os.kill(pid, signal.SIGTERM)


def resolve_cdn_url() -> tuple[str, str]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 25,
        "retries": 1,
        "format": "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/b[height<=720]",
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(SAMPLE, download=False)
    from app.bilibili_opus import is_trusted_bilibili_asset_url

    for entry in [*(info.get("requested_formats") or []), *(info.get("formats") or [])]:
        url = str(entry.get("url") or "")
        if (
            entry.get("vcodec") not in (None, "none")
            and str(entry.get("ext") or "").lower() == "mp4"
            and is_trusted_bilibili_asset_url(url)
        ):
            return url, str(entry.get("format_id") or "requested-video")
    raise AssertionError("Bilibili did not expose a trusted direct HTTPS video URL")


def read_range(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    import requests

    with requests.get(
        url,
        headers={**headers, "Accept-Encoding": "identity"},
        stream=True,
        timeout=(15, 30),
    ) as response:
        payload = response.raw.read(2048)
        return response.status_code, dict(response.headers), payload


def check_real_cdn_range() -> dict:
    url, format_id = resolve_cdn_url()
    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": "Mozilla/5.0",
    }
    status, response_headers, first = read_range(url, {**headers, "Range": "bytes=0-1023"})
    assert status == 206, f"Expected 206, got {status}"
    assert len(first) == 1024, f"Expected 1024 bytes, got {len(first)}"
    assert response_headers.get("Content-Range", "").startswith("bytes 0-1023/")
    validator = response_headers.get("ETag") or response_headers.get("Last-Modified")
    assert validator, "CDN response did not provide an If-Range validator"
    matching_status, _, matching = read_range(
        url, {**headers, "Range": "bytes=1024-2047", "If-Range": validator}
    )
    assert matching_status == 206, f"Matching If-Range returned {matching_status}"
    assert len(matching) == 1024
    stale_status, stale_headers, _ = read_range(
        url, {**headers, "Range": "bytes=1024-2047", "If-Range": '"stale-validator"'}
    )
    assert stale_status in {200, 206}, f"Stale If-Range returned {stale_status}"
    stale_validator = stale_headers.get("ETag") or stale_headers.get("Last-Modified")
    if stale_status == 206:
        assert stale_validator and stale_validator != '"stale-validator"', (
            "CDN returned 206 without a distinguishable validator"
        )
    return {
        "passed": True,
        "source": "Bilibili public sample via yt-dlp",
        "format_id": format_id,
        "host": url.split("/", 3)[2],
        "first_status": status,
        "first_bytes": len(first),
        "matching_if_range_status": matching_status,
        "stale_if_range_status": stale_status,
        "stale_validator_mismatch": stale_status == 206,
        "validator_type": "ETag" if response_headers.get("ETag") else "Last-Modified",
    }


def check_hls_timeout() -> dict:
    requested = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/stall.m3u8"):
                body = (
                    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:30\n"
                    "#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:30,\nsegment.ts\n#EXT-X-ENDLIST\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            requested.set()
            time.sleep(30)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.send_error(503)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output_dir = ROOT / "data" / "acceptance-artifacts" / "hls-timeout-smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{server.server_port}/stall.m3u8"
    config = DownloaderConfig(
        cookie_browser=None,
        hls_idle_timeout_seconds=1.0,
        hls_total_timeout_seconds=4.0,
    )
    engine = MediaDownloader(config)
    started = time.monotonic()
    error = ""
    try:
        engine._download_hls_asset(
            candidate_url=url,
            request_headers={},
            media_type=MediaType.VIDEO,
            asset=RemoteAsset(candidates=[url], index=0),
            output_dir=output_dir,
            upload_date="20261124",
            title="HLS timeout smoke",
            media_id="hls-timeout-smoke",
            platform=Platform.KUAISHOU,
            asset_index=None,
            compact_names=False,
            paired_stem=None,
            pair_image=False,
            is_kuaishou_source=True,
            verify_declared_dimensions=False,
            require_quality_fingerprint=False,
            callback=None,
            should_cancel=lambda: False,
            progress_index=None,
            progress_count=None,
        )
        raise AssertionError("Stalled HLS unexpectedly completed")
    except MediaDownloadError as exc:
        error = str(exc)
        assert "HLS 拉流" in error, error
    finally:
        elapsed = time.monotonic() - started
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert requested.wait(0), "FFmpeg never requested the stalled segment"
    assert elapsed < 8.0, f"HLS timeout took {elapsed:.2f}s"
    assert not list(output_dir.glob("**/*.part")), "Partial HLS file was not removed"
    return {
        "passed": True,
        "ffmpeg": engine._find_ffmpeg_executable(),
        "elapsed_seconds": round(elapsed, 3),
        "error": error,
        "partial_files_remaining": 0,
    }


def spawn_backend(state_path: Path, mode: str) -> None:
    port = free_port()
    isolated_root = ROOT / "data" / "acceptance-artifacts" / f"runtime-root-{mode}-{os.getpid()}"
    runtime_dir = ROOT / "data" / "acceptance-artifacts" / f"runtime-{mode}-{os.getpid()}"
    wrapper = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0, str(Path.cwd() / '.venv' / 'Lib' / 'site-packages')); "
        "import run; run.PROJECT_ROOT = Path(sys.argv[1]); "
        "raise SystemExit(run.main(sys.argv[2:]))"
    )
    command = [
        getattr(sys, "_base_executable", sys.executable),
        "-c",
        wrapper,
        str(isolated_root),
        "--port",
        str(port),
        "--no-browser",
        "--runtime-dir",
        str(runtime_dir),
    ]
    if mode == "monitor":
        command.extend(["--exit-with-parent", str(os.getpid())])
    log_path = state_path.with_suffix(".log")
    log_handle = log_path.open("wb")
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    finally:
        log_handle.close()
    if mode == "job":
        from launcher import _assign_windows_backend_job

        _assign_windows_backend_job(process)
    try:
        payload = wait_health(port)
    except BaseException:
        log_path.replace(state_path.with_suffix(".failed.log"))
        raise
    state_path.write_text(
        json.dumps(
            {"port": port, "pid": process.pid, "health": payload, "log": str(log_path),
             "isolated_root": str(isolated_root), "runtime_dir": str(runtime_dir)}
        ),
        encoding="utf-8",
    )
    if mode == "job":
        os._exit(0)


def run_helper(state_path: Path, mode: str) -> int:
    try:
        spawn_backend(state_path, mode)
    except Exception:
        state_path.write_text(
            json.dumps({"error": traceback.format_exc()}), encoding="utf-8"
        )
        raise
    return 0


def check_process_lifetime(mode: str, expected_alive: bool) -> dict:
    state_path = ROOT / "data" / "acceptance-artifacts" / f"lifetime-{mode}-{os.getpid()}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--helper",
        str(state_path),
        "--mode",
        mode,
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr[-1000:]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "error" not in state, state["error"]
    port = int(state["port"])
    pid = int(state["health"]["server_pid"])
    assert pid == int(state["pid"]), "Interpreter redirection changed the backend PID"
    try:
        if expected_alive:
            time.sleep(1.0)
            payload = health(port)
            assert payload and payload.get("server_pid") == pid, "Backend did not survive"
            result = {"passed": True, "survived_parent_exit": True, "pid": pid}
            terminate_owned(port, pid)
        else:
            stopped = wait_stopped(port, pid)
            if not stopped:
                terminate_owned(port, pid)
            assert stopped, f"Backend {pid} outlived its owner"
            result = {"passed": True, "survived_parent_exit": False, "pid": pid}
        return result
    finally:
        state_path.unlink(missing_ok=True)
        if "log" in state:
            with contextlib.suppress(OSError):
                Path(state["log"]).unlink(missing_ok=True)
        for key in ("isolated_root", "runtime_dir"):
            if key in state:
                shutil.rmtree(state[key], ignore_errors=True)


def find_window(title: str) -> int:
    if os.name != "nt":
        return 0
    user32 = ctypes.windll.user32
    found = 0

    def callback(hwnd, _param):
        nonlocal found
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found = int(hwnd)
                return False
        return True

    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(callback_type(callback), 0)
    return found


def check_window_close() -> dict:
    import webview

    before = health(8766)
    assert before and before.get("app_id") == "shiying-local-downloader", "No local service"
    title = f"拾影关窗验收-{os.getpid()}"
    outcome: dict = {"passed": False}

    def run_window() -> None:
        try:
            window.events.loaded.wait(20)
            deadline = time.monotonic() + 10
            hwnd = 0
            while time.monotonic() < deadline and not hwnd:
                hwnd = find_window(title)
                time.sleep(0.1)
            assert hwnd, "Test WebView2 window was not found"
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
            outcome["hwnd"] = hwnd
        except BaseException:
            outcome["error"] = traceback.format_exc()
            window.destroy()

    window = webview.create_window(title, "http://127.0.0.1:8766", width=900, height=650)
    webview.start(run_window, gui="edgechromium")
    assert "error" not in outcome, outcome["error"]
    after = health(8766)
    assert after and after.get("server_pid") == before.get("server_pid"), "Backend stopped with window"
    outcome.update(
        {
            "passed": True,
            "backend_pid_before": before.get("server_pid"),
            "backend_pid_after": after.get("server_pid"),
        }
    )
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--mode", choices=["monitor", "job", "orphan"])
    args = parser.parse_args()
    if args.helper:
        return run_helper(args.helper, args.mode or "orphan")
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    outcome: dict = {"passed": False, "checks": {}}
    try:
        outcome["checks"]["real_cdn_range"] = check_real_cdn_range()
        outcome["checks"]["hls_timeout"] = check_hls_timeout()
        if os.name == "nt":
            outcome["checks"]["window_close"] = check_window_close()
            outcome["checks"]["parent_monitor"] = check_process_lifetime("monitor", False)
            outcome["checks"]["windows_job"] = check_process_lifetime("job", False)
            outcome["checks"]["monitor_disabled_orphan"] = check_process_lifetime("orphan", True)
        else:
            outcome["checks"]["window_close"] = {"passed": None, "skipped": "Windows-only"}
            outcome["checks"]["parent_monitor"] = {"passed": None, "skipped": "Windows-only"}
            outcome["checks"]["windows_job"] = {"passed": None, "skipped": "Windows-only"}
            outcome["checks"]["monitor_disabled_orphan"] = check_process_lifetime("orphan", True)
        outcome["passed"] = all(
            check.get("passed") is not False for check in outcome["checks"].values()
        )
    except BaseException:
        outcome["error"] = traceback.format_exc()
    finally:
        RESULT.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print(RESULT.read_text(encoding="utf-8"))
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
