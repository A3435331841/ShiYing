"""\u5e73\u53f0 URL \u8bc6\u522b\u4e0e\u5f52\u4e00\u5316\u5206\u53d1\u603b\u7ebf\u3002

\u672c\u6a21\u5757\u8d1f\u8d23\u5904\u7406\u7528\u6237\u8f93\u5165\u7684\u5404\u79cd\u5f62\u6001\u7684\u591a\u5a92\u4f53\u5206\u4eab\u6587\u672c\u6216\u94fe\u63a5\uff0c\u5b8c\u6210\u4ee5\u4e0b\u6838\u5fc3\u4efb\u52a1\uff1a
1. \u4ece\u590d\u6742\u7684\u5206\u4eab\u6587\u672c\uff08\u5305\u542b\u4e2d\u6587\u3001Markdown\u3001\u8868\u60c5\u5305\uff09\u4e2d\u6b63\u5219\u62bd\u53d6\u6709\u6548 URL\uff1b
2. \u8fc7\u6ee4\u79c1\u7f51\u5730\u5740\u3001\u975e\u6cd5\u7aef\u53e3\u4e0e\u643a\u5e26\u660e\u6587\u8d26\u5bc6\u7684\u6076\u610f\u8f93\u5165\uff1b
3. \u5c06\u957f\u77ed\u94fe\u63a5\u3001\u5f39\u7a97\u94fe\u63a5\u4e0e\u5206\u4eab\u4ee3\u7801\u5f52\u4e00\u5316\u6620\u5c04\u4e3a\u5404\u5e73\u53f0\u89c4\u8303\u7684\u5355\u4f5c\u54c1 (ITEM)\u3001\u64ad\u653e\u5217\u8868 (PLAYLIST) \u6216\u4e3b\u9875 (PROFILE)\u3002
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .models import Platform, SourceKind, UrlInfo


class UnsupportedUrlError(ValueError):
    """\u5f53\u7528\u6237\u8f93\u5165\u7684 URL \u65e0\u6cd5\u88ab\u4efb\u4f55\u53d7\u652f\u6301\u7684\u5e73\u53f0\u89e3\u6790\u5668\u8bc6\u522b\u65f6\u629b\u51fa\u3002"""
    pass


# \u63d0\u53d6 HTTP/HTTPS URL \u7684\u57fa\u7840\u6b63\u5219\u4e0e Markdown \u8d85\u94fe\u63a5\u6b63\u5219
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MARKDOWN_URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
# \u624b\u673a\u7aef\u5206\u4eab\u5361\u7247\u672b\u5c3e\u5e38\u89c1\u7684\u4e2d\u6587\u4e0e\u82f1\u6587\u6807\u70b9\u5e72\u6270\u5b57\u7b26\u96c6\u5408
_TRAILING_SHARE_PUNCTUATION = ").,;!?]}\u3002\uff0c\uff1b\uff01\uff1f\u3011\uff09"


def _is_domain(host: str, domain: str) -> bool:
    """\u5224\u65ad\u76ee\u6807 host \u662f\u5426\u5c5e\u4e8e\u6307\u5b9a domain \u6216\u5176\u4e0b\u5c5e\u5b50\u57df\u540d\u3002"""
    return host == domain or host.endswith(f".{domain}")


def extract_url(value: str) -> str:
    """\u4ece\u7528\u6237\u8f93\u5165\u7684\u4efb\u610f\u6df7\u5408\u6587\u672c\u6216 Markdown \u8bed\u6cd5\u4e2d\u6e05\u6d17\u5e76\u63d0\u53d6\u6807\u51c6 HTTP(S) URL\u3002

    Args:
        value: \u7528\u6237\u8f93\u5165\u7684\u539f\u59cb\u5b57\u7b26\u4e32\uff08\u53ef\u80fd\u5305\u542b\u6587\u5b57\u63cf\u8ff0\u3001\u5206\u4eab\u8bdd\u672f\u3001Markdown \u8d85\u94fe\u63a5\uff09\u3002

    Returns:
        \u5f52\u4e00\u5316\u540e\u7684\u6807\u51c6 URL \u5b57\u7b26\u4e32\uff08\u5c0f\u5199\u534f\u8bae\u4e0e\u4e3b\u673a\u540d\uff0c\u53bb\u9664 fragment \u4e0e\u5c3e\u90e8\u5e72\u6270\u6807\u70b9\uff09\u3002

    Raises:
        UnsupportedUrlError: \u8f93\u5165\u975e\u5b8c\u6574 HTTP(S) \u5730\u5740\u3001\u643a\u5e26\u8d26\u5bc6\u6216\u7aef\u53e3\u975e\u6cd5\u65f6\u629b\u51fa\u3002
    """
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
    """\u89e3\u6790\u5e76\u8bc6\u522b URL \u6240\u5c5e\u5e73\u53f0\u4e0e\u8d44\u6e90\u5206\u7c7b\uff08\u5355\u4f5c\u54c1\u3001\u4e3b\u9875\u3001\u64ad\u653e\u5217\u8868\u3001\u77ed\u94fe\uff09\u3002

    \u672c\u51fd\u6570\u6839\u636e\u57df\u540d\u3001\u8def\u5f84\u4e0e\u67e5\u8be2\u53c2\u6570\u9010\u5e73\u53f0\u5339\u914d\uff0c\u5e76\u6267\u884c\u5173\u952e\u7684\u53cd\u4e32\u53f7\u5f52\u4e00\u5316\uff1a
    - \u5feb\u624b\uff1a\u8bc6\u522b\u77ed\u94fe\u3001\u666e\u901a\u64ad\u653e\u9875\u4e0e\u4f5c\u8005\u4e2a\u4eba\u4e3b\u9875\uff1b
    - \u5c0f\u7ea2\u4e66\uff1a\u8bc6\u522b xhslink \u77ed\u94fe\u3001/explore \u4f5c\u54c1\u9875\u4e0e /user/profile \u7528\u6237\u9875\uff1b
    - \u6296\u97f3\u4e0e\u6296\u97f3\u7cbe\u9009\uff1a\u5c06\u7f51\u9875\u5f39\u7a97\u3001\u5206\u4eab\u94fe\u63a5\u89c4\u8303\u5316\u4e3a /video/{id} \u6743\u5a01\u5730\u5740\uff1b
    - \u54d4\u54e9\u54d4\u54e9\uff1a\u8bc6\u522b\u5355P/\u591aP\u89c6\u9891\u3001\u64ad\u5355\u5408\u96c6\u3001\u52a8\u6001 Opus \u4e0e\u7a7a\u95f4\u4e3b\u9875\uff1b
    - YouTube\uff1a\u667a\u80fd\u533a\u5206\u5305\u542b &list= \u53c2\u6570\u7684\u5355\u89c6\u9891\u4e0e\u7eaf\u64ad\u653e\u5217\u8868\uff1b
    - \u5fae\u4fe1\u89c6\u9891\u53f7\uff1a\u6821\u9a8c /sph/ \u5206\u4eab\u9875\u5730\u5740\u3002

    Args:
        value: \u5f85\u8bc6\u522b\u7684\u94fe\u63a5\u6216\u5206\u4eab\u6587\u672c\u3002

    Returns:
        \u5305\u542b\u89c4\u8303\u5316 URL\u3001\u6240\u5c5e Platform \u679a\u4e3e\u4e0e SourceKind \u7c7b\u578b\u7684 UrlInfo \u4e0d\u53ef\u53d8\u6a21\u578b\u3002

    Raises:
        UnsupportedUrlError: URL \u683c\u5f0f\u4e0d\u7b26\u5408\u5df2\u77e5\u5e73\u53f0\u7684\u89c4\u8303\u6216\u4e3a\u4e0d\u53d7\u652f\u6301\u7684\u7b2c\u4e09\u65b9\u7ad9\u70b9\u3002
    """
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
