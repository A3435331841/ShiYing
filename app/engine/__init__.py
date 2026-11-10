"""Download engine package."""

from __future__ import annotations

from . import common, core, discovery, douyin_media, douyin_probe, naming, platform_items, transfer
from .common import *  # noqa: F401,F403
from .common import __all__ as _common_all
from .core import MediaDownloader

#: Modules whose module-level globals are mirrored by the ``app.downloader`` facade.
MODULES = (
    common,
    core,
    discovery,
    douyin_media,
    douyin_probe,
    naming,
    platform_items,
    transfer,
)

__all__ = [*_common_all, "MediaDownloader"]
