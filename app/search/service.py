"""AI 搜索编排：理解层 → 平台后端并行执行 → 去重/重排 → 统一响应。

依赖全部通过构造器注入（配置、密钥、引擎、浏览器后端、Agent-Reach），
因此本模块不 import app.main / app.manager，可独立测试与抽离。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from ..engine.common import safe_external_error_message
from ..errors import (
    DiscoveryError,
    DownloaderCoreError,
    SiteIssueCode,
    TemporaryAccessError,
)
from ..models import Platform
from .backends.agent_reach import AgentReachBackend, CHANNEL_BY_PLATFORM
from .backends.bilibili import BilibiliSearchBackend
from .backends.browser import BrowserSearchBackend
from .backends.ytdlp import YtDlpSearchBackend
from .key_store import SearchKeyStore
from .models import (
    PlatformIssue,
    PlatformResult,
    SearchHit,
    SearchPlan,
    SearchRequest,
    SearchResponse,
    SEARCHABLE_PLATFORMS,
)
from .understand import AgnesClient, KeywordPlan, plan_keywords, rerank_hits, validate_image_reference

# 单次搜索的总条数上限（多平台合并后）。
TOTAL_HITS_CAP = 30
# yt-dlp 平台最多尝试几个关键词变体（浏览器平台只用第一个，控制耗时）。
YTDLP_KEYWORD_VARIANTS = 3
BROWSER_KEYWORDS = 1


@dataclass(frozen=True)
class SearchConfig:
    enabled: bool = True
    base_url: str = "https://api.agnes-ai.cn/v1"
    model: str = "agnes-3.0-flash"
    use_chrome_cookies: bool = False
    chrome_profile: str | None = None


def _issue(platform: Platform, exc: BaseException) -> PlatformIssue:
    if isinstance(exc, DownloaderCoreError) and exc.issue_code is not None:
        code = exc.issue_code.value
    else:
        code = SiteIssueCode.UNKNOWN.value
    return PlatformIssue(
        platform=platform,
        issue_code=code,
        message=safe_external_error_message(str(exc))[:800],
    )


class SearchService:
    """搜索唯一入口。调用方只需提供 request，不接触任何后端细节。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], SearchConfig],
        key_store: SearchKeyStore,
        engine_factory: Callable[[], Any],
        browser_backend_factory: Callable[[SearchConfig], BrowserSearchBackend] | None = None,
        bilibili_backend_factory: Callable[[], BilibiliSearchBackend] | None = None,
        ytdlp_backend_factory: Callable[[], YtDlpSearchBackend] | None = None,
        agent_reach_backend: AgentReachBackend | None = None,
        llm_client_factory: Callable[[SearchConfig, str], AgnesClient] | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._key_store = key_store
        self._engine_factory = engine_factory
        self._browser_backend_factory = browser_backend_factory or (
            lambda config: BrowserSearchBackend(
                use_cookies=config.use_chrome_cookies,
                profile=config.chrome_profile,
            )
        )
        self._bilibili_backend_factory = bilibili_backend_factory or BilibiliSearchBackend
        self._ytdlp_backend_factory = ytdlp_backend_factory or (
            lambda: YtDlpSearchBackend(self._get_engine())
        )
        self._agent_reach = agent_reach_backend if agent_reach_backend is not None else AgentReachBackend()
        self._llm_client_factory = llm_client_factory or (
            lambda config, key: AgnesClient(
                base_url=config.base_url,
                api_key=key,
                model=config.model,
            )
        )
        self._engine: Any | None = None
        self._browser: BrowserSearchBackend | None = None

    # -- 惰性资源 -------------------------------------------------------
    def _get_engine(self) -> Any:
        if self._engine is None:
            self._engine = self._engine_factory()
        return self._engine

    def _get_browser(self, config: SearchConfig) -> BrowserSearchBackend:
        if self._browser is None:
            self._browser = self._browser_backend_factory(config)
        return self._browser

    # -- 入口 -----------------------------------------------------------
    def search(self, request: SearchRequest) -> SearchResponse:
        config = self._config_provider()
        if not config.enabled:
            raise DiscoveryError(
                "AI 搜索已在设置中关闭",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        if not request.query and not request.image:
            raise DiscoveryError(
                "请输入要搜索的内容描述或图片",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        platforms = list(request.platforms) or list(SEARCHABLE_PLATFORMS)

        plan, client = self._build_plan(request, config, platforms)
        platform_keywords: dict[Platform, list[str]] = {}
        for platform in platforms:
            values = [k for k in plan.keywords.get(platform.value, []) if k.strip()]
            if not values and request.query:
                values = [request.query]
            platform_keywords[platform] = values

        results = self._execute(
            platforms=platforms,
            platform_keywords=platform_keywords,
            config=config,
            limit=request.limit,
        )

        merged = self._merge(results, request.limit)
        if client is not None and request.ai_mode and request.query and len(merged) >= 6:
            order = rerank_hits(
                client,
                query=request.query,
                hits=[self._hit_payload(hit) for hit in merged],
            )
            if order:
                merged = [merged[index] for index in order if index < len(merged)]
        merged = merged[:TOTAL_HITS_CAP]

        ordered_results: list[PlatformResult] = []
        for platform in platforms:
            platform_hits = [hit for hit in merged if hit.platform is platform]
            issue = results.get(platform, PlatformResult(platform=platform)).issue
            ordered_results.append(
                PlatformResult(platform=platform, hits=platform_hits, issue=issue)
            )
        return SearchResponse(
            plan=plan,
            results=ordered_results,
            hits=merged,
            request_id=request.request_id,
        )

    # -- 理解层 ---------------------------------------------------------
    def _build_plan(
        self, request: SearchRequest, config: SearchConfig, platforms: list[Platform]
    ) -> tuple[SearchPlan, AgnesClient | None]:
        platform_values = [platform.value for platform in platforms]
        raw = KeywordPlan(
            keywords={value: ([request.query] if request.query else []) for value in platform_values},
            source="raw",
        )
        if not request.ai_mode:
            return self._to_plan(raw, fallback_reason="已关闭 AI 模式，使用原始关键词"), None
        try:
            key = self._key_store.load()
        except OSError:
            key = ""
        if not key:
            if not request.query:
                raise DiscoveryError(
                    "图片搜索需要先在设置中配置 AI API Key",
                    issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
                )
            return self._to_plan(raw, fallback_reason="未配置 AI API Key，已使用原始关键词"), None
        image = validate_image_reference(request.image) if request.image else None
        client = self._llm_client_factory(config, key)
        try:
            plan = plan_keywords(
                client,
                query=request.query or "",
                image=image,
                platform_values=platform_values,
            )
        except (DiscoveryError, TemporaryAccessError) as exc:
            # AI 失败不阻塞搜索：退回原始关键词，并把原因告诉用户。
            return self._to_plan(raw, fallback_reason=f"AI 理解失败：{safe_external_error_message(str(exc))[:200]}"), None
        if plan.source == "raw" and plan.keywords == raw.keywords:
            return self._to_plan(plan, fallback_reason="AI 输出无法解析，已使用原始关键词"), client
        return self._to_plan(plan, fallback_reason=None), client

    @staticmethod
    def _to_plan(plan: KeywordPlan, *, fallback_reason: str | None) -> SearchPlan:
        return SearchPlan(
            keywords=plan.keywords,
            description=plan.description,
            source=plan.source,
            keywords_source="ai" if plan.source == "ai" else "query",
            fallback_reason=fallback_reason,
        )

    # -- 执行 -----------------------------------------------------------
    def _execute(
        self,
        *,
        platforms: list[Platform],
        platform_keywords: dict[Platform, list[str]],
        config: SearchConfig,
        limit: int,
    ) -> dict[Platform, PlatformResult]:
        ytdlp_platforms = [
            platform for platform in platforms if platform is Platform.YOUTUBE
        ]
        bilibili_platforms = [
            platform for platform in platforms if platform is Platform.BILIBILI
        ]
        browser_platforms = [
            platform
            for platform in platforms
            if platform in (Platform.DOUYIN, Platform.KUAISHOU, Platform.XIAOHONGSHU)
        ]
        results: dict[Platform, PlatformResult] = {}

        def run_bilibili(platform: Platform) -> None:
            keywords = platform_keywords.get(platform) or []
            hits: list[SearchHit] = []
            try:
                backend = self._bilibili_backend_factory()
                seen: set[str] = set()
                for keyword in keywords[:YTDLP_KEYWORD_VARIANTS]:
                    for hit in backend.search(keyword, limit):
                        key = hit.media_id or hit.source_url
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(hit)
                        if len(hits) >= limit:
                            break
                    if len(hits) >= limit:
                        break
                results[platform] = PlatformResult(platform=platform, hits=hits[:limit])
            except DownloaderCoreError as exc:
                results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))
            except Exception as exc:  # noqa: BLE001 - 单平台失败不影响其他平台
                results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))

        def run_ytdlp(platform: Platform) -> None:
            keywords = platform_keywords.get(platform) or []
            hits: list[SearchHit] = []
            try:
                backend = self._ytdlp_backend_factory()
                seen: set[str] = set()
                for keyword in keywords[:YTDLP_KEYWORD_VARIANTS]:
                    for hit in backend.search(platform, keyword, limit):
                        key = hit.media_id or hit.source_url
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(hit)
                        if len(hits) >= limit:
                            break
                    if len(hits) >= limit:
                        break
                results[platform] = PlatformResult(platform=platform, hits=hits[:limit])
            except DownloaderCoreError as exc:
                results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))
            except Exception as exc:  # noqa: BLE001 - 单平台失败不影响其他平台
                results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))

        def run_browser_batch(batch_platforms: list[Platform]) -> None:
            tasks: list[tuple[Platform, str]] = []
            for platform in batch_platforms:
                keywords = platform_keywords.get(platform) or []
                if keywords:
                    tasks.append((platform, keywords[0]))
            if not tasks:
                return
            backend = self._get_browser(config)
            try:
                batch = backend.search_many(tasks, limit)
            except DownloaderCoreError as exc:
                for platform in batch_platforms:
                    results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))
                return
            except Exception as exc:  # noqa: BLE001
                for platform in batch_platforms:
                    results[platform] = PlatformResult(platform=platform, issue=_issue(platform, exc))
                return
            issues = backend.last_issues
            for platform, _keyword in tasks:
                hits = batch.get(platform, [])
                if hits:
                    results[platform] = PlatformResult(platform=platform, hits=hits[:limit])
                elif platform in issues:
                    results[platform] = PlatformResult(
                        platform=platform, issue=_issue(platform, issues[platform])
                    )
                else:
                    results[platform] = PlatformResult(
                        platform=platform,
                        issue=PlatformIssue(
                            platform=platform,
                            issue_code=SiteIssueCode.CONTENT_UNAVAILABLE.value,
                            message="该平台没有返回搜索结果",
                        ),
                    )

        def run_agent_reach(platform: Platform) -> bool:
            if not self._agent_reach.available() or platform not in CHANNEL_BY_PLATFORM:
                return False
            keywords = platform_keywords.get(platform) or []
            for keyword in keywords[:1]:
                try:
                    hits = self._agent_reach.search(platform, keyword, limit)
                except DownloaderCoreError:
                    return False  # 交给内置后端兜底
                if hits:
                    results[platform] = PlatformResult(platform=platform, hits=hits[:limit])
                    return True
            return False

        # Agent-Reach 优先（若安装），失败自动回退内置后端。
        agent_handled: set[Platform] = set()
        for platform in ytdlp_platforms + bilibili_platforms + [
            p for p in browser_platforms if p is Platform.XIAOHONGSHU
        ]:
            if run_agent_reach(platform):
                agent_handled.add(platform)

        remaining_ytdlp = [p for p in ytdlp_platforms if p not in agent_handled]
        remaining_bilibili = [p for p in bilibili_platforms if p not in agent_handled]
        remaining_browser = [p for p in browser_platforms if p not in agent_handled]

        workers = 1 + len(remaining_ytdlp) + len(remaining_bilibili)
        with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as pool:
            futures = [pool.submit(run_ytdlp, platform) for platform in remaining_ytdlp]
            futures += [pool.submit(run_bilibili, platform) for platform in remaining_bilibili]
            if remaining_browser:
                futures.append(pool.submit(run_browser_batch, remaining_browser))
            for future in futures:
                future.result()

        for platform in platforms:
            results.setdefault(platform, PlatformResult(platform=platform))
        return results

    # -- 合并与输出 -----------------------------------------------------
    @staticmethod
    def _merge(results: dict[Platform, PlatformResult], per_platform: int) -> list[SearchHit]:
        """按平台轮转合并，保证每个平台都有曝光机会。"""

        buckets = {
            platform: list(result.hits[:per_platform])
            for platform, result in results.items()
        }
        merged: list[SearchHit] = []
        seen: set[str] = set()
        index = 0
        while len(merged) < TOTAL_HITS_CAP:
            added = False
            for platform in results:
                bucket = buckets.get(platform) or []
                if index < len(bucket):
                    hit = bucket[index]
                    key = f"{hit.platform.value}:{hit.media_id or hit.source_url}"
                    if key not in seen:
                        seen.add(key)
                        merged.append(hit)
                        added = True
                    if len(merged) >= TOTAL_HITS_CAP:
                        break
            if not added:
                break
            index += 1
        return merged

    @staticmethod
    def _hit_payload(hit: SearchHit) -> dict[str, Any]:
        return {
            "platform": hit.platform.value,
            "title": hit.title,
            "author": hit.author or "",
        }
