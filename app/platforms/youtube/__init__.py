"""YouTube 平台子包：对外暴露 SPI 提供者与 Deno 沙箱能力探测。

本包只做符号导出，导入时不会拉起 yt-dlp 或 Deno 进程；真实发现与传输在
调用提供者的 discover 方法时才惰性解析执行引擎，因此可被主进程与工作线程
安全导入。主要调用方：ProviderRegistry 注册流程与平台路由层。
"""

from .provider import (
    DENO_JS_RUNTIME,
    YOUTUBE_OUTPUT_FOLDER,
    YouTubeProvider,
    deno_available,
)

__all__ = [
    "DENO_JS_RUNTIME",
    "YOUTUBE_OUTPUT_FOLDER",
    "YouTubeProvider",
    "deno_available",
]
