"""平台 SPI 分包契约与 YouTube 适配器的离线回归测试。

覆盖历史 ``app.platforms`` 导入门面兼容、六大生命周期接口完整性、
注册中心线程安全语义，以及 YouTube 提供者的识别/会话/发现委托/画质/
封装/归档行为。全部使用临时目录与假引擎，不发起任何网络请求，也不读取
用户凭据。用例运行于 pytest 主进程，不涉及共享可变全局状态。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import DownloadItem, MediaType, Platform, SourceKind
from app.platforms import (
    MediaStream,
    PlatformProvider,
    ProviderRegistry,
    SessionContext,
    UnsupportedUrlError,
    build_provider_registry,
    identify_url,
    provider_for,
)
from app.platforms.base import ProcessedMedia
from app.platforms.youtube import (
    DENO_JS_RUNTIME,
    YOUTUBE_OUTPUT_FOLDER,
    YouTubeProvider,
    deno_available,
)


def test_legacy_platforms_facade_still_exports_identification_api():
    """platforms.py 迁入包后，旧导入路径与识别结果必须保持不变。"""
    assert identify_url("https://www.douyin.com/video/123").platform is Platform.DOUYIN
    assert identify_url("https://www.youtube.com/watch?v=abcdefghijk").platform is Platform.YOUTUBE
    with pytest.raises(UnsupportedUrlError):
        identify_url("https://example.com/not-supported")


def test_provider_contract_declares_all_six_lifecycle_hooks():
    """SPI 契约不得缺任何生命周期阶段。"""
    assert set(PlatformProvider.__abstractmethods__) == {
        "identify", "prepare_session", "discover", "probe_streams",
        "process_stream", "resolve_output_path",
    }


def test_registry_returns_registered_provider_and_snapshot():
    registry = ProviderRegistry()
    provider = YouTubeProvider(engine=SimpleNamespace())
    registry.register(provider)
    assert registry.get(Platform.YOUTUBE) is provider
    assert registry.platforms() == (Platform.YOUTUBE,)
    with pytest.raises(KeyError):
        registry.get(Platform.DOUYIN)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://youtu.be/abcdefghijk",
    "https://www.youtube.com/shorts/abcdefghijk",
])
def test_youtube_provider_identifies_supported_urls(url):
    info = YouTubeProvider().identify(url)
    assert info.platform is Platform.YOUTUBE
    assert info.kind in (SourceKind.ITEM, SourceKind.SHORT_LINK)


def test_youtube_provider_rejects_other_platforms_without_echoing_url():
    with pytest.raises(UnsupportedUrlError) as failure:
        YouTubeProvider().identify("https://www.douyin.com/video/123?token=secret")
    assert "douyin" not in str(failure.value)
    assert "secret" not in str(failure.value)


def test_prepare_session_declares_deno_and_never_carries_credentials():
    anonymous = YouTubeProvider().prepare_session(SessionContext())
    assert anonymous.strategy == "anonymous"
    assert anonymous.credentials_available is False
    assert anonymous.js_runtime == DENO_JS_RUNTIME

    browser = YouTubeProvider().prepare_session(
        SessionContext(use_chrome_cookies=True, cookie_browser="chrome")
    )
    assert browser.strategy == "chrome-cookies"
    assert browser.credentials_available is True
    assert "降级" in browser.notes


class _FakeEngine:
    """只实现 EnginePort.discover 的离线假引擎。"""

    def __init__(self):
        self.calls = []

    def discover(self, url, platform, kind, *, should_cancel=None):
        self.calls.append((url, platform, kind))
        return SimpleNamespace(items=[
            DownloadItem(id="video-1", media_id="abcdefghijk", source_url=url, title="Demo")
        ])


def test_discover_delegates_to_injected_engine():
    engine = _FakeEngine()
    provider = YouTubeProvider(engine=engine)
    target = provider.identify("https://www.youtube.com/watch?v=abcdefghijk")
    auth = provider.prepare_session(SessionContext())
    result = provider.discover(target, auth)
    assert [item.id for item in result.items] == ["video-1"]
    assert engine.calls == [(target.url, Platform.YOUTUBE, target.kind)]


def test_default_registry_covers_every_supported_platform():
    registry = build_provider_registry(engine=_FakeEngine())
    assert set(registry.platforms()) == set(Platform)
    assert len(registry.platforms()) == len(Platform)
    for platform in Platform:
        provider = registry.get(platform)
        assert provider.platform is platform
        assert isinstance(provider, PlatformProvider)


_PROVIDER_URLS = {
    Platform.DOUYIN: "https://www.douyin.com/video/1234567890",
    Platform.SELECTED_DOUYIN: "https://jingxuan.douyin.com/video/1234567890",
    Platform.XIAOHONGSHU: "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
    Platform.KUAISHOU: "https://www.kuaishou.com/short-video/3xvideo1",
    Platform.BILIBILI: "https://www.bilibili.com/video/BV1xx411c7mD",
    Platform.YOUTUBE: "https://www.youtube.com/watch?v=abcdefghijk",
    Platform.WECHAT: "https://weixin.qq.com/sph/demo-work",
}


@pytest.mark.parametrize(("platform", "url"), _PROVIDER_URLS.items())
def test_every_provider_identifies_its_platform_and_delegates_discovery(platform, url):
    engine = _FakeEngine()
    provider = provider_for(platform, engine=engine)
    target = provider.identify(url)
    assert target.platform is platform
    auth = provider.prepare_session(SessionContext())
    assert not hasattr(auth, "credentials")
    result = provider.discover(target, auth)
    assert [item.id for item in result.items] == ["video-1"]
    assert engine.calls == [(target.url, platform, target.kind)]


@pytest.mark.parametrize(("platform", "folder"), [
    (Platform.DOUYIN, "抖音"),
    (Platform.SELECTED_DOUYIN, "Selected-Douyin"),
    (Platform.XIAOHONGSHU, "小红书"),
    (Platform.KUAISHOU, "快手"),
    (Platform.BILIBILI, "B站"),
    (Platform.YOUTUBE, "YouTube"),
    (Platform.WECHAT, "视频号"),
])
def test_every_provider_resolves_safe_platform_output(platform, folder, tmp_path: Path):
    provider = provider_for(platform, engine=_FakeEngine())
    item = DownloadItem(
        id="item-1",
        media_id="media-1",
        source_url=_PROVIDER_URLS[platform],
        title="../../Demo: Title",
        author="../../Author\\\\Name",
        upload_date="2026-11-24",
    )
    path = provider.resolve_output_path(item, tmp_path)
    assert tmp_path in path.parents
    assert path.parts[-3] == folder
    assert ".." not in path.parts
    assert path.name.endswith("[media-1].mp4")


def test_wechat_provider_rejects_engine_bypassing_decryption():
    provider = provider_for(Platform.WECHAT, engine=_FakeEngine())
    with pytest.raises(RuntimeError, match="解密必须由下载引擎"):
        provider.process_stream(
            MediaStream(url="https://cdn.example/video", format_id="best"), b"encrypted"
        )


def test_probe_streams_uses_measured_resolution_and_sortable_structure():
    provider = YouTubeProvider(engine=_FakeEngine())
    auth = provider.prepare_session(SessionContext())
    item = DownloadItem(
        id="video-1", source_url="https://www.youtube.com/watch?v=abcdefghijk",
        title="Demo", resolution="3840x2160", selected_format="bestvideo+bestaudio",
        media_type=MediaType.VIDEO,
    )
    streams = provider.probe_streams(item, auth)
    assert len(streams) == 1
    assert (streams[0].width, streams[0].height) == (3840, 2160)
    assert streams[0].requires_js_runtime is True
    assert provider.probe_streams(
        DownloadItem(id="v", source_url="https://youtu.be/x", title="x"), auth
    ) == []


def test_process_stream_marks_lossless_remux():
    provider = YouTubeProvider(engine=_FakeEngine())
    stream = MediaStream(url="https://cdn.example/stream", format_id="best", requires_js_runtime=True)
    processed = provider.process_stream(stream, b"")
    assert isinstance(processed, ProcessedMedia)
    assert processed.remux_required is True
    assert "无损" in processed.notes


def test_resolve_output_path_sanitizes_author_and_stays_under_root(tmp_path: Path):
    provider = YouTubeProvider(engine=_FakeEngine())
    item = DownloadItem(
        id="video-1", media_id="abcdefghijk",
        source_url="https://www.youtube.com/watch?v=abcdefghijk",
        title="A/B: Demo", author="../../evil\\\\name", upload_date="2026-11-13",
    )
    path = provider.resolve_output_path(item, tmp_path)
    assert path.parts[-3] == YOUTUBE_OUTPUT_FOLDER
    assert ".." not in path.parts
    assert tmp_path in path.parents
    assert path.name.endswith("[abcdefghijk].mp4")


def test_deno_available_is_a_boolean_probe_without_side_effects():
    assert isinstance(deno_available(), bool)
