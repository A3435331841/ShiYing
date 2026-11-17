"""拾影应用包：保留历史导入路径的轻量级延迟解析门面。

模块级 __getattr__ 把旧版 app.* 符号透明转发到重构后的子模块，避免包导入
阶段拉起 FastAPI 与下载引擎。无可变全局状态，可被主进程与工作线程安全导入。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "DownloadItem": (".models", "DownloadItem"),
    "DownloadJob": (".models", "DownloadJob"),
    "DownloadManager": (".task_manager", "DownloadManager"),
    "DownloaderConfig": (".downloader", "DownloaderConfig"),
    "ItemStatus": (".models", "ItemStatus"),
    "JobStatus": (".models", "JobStatus"),
    "MediaDownloader": (".downloader", "MediaDownloader"),
    "MediaType": (".models", "MediaType"),
    "Platform": (".models", "Platform"),
    "SourceKind": (".models", "SourceKind"),
    "UnsupportedUrlError": (".platforms", "UnsupportedUrlError"),
    "identify_url": (".platforms", "identify_url"),
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
