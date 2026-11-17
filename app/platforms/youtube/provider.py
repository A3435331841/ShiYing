"""YouTube 平台提供者：SPI 六大生命周期接口的适配器实现。

本包是四步走演进路线「第三步：平台 SPI 分包」的首个落地平台。URL 识别与
会话策略由本模块真实实现；作品发现、格式探测与字节传输在迁移期委托给既有
``MediaDownloader``（通过 EnginePort 注入），核心引擎因此无需感知 YouTube
的 n-sig 挑战、播放列表与 Shorts 细节。Deno JS 沙箱由 yt-dlp 的
``js_runtimes={"deno": {}}`` 在引擎侧执行，本模块只声明依赖与降级语义，
不直接启动外部进程，避免在 SPI 层引入不可控的沙箱逃逸面。

不持有可变全局状态；注入的引擎实例由调用方负责生命周期，故本模块可被
多个工作线程共享。主要调用方：ProviderRegistry 与未来的平台路由层。
"""

from __future__ import annotations

import re
from pathlib import Path

from ...models import DownloadItem, Platform, UrlInfo
from .._identify import UnsupportedUrlError, identify_url
from ..base import (
    EnginePort,
    MediaStream,
    PlatformProvider,
    ProcessedMedia,
    SessionAuth,
    SessionContext,
)

# yt-dlp 需要 Deno 执行 YouTube 的 n-sig / 签名挑战；缺失时只能降级到基础格式。
DENO_JS_RUNTIME = "deno"
YOUTUBE_OUTPUT_FOLDER = "YouTube"
_RESOLUTION_RE = re.compile(r"^(\d+)x(\d+)$")


class YouTubeProvider(PlatformProvider):
    """YouTube（常规视频 / Shorts / 播放列表 / 频道）的 SPI 适配器。

    Args:
        engine: 可选的执行引擎端口；不传时惰性构造默认 MediaDownloader，
            便于离线测试注入假引擎而不触发任何网络请求。
    """

    platform = Platform.YOUTUBE

    def __init__(self, engine: EnginePort | None = None) -> None:
        self._engine = engine

    def _resolve_engine(self) -> EnginePort:
        """惰性解析执行引擎，避免在包导入阶段拉起 yt-dlp 依赖。

        Returns:
            满足 EnginePort 协议的引擎实例。
        """
        if self._engine is None:
            from ...downloader import DownloaderConfig, MediaDownloader

            self._engine = MediaDownloader(DownloaderConfig())
        return self._engine

    def identify(self, url: str) -> UrlInfo:
        """识别 YouTube 链接并归一化为标准 UrlInfo。

        Args:
            url: 用户粘贴的分享文本或链接。

        Returns:
            平台为 YOUTUBE 的 UrlInfo。

        Raises:
            UnsupportedUrlError: 链接不属于 YouTube 或格式非法；异常文本不含
                原始 URL，避免把签名参数带进对外报错。
        """
        info = identify_url(url)
        if info.platform is not Platform.YOUTUBE:
            raise UnsupportedUrlError("URL does not belong to the YouTube platform")
        return info

    def prepare_session(self, context: SessionContext) -> SessionAuth:
        """决定匿名会话或 Chrome Cookie，并声明 Deno 运行时需求。

        Args:
            context: 会话输入约束，不含任何凭据明文。

        Returns:
            SessionAuth 策略描述；``js_runtime`` 固定为 deno。
        """
        use_browser = bool(context.use_chrome_cookies and context.cookie_browser)
        return SessionAuth(
            strategy="chrome-cookies" if use_browser else "anonymous",
            credentials_available=use_browser,
            js_runtime=DENO_JS_RUNTIME,
            notes=(
                "yt-dlp 以 Deno 沙箱执行 n-sig 挑战；Deno 缺失时降级到基础格式，"
                "不得静默把低清流转当最高画质交付。"
            ),
        )

    def discover(self, target: UrlInfo, auth: SessionAuth) -> list[DownloadItem]:
        """委托现有引擎展开单作品 / 播放列表 / 频道。

        Args:
            target: identify 阶段产出的标准 URL 信息。
            auth: 会话策略；当前仅用于说明，凭据由引擎按配置自行管理。

        Returns:
            引擎发现结果中的标准 DownloadItem 列表。

        Raises:
            DownloadCancelledError: 用户取消发现；由引擎抛出并向上传递。
        """
        result = self._resolve_engine().discover(
            target.url, self.platform, target.kind
        )
        return list(result.items)

    def probe_streams(
        self, item: DownloadItem, auth: SessionAuth
    ) -> list[MediaStream]:
        """把条目已实测的画质信息规整为标准 MediaStream 描述。

        迁移期内真实的多源探测与最高画质下限仍由 yt-dlp / FFprobe 在引擎侧
        完成；本方法只把其结果转成 SPI 标准结构，供统一选流策略消费。

        Args:
            item: 已发现的下载条目，可能带 selected_format 与 resolution。
            auth: 会话策略。

        Returns:
            含 0 或 1 个 MediaStream 的列表；无实测信息时为空列表。
        """
        width = height = None
        if item.resolution:
            matched = _RESOLUTION_RE.match(item.resolution)
            if matched:
                width, height = int(matched.group(1)), int(matched.group(2))
        if not item.selected_format and width is None:
            return []
        return [
            MediaStream(
                url=item.source_url,
                format_id=item.selected_format or "best",
                width=width,
                height=height,
                codec="",
                requires_js_runtime=auth.js_runtime == DENO_JS_RUNTIME,
            )
        ]

    def process_stream(
        self, stream: MediaStream, raw_data: bytes
    ) -> ProcessedMedia:
        """描述 YouTube 分离流与 JS 挑战结果的后续封装要求。

        Args:
            stream: 选中的媒体流描述。
            raw_data: 原始字节；流式下载场景由引擎直接落盘，可为空。

        Returns:
            ProcessedMedia，音视频分离时标记需要 FFmpeg 无损 remux。
        """
        return ProcessedMedia(
            payload=raw_data,
            container="mp4" if stream.requires_js_runtime else "webm",
            remux_required=stream.requires_js_runtime,
            notes=(
                "n-sig 挑战由 yt-dlp + Deno 在引擎侧完成；4K/8K 音视频分离轨"
                "由 FFmpeg 无损 remux，禁止重编码。"
            ),
        )

    def resolve_output_path(self, item: DownloadItem, root_dir: Path) -> Path:
        """按平台/作者两级归档规则生成安全的目标路径。

        Args:
            item: 下载条目。
            root_dir: 用户配置的下载根目录。

        Returns:
            ``root_dir/YouTube/{作者}/{日期}-{标题} [{ID}].mp4``，
            所有片段均经文件名清洗，不会越出 root_dir。
        """
        from ...engine.common import safe_component

        author = safe_component(item.author, fallback="Unknown-Author", limit=72)
        title = safe_component(item.title, fallback="YouTube", limit=72)
        identity = safe_component(item.media_id or item.id, limit=32)
        date = safe_component(item.upload_date or "Unknown-Date", limit=20)
        return (
            Path(root_dir)
            / YOUTUBE_OUTPUT_FOLDER
            / author
            / f"{date}-{title} [{identity}].mp4"
        )


def deno_available() -> bool:
    """检查 Deno JS 沙箱运行时是否随依赖安装就绪。

    Returns:
        PATH 中存在 deno 可执行文件，或 Python 环境中存在 deno 包装包时为 True。

    Notes:
        仅做存在性探测，不启动外部进程；具体挑战执行由 yt-dlp 负责。
    """
    import importlib.util
    import shutil

    return bool(shutil.which(DENO_JS_RUNTIME) or importlib.util.find_spec("deno"))


__all__ = ["DENO_JS_RUNTIME", "YOUTUBE_OUTPUT_FOLDER", "YouTubeProvider", "deno_available"]
