"""平台通道能力登记：支持/实验/不可用的唯一口径。

登记文件默认 ``data/capabilities.json``；缺失或损坏时全部按不可用处理，
而不是乐观地默认可下载。证据文件缺失只标记 ``evidence_missing``，不自动
升级或降级状态。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

KNOWN_PLATFORMS: tuple[str, ...] = (
    "xiaohongshu",
    "douyin",
    "selected-douyin",
    "kuaishou",
    "bilibili",
    "youtube",
    "wechat",
)
ALLOWED_STATES = frozenset({"supported", "experimental", "unavailable"})
DEFAULT_CHANNEL = "default"


class PlatformCapability(BaseModel):
    platform: str
    channel: str = DEFAULT_CHANNEL
    label: str = ""
    download: str = "unavailable"
    search: str = "unavailable"
    requires_login: bool = False
    confirm_required: bool = False
    reason: str = ""
    verified_at: str | None = None
    evidence: str | None = None
    evidence_missing: bool = False


class CapabilityRegistry(BaseModel):
    source: str = ""
    entries: list[PlatformCapability] = Field(default_factory=list)

    def get(self, platform: str, channel: str = DEFAULT_CHANNEL) -> PlatformCapability:
        for entry in self.entries:
            if entry.platform == platform and entry.channel == channel:
                return entry
        if channel != DEFAULT_CHANNEL:
            for entry in self.entries:
                if entry.platform == platform and entry.channel == DEFAULT_CHANNEL:
                    return entry
        return PlatformCapability(
            platform=platform,
            channel=channel,
            label=platform,
            reason="未登记平台，按不可用处理",
        )

    def all(self) -> list[PlatformCapability]:
        return list(self.entries)


def _state(value: object) -> str:
    return value if isinstance(value, str) and value in ALLOWED_STATES else "unavailable"


def _evidence_missing(evidence: object, root: Path, *, claimed: bool = False) -> bool:
    """证据是否缺失；claimed 表示该条目声称已验收（填写了 verified_at）。

    声称已验收却没有证据路径，与「填了路径但文件不存在」一样属于证据缺失，
    必须在 /api/platforms 标记出来，否则证据框架空转：verified_at 有值而
    evidence=null 会被当成「无需证据」。
    """
    if not isinstance(evidence, str) or not evidence.strip():
        return claimed
    candidate = Path(evidence)
    if not candidate.is_absolute():
        candidate = root / candidate
    return not candidate.is_file()


def _entry(
    *,
    platform: str,
    channel: str,
    data: dict,
    root: Path,
) -> PlatformCapability:
    evidence = data.get("evidence")
    download = _state(data.get("download"))
    confirm_required = data.get("confirm_required")
    if not isinstance(confirm_required, bool):
        confirm_required = download == "experimental"
    return PlatformCapability(
        platform=platform,
        channel=channel,
        label=str(data.get("label") or platform),
        download=download,
        search=_state(data.get("search")),
        requires_login=bool(data.get("requires_login")),
        confirm_required=confirm_required,
        reason=str(data.get("reason") or ""),
        verified_at=data.get("verified_at") if isinstance(data.get("verified_at"), str) else None,
        evidence=evidence if isinstance(evidence, str) else None,
        evidence_missing=_evidence_missing(
            evidence, root, claimed=bool(data.get("verified_at"))
        ),
    )


def _unavailable_all(reason: str, source: str) -> CapabilityRegistry:
    entries = [
        PlatformCapability(
            platform=platform,
            label=platform,
            reason=reason,
        )
        for platform in KNOWN_PLATFORMS
    ]
    return CapabilityRegistry(source=source, entries=entries)


def load_capabilities(
    path: str | Path,
    *,
    root: str | Path,
    known_platforms: Iterable[str] = KNOWN_PLATFORMS,
) -> CapabilityRegistry:
    source = str(path)
    registry = _unavailable_all("能力登记文件缺失，已按不可用处理", source)
    candidate = Path(path)
    # 配置里的相对路径必须相对项目根解析，否则从其它 cwd 启动时明明存在的
    # 登记文件也会被当成缺失，导致所有平台被判不可用、任务创建返回 422。
    if not candidate.is_absolute():
        candidate = Path(root) / candidate
    if not candidate.is_file():
        return registry
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _unavailable_all("能力登记文件无法解析，已按不可用处理", source)
    if not isinstance(payload, dict):
        return _unavailable_all("能力登记文件结构无效，已按不可用处理", source)
    raw_platforms = payload.get("platforms")
    if not isinstance(raw_platforms, dict):
        return _unavailable_all("能力登记文件缺少 platforms，已按不可用处理", source)
    root_path = Path(root)
    entries: list[PlatformCapability] = []
    for platform in known_platforms:
        raw = raw_platforms.get(platform)
        if not isinstance(raw, dict):
            entries.append(
                PlatformCapability(
                    platform=platform,
                    label=platform,
                    reason="平台未登记，按不可用处理",
                )
            )
            continue
        entries.append(
            _entry(platform=platform, channel=DEFAULT_CHANNEL, data=raw, root=root_path)
        )
        channels = raw.get("channels")
        if isinstance(channels, dict):
            for channel, channel_data in channels.items():
                if isinstance(channel, str) and isinstance(channel_data, dict):
                    entries.append(
                        _entry(
                            platform=platform,
                            channel=channel,
                            data=channel_data,
                            root=root_path,
                        )
                    )
    return CapabilityRegistry(source=source, entries=entries)


def creation_policy(
    registry: CapabilityRegistry,
    platform: str,
    *,
    confirmed: bool = False,
) -> tuple[bool, int, str, str]:
    entry = registry.get(platform)
    if entry.download == "unavailable":
        return (
            False,
            422,
            "unsupported_platform",
            entry.reason or "该平台当前不可用",
        )
    if entry.download == "experimental" and entry.confirm_required and not confirmed:
        return (
            False,
            409,
            "experimental_confirmation_required",
            entry.reason or "该平台为实验通道，需确认后创建任务",
        )
    return True, 200, "ok", ""


def environment_impact(checks: Iterable[dict]) -> list[dict]:
    failed = {
        str(check.get("id"))
        for check in checks
        if isinstance(check, dict) and not check.get("ok")
    }
    impacts: list[dict] = []
    if "ffmpeg" in failed:
        impacts.append(
            {"capability": "merging", "ok": False, "reason": "FFmpeg 缺失：无法合并音视频"}
        )
        impacts.append(
            {"capability": "frame_extract", "ok": False, "reason": "FFmpeg 缺失：无法抽帧"}
        )
    if "ffprobe" in failed:
        impacts.append(
            {"capability": "quality_probe", "ok": False, "reason": "FFprobe 缺失：无法探测画质"}
        )
    if "deno" in failed:
        impacts.append(
            {"capability": "youtube_hd", "ok": False, "reason": "Deno 缺失：YouTube 高清签名不可用，将降级基础格式"}
        )
    if "chrome" in failed:
        impacts.append(
            {"capability": "kuaishou", "ok": False, "reason": "Chrome 缺失：快手页面解析通道不可用"}
        )
    return impacts