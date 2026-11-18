"""拾影 AI 搜索子系统（可抽离包）。

对外只暴露 SearchService 与其模型；不依赖 app.main / app.manager / UI，
配置与密钥均注入，便于独立测试、CLI 调用与将来抽离为独立包。
"""

from __future__ import annotations

from .key_store import SearchKeyStore
from .models import (
    SEARCHABLE_PLATFORMS,
    UNSEARCHABLE_PLATFORMS,
    PlatformIssue,
    PlatformResult,
    SearchHit,
    SearchPlan,
    SearchRequest,
    SearchResponse,
)
from .service import SearchConfig, SearchService

__all__ = [
    "SEARCHABLE_PLATFORMS",
    "UNSEARCHABLE_PLATFORMS",
    "PlatformIssue",
    "PlatformResult",
    "SearchConfig",
    "SearchHit",
    "SearchKeyStore",
    "SearchPlan",
    "SearchRequest",
    "SearchResponse",
    "SearchService",
]
