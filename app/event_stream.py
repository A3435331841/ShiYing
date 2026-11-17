"""SSE 事件缓冲：把任务进度与状态变更安全推送到前端订阅者。

订阅者列表由锁保护，运行于 FastAPI 事件循环与后台工作线程交界处；只传递
已脱敏的状态摘要，不携带播放地址或凭据。主要调用方为 app/main.py 的 /api/events。
"""

from __future__ import annotations

import asyncio
import threading

from .models import DownloadJob


class JobEventBuffer:
    """Coalesce worker updates per job without blocking the HTTP thread pool."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ready = asyncio.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, DownloadJob] = {}
        self._revisions: dict[str, int] = {}
        self._closed = False

    def publish(self, _, job: DownloadJob) -> None:
        with self._lock:
            if self._closed:
                return
            previous = self._revisions.get(job.id)
            if previous is not None and previous >= job.revision:
                return
            wake = not self._pending
            self._pending[job.id] = job
            self._revisions[job.id] = job.revision
            if wake:
                self._loop.call_soon_threadsafe(self._ready.set)

    async def take(self, timeout: float = 15.0) -> list[DownloadJob]:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        with self._lock:
            jobs = list(self._pending.values())
            self._pending.clear()
            self._ready.clear()
            return jobs

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._revisions.clear()
