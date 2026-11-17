"""平台接入聚合包：历史 URL 识别门面 + 新一代 SPI 契约导出。

历史上 URL 识别与归一化逻辑位于 ``app/platforms.py`` 单文件。为满足
「每个平台独立成包」的 SPI 分包规范，该实现已迁入
``app/platforms/_identify.py``，本模块原样重导出其公共符号，因此
``from app.platforms import identify_url`` 等 20 余处既有导入、旧测试与
脚本无需改动（公共兼容门面约束）。同时导出 PlatformProvider 抽象契约与
注册中心，供各平台子包实现六大生命周期接口。

本模块只做定义与重导出，不持有可变状态、不执行 I/O，可被主进程与工作
线程安全导入。主要调用方：app/main.py、app/engine/、app/manager/ 与测试套件。
"""

from __future__ import annotations

from ._identify import (
    Platform,
    SourceKind,
    UnsupportedUrlError,
    UrlInfo,
    extract_url,
    identify_url,
)
from .base import (
    EnginePort,
    MediaStream,
    PlatformProvider,
    ProcessedMedia,
    ProviderRegistry,
    SessionAuth,
    SessionContext,
)

__all__ = [
    "Platform",
    "SourceKind",
    "UnsupportedUrlError",
    "UrlInfo",
    "extract_url",
    "identify_url",
    "EnginePort",
    "MediaStream",
    "PlatformProvider",
    "ProcessedMedia",
    "ProviderRegistry",
    "SessionAuth",
    "SessionContext",
]
