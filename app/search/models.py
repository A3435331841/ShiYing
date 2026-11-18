"""AI 搜索子系统的数据模型。

本包刻意不依赖 app.main / app.manager / UI：只使用 pydantic 与平台枚举，
保证将来可以整体抽离为独立包或 CLI，而不需要改动调用方。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..models import Platform

# v1 可搜索平台。抖音精选没有独立搜索入口，视频号没有公开搜索能力，
# 两者在 UI 中置灰并给出说明，不进入请求模型。
SEARCHABLE_PLATFORMS: tuple[Platform, ...] = (
    Platform.BILIBILI,
    Platform.YOUTUBE,
    Platform.XIAOHONGSHU,
    Platform.DOUYIN,
    Platform.KUAISHOU,
)

# 无独立/公开搜索能力的平台及原因（供 UI 与 status 接口使用）。
UNSEARCHABLE_PLATFORMS: dict[Platform, str] = {
    Platform.SELECTED_DOUYIN: "抖音精选没有独立搜索入口，结果仅可能来自抖音主站搜索",
    Platform.WECHAT: "微信视频号没有公开搜索接口，请粘贴作品链接下载",
}


class SearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    # http(s) 图片 URL 或 data:image/...;base64,... 数据 URI
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    platforms: list[Platform] = Field(default_factory=lambda: list(SEARCHABLE_PLATFORMS))
    limit: int = Field(default=10, ge=1, le=30)
    # AI 关闭或未配置 key 时，query 原样作为各平台关键词。
    ai_mode: bool = True
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("query", "image")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("platforms")
    @classmethod
    def _only_searchable(cls, values: list[Platform]) -> list[Platform]:
        unsupported = [p for p in values if p not in SEARCHABLE_PLATFORMS]
        if unsupported:
            names = "、".join(p.value for p in unsupported)
            raise ValueError(f"该平台暂不支持搜索：{names}")
        # 去重并保序
        seen: set[Platform] = set()
        result: list[Platform] = []
        for platform in values:
            if platform not in seen:
                seen.add(platform)
                result.append(platform)
        return result


class SearchHit(BaseModel):
    platform: Platform
    source_url: str
    title: str = ""
    author: str | None = None
    media_id: str | None = None
    cover_url: str | None = None
    duration_seconds: int | None = None
    published_at: str | None = None
    keyword: str | None = None

    @property
    def key(self) -> str:
        return f"{self.platform.value}:{self.media_id or self.source_url}"


class PlatformIssue(BaseModel):
    platform: Platform
    issue_code: str
    message: str


class PlatformResult(BaseModel):
    platform: Platform
    hits: list[SearchHit] = Field(default_factory=list)
    issue: PlatformIssue | None = None


class SearchPlan(BaseModel):
    # platform.value -> 关键词列表
    keywords: dict[str, list[str]]
    description: str = ""
    source: str = "raw"  # raw | ai
    keywords_source: str = "query"
    fallback_reason: str | None = None


class SearchResponse(BaseModel):
    plan: SearchPlan
    results: list[PlatformResult]
    hits: list[SearchHit]
    request_id: str | None = None
