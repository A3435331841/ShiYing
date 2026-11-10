"""Parser and asset extractor for Bilibili Opus articles & dynamic posts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit
import requests

from .errors import DiscoveryError
from .xiaohongshu import RemoteAsset

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Only the hosts Bilibili actually serves artwork from. Matching is by exact
# host or a dot-suffixed child, so look-alikes such as "hdslb.com.evil.test"
# never pass. Keep this list evidence-based; do not widen it by suffix guessing.
BILIBILI_ASSET_HOSTS = ("hdslb.com", "biliimg.com", "bilivideo.com")


def is_trusted_bilibili_asset_url(value: str) -> bool:
    """Return True only for an HTTPS Bilibili media URL on a standard port."""

    if not isinstance(value, str) or not value or len(value) > 8_192:
        return False
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not hostname
        ):
            return False
        if not any(
            hostname == host or hostname.endswith(f".{host}")
            for host in BILIBILI_ASSET_HOSTS
        ):
            return False
        return parsed.port in {None, 443}
    except (TypeError, ValueError):
        return False


def _safe_dimension(value) -> int | None:
    """Parse an upstream width/height that may be int, numeric string, or junk."""
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class BilibiliOpus:
    opus_id: str
    title: str
    author: str
    author_id: str
    text_content: str
    images: list[RemoteAsset] = field(default_factory=list)


def parse_bilibili_opus(url: str) -> BilibiliOpus:
    parsed = urlsplit(url)
    def trusted_page(value):
        p = urlsplit(value)
        try:
            return (p.scheme == "https" and p.hostname in {"bilibili.com", "www.bilibili.com"}
                    and p.port in (None, 443) and p.username is None and p.password is None)
        except ValueError:
            return False

    if not trusted_page(url):
        raise DiscoveryError("不可信的B站图文地址")
    opus_id = None
    match_opus = re.search(r"/opus/(\d+)", parsed.path)
    if match_opus:
        opus_id = match_opus.group(1)
    else:
        match_cv = re.search(r"/read/cv(\d+)", parsed.path)
        if match_cv:
            current = url
            for _ in range(6):
                resp = requests.get(current, headers={"User-Agent": UA}, timeout=15, allow_redirects=False)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                target = urljoin(current, resp.headers.get("Location", ""))
                if not resp.headers.get("Location") or not trusted_page(target):
                    raise DiscoveryError("B站专栏跳转地址不可信")
                current = target
                redirect_match = re.search(r"/opus/(\d+)", urlsplit(current).path)
                if redirect_match:
                    opus_id = redirect_match.group(1)
                    break
            if not opus_id:
                raise DiscoveryError("无法确认B站专栏对应的图文作品")

    if not opus_id:
        raise DiscoveryError("无法识别B站专栏或动态作品ID")

    api_url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail?id={opus_id}"
    res = requests.get(
        api_url,
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
        timeout=15,
        allow_redirects=False,
    )
    if res.status_code in (301, 302, 303, 307, 308):
        raise DiscoveryError("B站动态接口发生非预期跳转")
    if res.status_code != 200:
        raise DiscoveryError(f"请求B站动态接口失败 ({res.status_code})")
    try:
        data = res.json()
    except ValueError as exc:
        raise DiscoveryError("B站动态接口返回了无法解析的内容，请稍后重试。") from exc
    if not isinstance(data, dict) or data.get("code") != 0:
        raise DiscoveryError("B站动态接口返回异常，请稍后重试。")

    item = (data.get("data") or {}).get("item") or {}
    if not isinstance(item, dict):
        raise DiscoveryError("B站图文接口返回了意外的数据结构。")
    if str(item.get("id_str") or "") != opus_id:
        raise DiscoveryError("B站图文接口返回的作品身份不匹配")
    modules = item.get("modules", [])
    
    author_name = "B站UP主"
    author_id = ""
    title = ""
    text_parts = []
    raw_pics = []

    for mod in modules:
        mtype = mod.get("module_type")
        if mtype == "MODULE_TYPE_AUTHOR":
            ma = mod.get("module_author", {})
            author_name = ma.get("name") or author_name
            author_id = str(ma.get("mid") or "")
        elif mtype == "MODULE_TYPE_TITLE":
            mt = mod.get("module_title", {})
            title = mt.get("text") or title
        elif mtype == "MODULE_TYPE_CONTENT":
            mc = mod.get("module_content", {})
            for para in mc.get("paragraphs", []):
                ptype = para.get("para_type")
                if ptype == 1:
                    # Text paragraph
                    p_text = para.get("text") or {}
                    t = p_text.get("nodes") or []
                    for node in t:
                        n_word = node.get("word") if isinstance(node, dict) else None
                        word = n_word.get("words") if isinstance(n_word, dict) else None
                        if word:
                            text_parts.append(word)
                elif ptype == 2:
                    # Picture paragraph
                    p_info = para.get("pic") or {}
                    for p in p_info.get("pics", []):
                        if p.get("url"):
                            raw_pics.append(p)

    if not title:
        full_text = "".join(text_parts).strip()
        title = full_text[:40].replace("\n", " ") if full_text else "B站图文动态"

    images: list[RemoteAsset] = []
    for idx, pic in enumerate(raw_pics, 1):
        img_url = pic.get("url") or ""
        if not img_url:
            continue
        if img_url.startswith("http://"):
            img_url = "https://" + img_url[7:]
        if not is_trusted_bilibili_asset_url(img_url):
            continue
        images.append(
            RemoteAsset(
                candidates=[img_url],
                index=idx,
                width=_safe_dimension(pic.get("width")),
                height=_safe_dimension(pic.get("height")),
                size=None,
                format_id=f"bilibili-opus-{idx}",
            )
        )

    return BilibiliOpus(
        opus_id=opus_id,
        title=title,
        author=author_name,
        author_id=author_id,
        text_content="\n".join(text_parts),
        images=images,
    )
