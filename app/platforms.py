from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .models import Platform, SourceKind, UrlInfo


class UnsupportedUrlError(ValueError):
    pass


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MARKDOWN_URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_TRAILING_SHARE_PUNCTUATION = ").,;!?]}\u3002\uff0c\uff1b\uff01\uff1f\u3011\uff09"


def _is_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def extract_url(value: str) -> str:
    value = value.strip()
    markdown_match = _MARKDOWN_URL_RE.search(value)
    plain_match = _URL_RE.search(value)
    use_markdown = bool(
        markdown_match
        and (plain_match is None or markdown_match.start() <= plain_match.start())
    )
    match = markdown_match if use_markdown else plain_match
    if match:
        value = match.group(1 if use_markdown else 0).rstrip(
            _TRAILING_SHARE_PUNCTUATION
        )
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsupportedUrlError("Please enter a complete http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedUrlError("URLs containing credentials are not accepted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedUrlError("Invalid URL port") from exc
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=(parsed.hostname or "").lower().rstrip(".")
        + (f":{port}" if port is not None else ""),
        fragment="",
    )
    return urlunsplit(normalized)


def identify_url(value: str) -> UrlInfo:
    url = extract_url(value)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)
    query_with_blanks = parse_qs(parsed.query, keep_blank_values=True)

    if host in {"kuaishou.com", "www.kuaishou.com", "v.kuaishou.com", "m.kuaishou.com", "c.kuaishou.com", "m.gifshow.com"} or re.fullmatch(r"[a-z0-9-]+\.m\.chenzhongtech\.com", host):
        from .errors import DiscoveryError
        from .kuaishou import source_identity

        try:
            kind, _ = source_identity(url)
        except DiscoveryError as exc:
            raise UnsupportedUrlError(str(exc)) from exc
        return UrlInfo(url=url, platform=Platform.KUAISHOU, kind=SourceKind(kind))

    if _is_domain(host, "xhslink.com") or host == "xhslink.cn":
        return UrlInfo(
            url=url, platform=Platform.XIAOHONGSHU, kind=SourceKind.SHORT_LINK
        )
    if _is_domain(host, "xiaohongshu.com"):
        if re.search(r"/user/profile/[^/]+", path):
            kind = SourceKind.PROFILE
        elif re.search(r"/(?:explore|discovery/item)/[0-9a-f]+", path, re.IGNORECASE):
            kind = SourceKind.ITEM
        else:
            raise UnsupportedUrlError("Unsupported Xiaohongshu URL")
        return UrlInfo(url=url, platform=Platform.XIAOHONGSHU, kind=kind)

    if host == "v.douyin.com":
        return UrlInfo(url=url, platform=Platform.DOUYIN, kind=SourceKind.SHORT_LINK)
    if host in {"iesdouyin.com", "www.iesdouyin.com", "m.douyin.com"}:
        share = re.fullmatch(r"/share/(?:video|note|slides)/(\d+)", path)
        if share:
            return UrlInfo(
                url=f"https://www.douyin.com/video/{share.group(1)}",
                platform=Platform.DOUYIN,
                kind=SourceKind.ITEM,
            )
    if _is_domain(host, "douyin.com"):
        user_match = re.fullmatch(r"/user/[^/]+", path)
        modal_values = query_with_blanks.get("modal_id", [])
        vid_values = query_with_blanks.get("vid", [])
        item_query_present = (
            "modal_id" in query_with_blanks or "vid" in query_with_blanks
        )
        video_match = re.fullmatch(r"/video/(\d+)", path)
        if host == "jingxuan.douyin.com" and not video_match:
            video_match = re.fullmatch(r"/m/video/(\d+)", path)
        note_match = re.fullmatch(r"/note/(\d+)", path)
        if video_match or note_match:
            media_id = (video_match or note_match).group(1)
            url = f"https://www.douyin.com/video/{media_id}"
            kind = SourceKind.ITEM
        elif (user_match or path in ("", "/")) and item_query_present:
            for values in (modal_values, vid_values):
                if values and (
                    len(set(values)) != 1
                    or not all(re.fullmatch(r"[0-9]+", value) for value in values)
                ):
                    raise UnsupportedUrlError(
                        "Invalid or ambiguous Douyin modal/vid video URL"
                    )
            # The open profile modal can differ from the contextual vid.
            # Bind all later discovery, verification and retries to this item.
            media_id = (modal_values or vid_values)[0]
            url = f"https://www.douyin.com/video/{media_id}"
            kind = SourceKind.ITEM
        elif user_match:
            kind = SourceKind.PROFILE
        else:
            raise UnsupportedUrlError("Unsupported Douyin URL")
        return UrlInfo(url=url, platform=Platform.DOUYIN, kind=kind)

    if host == "b23.tv":
        return UrlInfo(url=url, platform=Platform.BILIBILI, kind=SourceKind.SHORT_LINK)
    if _is_domain(host, "bilibili.com"):
        if re.search(r"/(?:read/cv|opus/)", path):
            return UrlInfo(url=url, platform=Platform.BILIBILI, kind=SourceKind.ITEM)
        if any(
            token in path
            for token in ("/lists/", "/favlist", "/medialist/", "/channel/", "/list/")
        ):
            kind = SourceKind.PLAYLIST
        elif host == "space.bilibili.com" and re.match(r"/\d+", path):
            kind = SourceKind.PROFILE
        else:
            kind = SourceKind.ITEM
        return UrlInfo(url=url, platform=Platform.BILIBILI, kind=kind)

    if host == "youtu.be" or _is_domain(host, "youtube.com"):
        # watch?v=X&list=Y 与 youtu.be/ID?list=Y 是"播放列表里的某个视频"
        # 最常见的复制形态，用户意图是单个视频；只有 /playlist 路径或
        # 不带明确单视频标识的 list 链接才按整个播放列表处理。
        v_values = query.get("v") or []
        has_single_video = (
            len(v_values) == 1 and re.fullmatch(r"[\w-]{5,}", v_values[0])
        ) or (host == "youtu.be" and re.fullmatch(r"/[\w-]{5,}", path))
        if "/playlist" in path or ("list" in query and not has_single_video):
            kind = SourceKind.PLAYLIST
        elif re.match(
            r"/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(?:/(?:videos|shorts|streams))?$",
            path,
        ):
            kind = SourceKind.PROFILE
        else:
            kind = SourceKind.ITEM
        return UrlInfo(url=url, platform=Platform.YOUTUBE, kind=kind)

    if host in ("weixin.qq.com", "channels.weixin.qq.com"):
        if path.startswith("/sph/") or path.startswith("/finder-preview/pages/"):
            return UrlInfo(url=url, platform=Platform.WECHAT, kind=SourceKind.ITEM)
        raise UnsupportedUrlError("这个地址不是视频号分享页。")

    raise UnsupportedUrlError(
        "Only Xiaohongshu, Douyin, Kuaishou, Bilibili, WeChat Channels, and YouTube URLs are supported"
    )
