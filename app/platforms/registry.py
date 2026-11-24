"""Uniform provider assembly for every supported platform."""

from __future__ import annotations

from ..models import Platform
from .base import EnginePort, PlatformProvider, ProviderRegistry
from .bilibili import BilibiliProvider
from .douyin import DouyinProvider
from .kuaishou import KuaishouProvider
from .selected_douyin import SelectedDouyinProvider
from .wechat import WeChatProvider
from .xiaohongshu import XiaohongshuProvider
from .youtube import YouTubeProvider

_PROVIDER_TYPES: tuple[type[PlatformProvider], ...] = (
    DouyinProvider,
    SelectedDouyinProvider,
    XiaohongshuProvider,
    KuaishouProvider,
    BilibiliProvider,
    YouTubeProvider,
    WeChatProvider,
)


def build_provider_registry(engine: EnginePort | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider_type in _PROVIDER_TYPES:
        registry.register(provider_type(engine=engine))
    return registry


def provider_for(
    platform: Platform,
    engine: EnginePort | None = None,
) -> PlatformProvider:
    return build_provider_registry(engine).get(platform)
