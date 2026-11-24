"""平台接入 SPI 抽象契约（PlatformProvider）与注册中心。

本模块定义「表现层 → 服务层 → 平台 SPI → 执行引擎」分层中的领域契约：
每个平台以独立子包实现六大标准生命周期钩子，核心调度与传输引擎不感知
平台逆向细节。随包提供的值对象均为不可变 dataclass，不持有网络句柄；
仅 ``ProviderRegistry`` 持有可变映射，内部用 RLock 保护，可由多个工作线程
安全共享。本模块自身不执行 I/O，可被主进程与异步线程池同时导入。

主要调用方：``app/platforms/<platform>/provider.py`` 的具体实现，以及
``app.platforms.registry`` 的统一装配入口。各平台通过统一契约委托现有
``MediaDownloader``，核心引擎不感知平台逆向细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from ..models import DownloadItem, Platform, SourceKind, UrlInfo


@dataclass(frozen=True)
class SessionContext:
    """一次平台会话的输入约束，不包含任何凭据明文。

    Attributes:
        use_chrome_cookies: 是否允许读取本机 Chrome 登录态。
        cookie_browser: 允许读取的浏览器标识，如 ``chrome``。
        cookie_profile: 可选的浏览器 Profile。
        metadata: 平台无关的附加上下文，仅供扩展使用。
    """

    use_chrome_cookies: bool = False
    cookie_browser: str | None = None
    cookie_profile: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionAuth:
    """会话鉴权策略的对外描述，严禁携带 Cookie 或 Token 明文。

    Attributes:
        strategy: ``anonymous`` / ``chrome-cookies`` / ``platform-secret`` 之一。
        credentials_available: 是否具备可用凭据，仅表示状态不保存内容。
        js_runtime: 该平台执行 JS 挑战所需的运行时，如 ``deno``。
        notes: 面向维护者的降级说明。
    """

    strategy: str
    credentials_available: bool = False
    js_runtime: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class MediaStream:
    """一条候选媒体流的标准化描述，不含签名播放地址中的敏感查询参数。

    Attributes:
        url: 候选地址（调用方须在展示前经脱敏处理）。
        format_id: yt-dlp / 平台侧的格式标识。
        width: 声明宽度，未知时为 None。
        height: 声明高度，未知时为 None。
        codec: 编码标识。
        requires_js_runtime: 是否需要 JS 挑战解密（如 YouTube n-sig）。
    """

    url: str
    format_id: str
    width: int | None = None
    height: int | None = None
    codec: str = ""
    requires_js_runtime: bool = False


@dataclass(frozen=True)
class ProcessedMedia:
    """媒体流处理后的结果描述。

    Attributes:
        payload: 处理后的字节；流式场景允许为空字节由引擎直接落盘。
        container: 目标容器，如 ``mp4`` / ``webm``。
        remux_required: 音视频分离轨是否需要 FFmpeg 无损封装合并。
        notes: 处理链说明，供诊断使用。
    """

    payload: bytes
    container: str
    remux_required: bool = False
    notes: str = ""


class EnginePort(Protocol):
    """现有下载引擎的最小端口，供平台提供者委托真实发现与传输。

    演进期间各平台仍复用 ``MediaDownloader``，通过此 Protocol 解耦，
    避免 SPI 包直接依赖引擎实现细节造成循环导入。
    """

    def discover(
        self,
        url: str,
        platform: Platform,
        kind: SourceKind,
        *,
        should_cancel: Any = None,
    ) -> Any:
        """执行一次作品发现并返回含 ``items`` 的结果对象。"""
        ...


class PlatformProvider(ABC):
    """全平台统一的六大标准生命周期接口。

    子类必须声明 ``platform`` 枚举并实现全部抽象方法。方法本身不得打印或
    持久化 Cookie、xsec_token 等凭据；对外异常须经
    ``safe_external_error_message()`` 脱敏。
    """

    platform: Platform

    @abstractmethod
    def identify(self, url: str) -> UrlInfo:
        """识别并归一化 URL，完成短链还原与防串号参数提取。

        Args:
            url: 用户粘贴的原始分享文本或链接。

        Returns:
            含规范 URL、平台枚举与资源类型的 UrlInfo。

        Raises:
            UnsupportedUrlError: URL 不属于本平台或格式非法。
        """
        ...

    @abstractmethod
    def prepare_session(self, context: SessionContext) -> SessionAuth:
        """决定匿名会话、系统浏览器 Cookie 还是平台专属凭据。

        Args:
            context: 本次会话的输入约束，不含凭据明文。

        Returns:
            SessionAuth 策略描述，不包含任何凭据内容。
        """
        ...

    @abstractmethod
    def discover(
        self,
        target: UrlInfo,
        auth: SessionAuth,
        *,
        should_cancel: Any = None,
    ) -> Any:
        """展开单作品、多 P、合集或主页，产出标准 DiscoveryResult 对象。

        Args:
            target: identify 阶段产出的标准 URL 信息。
            auth: 会话鉴权策略。
            should_cancel: 可选的取消谓词，由任务管理器传入。

        Returns:
            兼容 ``DiscoveryResult`` 的结果对象，保留作者、警告与完整性标记。

        Raises:
            DownloadCancelledError: 用户取消本次发现。
        """
        ...

    @abstractmethod
    def probe_streams(
        self, item: DownloadItem, auth: SessionAuth
    ) -> list[MediaStream]:
        """探测候选媒体流并按实测画质从高到低排序。

        Args:
            item: 待探测的下载条目。
            auth: 会话鉴权策略。

        Returns:
            MediaStream 列表，最高画质在前。
        """
        ...

    @abstractmethod
    def process_stream(
        self, stream: MediaStream, raw_data: bytes
    ) -> ProcessedMedia:
        """处理平台特化解密、JS 挑战结果或音视频分离流合并。

        Args:
            stream: 已选中的媒体流描述。
            raw_data: 原始字节；流式场景可为空。

        Returns:
            ProcessedMedia 结果与后续封装要求。
        """
        ...

    @abstractmethod
    def resolve_output_path(self, item: DownloadItem, root_dir: Path) -> Path:
        """按平台归档规范生成最终路径，处理实况成对与超长截断。

        Args:
            item: 已完成下载的条目。
            root_dir: 用户配置的下载根目录。

        Returns:
            不含非法字符、不会越出 root_dir 的目标路径。
        """
        ...


class ProviderRegistry:
    """平台枚举到 PlatformProvider 实例的线程安全注册表。

    只在进程内保存已构造的提供者，不序列化、不落盘；注册与查询均以
    RLock 保护，供调度层多线程按任务平台查找适配器。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._providers: dict[Platform, PlatformProvider] = {}

    def register(self, provider: PlatformProvider) -> None:
        """登记一个平台提供者，同平台重复注册以最后者为准。

        Args:
            provider: 已实现全部六大接口的提供者实例。

        Returns:
            None。
        """
        with self._lock:
            self._providers[provider.platform] = provider

    def get(self, platform: Platform) -> PlatformProvider:
        """按平台枚举取回提供者。

        Args:
            platform: 目标平台枚举。

        Returns:
            对应的 PlatformProvider 实例。

        Raises:
            KeyError: 该平台尚未注册提供者，调用方应回退到现有引擎路径。
        """
        with self._lock:
            return self._providers[platform]

    def platforms(self) -> tuple[Platform, ...]:
        """返回当前已注册的平台枚举快照。

        Returns:
            按注册顺序的平台元组；该快照不受后续并发注册影响。
        """
        with self._lock:
            return tuple(self._providers)
