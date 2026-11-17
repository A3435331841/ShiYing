"""拾影核心领域模型：平台枚举、任务状态、下载条目与结果值对象。

本模块被 API 层、任务调度层、下载引擎与持久化层共同依赖，只定义数据结构
与不依赖外部状态的纯函数，不执行 I/O、不持有锁，因此可被多线程工作池安全
共享。所有实体基于 pydantic BaseModel，序列化为 JSON 后落盘到 data/state/。

主要调用方：app/main.py（请求/响应模型）、app/manager/（任务状态机）、
app/engine/（下载条目与结果）、app/storage.py（持久化读写）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import SiteIssueCode


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    # 抖音精选与主站是物理分治的两个平台：枚举、URL 识别与落盘目录均独立。
    # 值采用 kebab-case，与前端 home.js / task-views.js 的平台字典键一致。
    SELECTED_DOUYIN = "selected-douyin"
    KUAISHOU = "kuaishou"
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    WECHAT = "wechat"


# 抖音系共用同一套作品身份绑定、签名与画质探测实现。分治后精选不能再被写成
# DOUYIN，但引擎与调度层原有的「是否抖音系」判断必须同时覆盖两者，否则精选
# 任务会静默失去 anti-串号、签名失败诊断与最高画质下限等关键保护。
DOUYIN_PLATFORMS: frozenset[Platform] = frozenset(
    {Platform.DOUYIN, Platform.SELECTED_DOUYIN}
)


class SourceKind(str, Enum):
    PROFILE = "profile"
    PLAYLIST = "playlist"
    ITEM = "item"
    SHORT_LINK = "short_link"


class JobStatus(str, Enum):
    QUEUED = "queued"
    DISCOVERING = "discovering"
    DOWNLOADING = "downloading"
    NEEDS_AUTH = "needs_auth"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"


class ItemStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"
    NEEDS_AUTH = "needs_auth"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    PAUSED = "paused"


class MediaType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    UNKNOWN = "unknown"


ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DISCOVERING,
    JobStatus.DOWNLOADING,
}

RETRYABLE_ITEM_STATUSES = {
    ItemStatus.FAILED,
    ItemStatus.NEEDS_AUTH,
    ItemStatus.CANCELLED,
    ItemStatus.PAUSED,
    ItemStatus.SKIPPED,
}


class TransferProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    downloaded_bytes: int = 0
    total_bytes: int | None = None
    percent: float | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: float | None = None
    fragment_index: int | None = None
    fragment_count: int | None = None
    filename: str | None = None


class DownloadItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    media_id: str | None = None
    source_url: str
    title: str = "Untitled"
    upload_date: str | None = None
    author: str | None = None
    extractor_key: str | None = None
    playlist_index: int | None = None
    media_type: MediaType = MediaType.UNKNOWN
    status: ItemStatus = ItemStatus.QUEUED
    progress: TransferProgress = Field(default_factory=TransferProgress)
    attempts: int = 0
    output_paths: list[str] = Field(default_factory=list)
    selected_format: str | None = None
    resolution: str | None = None
    error: str | None = None
    auth_message: str | None = None
    issue_code: SiteIssueCode | None = None
    # Store only fixed diagnostic categories, never raw exception details.
    diagnostic_code: str | None = None
    retryable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DownloadJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source_url: str
    platform: Platform
    source_kind: SourceKind
    output_root: str
    resolved_source_kind: SourceKind | None = None
    resolved_source_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    author: str | None = None
    output_dir: str | None = None
    items: list[DownloadItem] = Field(default_factory=list)
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    active_item_id: str | None = None
    activity_message: str | None = None
    activity_started_at: datetime | None = None
    error: str | None = None
    warning: str | None = None
    auth_message: str | None = None
    issue_code: SiteIssueCode | None = None
    issue_message: str | None = None
    diagnostic_code: str | None = None
    verification_url: str | None = None
    cookie_browser: str | None = "chrome"
    cookie_profile: str | None = None
    cookie_profile_auto_selected: bool = False
    cookie_fallback_used: bool = False
    discovery_complete: bool = True
    selected_item_ids: list[str] | None = None
    cancel_requested: bool = False
    pause_requested: bool = False
    retryable: bool = True
    revision: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    def refresh_counts(self) -> None:
        # With an explicit user selection, only the selected items count toward
        # totals; unselected (skipped) items are neither failures nor missing work.
        items = self.items
        if self.selected_item_ids is not None:
            scope = set(self.selected_item_ids)
            items = [item for item in self.items if item.id in scope]
        self.total_items = len(items)
        self.completed_items = sum(
            item.status == ItemStatus.COMPLETED for item in items
        )
        self.failed_items = sum(
            item.status in {ItemStatus.FAILED, ItemStatus.NEEDS_AUTH}
            for item in items
        )

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir or self.output_root)


class UrlInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    platform: Platform
    kind: SourceKind


class ManagerEvent(BaseModel):
    job_id: str
    event: str
    item_id: str | None = None
    revision: int
    timestamp: datetime = Field(default_factory=utc_now)
