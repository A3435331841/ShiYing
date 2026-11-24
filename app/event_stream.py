"""SSE 事件缓冲：把任务进度与状态变更安全推送到前端订阅者。

订阅者列表由锁保护，运行于 FastAPI 事件循环与后台工作线程交界处；只传递
已脱敏的状态摘要，不携带播放地址或凭据。主要调用方为 app/main.py 的 /api/events。
"""

from __future__ import annotations

import asyncio
import threading
import weakref

from .models import DownloadJob


# 活跃订阅者注册表：停机线程无法直接 set 各缓冲区自己的 asyncio.Event，
# 由 wake_all() 通过各自的事件循环跨界唤醒，避免 SSE 在 take() 里等到 15s
# 超时，被 uvicorn 的 5s graceful shutdown 超时取消成 CancelledError。
_active_buffers: weakref.WeakSet[JobEventBuffer] = weakref.WeakSet()
_buffers_lock = threading.Lock()


class JobEventBuffer:
    """Coalesce worker updates per job without blocking the HTTP thread pool."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ready = asyncio.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, DownloadJob] = {}
        self._revisions: dict[str, int] = {}
        self._closed = False
        with _buffers_lock:
            _active_buffers.add(self)

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
        with _buffers_lock:
            _active_buffers.discard(self)


def wake_all() -> None:
    """从停机线程唤醒所有阻塞在 take() 的订阅者（含空缓冲区）。

    RUNTIME_STOP_EVENT 属于 app/runtime 的 threading.Event，与缓冲区自己的
    asyncio.Event 没有任何连接；只置位停机事件不会结束挂起的 SSE 生成器。
    """
    with _buffers_lock:
        buffers = tuple(_active_buffers)
    for buffer in buffers:
        try:
            buffer._loop.call_soon_threadsafe(buffer._ready.set)
        except RuntimeError:
            pass  # 事件循环已关闭（测试桩），无需再唤醒
