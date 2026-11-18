"""搜索后端的公共入口。

每个后端只负责“给定平台 + 关键词 → 候选 SearchHit 列表”，失败时抛出
项目统一的 DownloaderCoreError 子类；编排、去重与降级由 service 层负责。
"""

from __future__ import annotations

from .agent_reach import AgentReachBackend
from .bilibili import BilibiliSearchBackend
from .browser import BrowserSearchBackend
from .ytdlp import YtDlpSearchBackend

__all__ = [
    "AgentReachBackend",
    "BilibiliSearchBackend",
    "BrowserSearchBackend",
    "YtDlpSearchBackend",
]
