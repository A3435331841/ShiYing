"""Engine-backed PlatformProvider defaults shared by platform adapters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..models import DownloadItem, MediaType, Platform, UrlInfo
from ._identify import UnsupportedUrlError, identify_url
from .base import (
    EnginePort,
    MediaStream,
    PlatformProvider,
    ProcessedMedia,
    SessionAuth,
    SessionContext,
)

_RESOLUTION_RE = re.compile(r"^(\d+)x(\d+)$")


class EngineBackedProvider(PlatformProvider):
    platform: Platform
    output_folder: str | None = None
    default_extension = "mp4"
    session_strategy = "anonymous"
    session_notes = ""
    js_runtime: str | None = None
    container = "mp4"
    remux_required = False
    process_notes = "流处理由统一下载引擎执行"

    def __init__(self, engine: EnginePort | None = None) -> None:
        self._engine = engine

    def _resolve_engine(self) -> EnginePort:
        if self._engine is None:
            from ..downloader import DownloaderConfig, MediaDownloader

            self._engine = MediaDownloader(DownloaderConfig())
        return self._engine

    def identify(self, url: str) -> UrlInfo:
        info = identify_url(url)
        if info.platform is not self.platform:
            raise UnsupportedUrlError("URL does not belong to this platform")
        return info

    def prepare_session(self, context: SessionContext) -> SessionAuth:
        browser = (
            self.session_strategy == "chrome-cookies"
            and bool(context.use_chrome_cookies and context.cookie_browser)
        )
        return SessionAuth(
            strategy=(
                "chrome-cookies"
                if browser
                else "anonymous"
                if self.session_strategy == "chrome-cookies"
                else self.session_strategy
            ),
            credentials_available=browser,
            js_runtime=self.js_runtime,
            notes=self.session_notes,
        )

    def discover(
        self,
        target: UrlInfo,
        auth: SessionAuth,
        *,
        should_cancel: Any = None,
    ) -> Any:
        return self._resolve_engine().discover(
            target.url,
            self.platform,
            target.kind,
            should_cancel=should_cancel,
        )

    def probe_streams(
        self, item: DownloadItem, auth: SessionAuth
    ) -> list[MediaStream]:
        matched = _RESOLUTION_RE.match(item.resolution or "")
        dimensions = (
            (int(matched.group(1)), int(matched.group(2))) if matched else (None, None)
        )
        if not item.selected_format and dimensions[0] is None:
            return []
        return [
            MediaStream(
                url=item.source_url,
                format_id=item.selected_format or "best",
                width=dimensions[0],
                height=dimensions[1],
                requires_js_runtime=auth.js_runtime is not None,
            )
        ]

    def process_stream(
        self, stream: MediaStream, raw_data: bytes
    ) -> ProcessedMedia:
        return ProcessedMedia(
            payload=raw_data,
            container=self.container,
            remux_required=self.remux_required,
            notes=self.process_notes,
        )

    def resolve_output_path(self, item: DownloadItem, root_dir: Path) -> Path:
        from ..engine.common import safe_component

        author = safe_component(item.author, fallback="Unknown-Author", limit=72)
        identity = safe_component(item.media_id or item.id, limit=32)
        date = safe_component(item.upload_date or "Unknown-Date", limit=20)
        title = safe_component(item.title, fallback=self.platform.value, limit=72)
        extension = (
            "jpg"
            if item.media_type is MediaType.IMAGE
            else self.default_extension
        )
        directory = Path(root_dir)
        if self.output_folder:
            directory /= safe_component(self.output_folder, limit=72)
        return directory / author / f"{date}-{title} [{identity}].{extension}"
