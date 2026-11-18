"""Agnes（OpenAI 兼容）理解层：描述/图片 → 各平台关键词，候选重排。

- 协议：POST {base_url}/chat/completions，Authorization: Bearer <key>
- 免费模型 agnes-3.0-flash 支持文本与 image_url 图片输入
- Agnes 未文档化 JSON mode，因此用提示词约束 + 代码块提取 + 一次修复重试，
  仍失败则退回“原样关键词”，保证搜索主链路永远可用
- 不打印、不回显 API Key；错误映射到项目统一的 SiteIssueCode
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from ..errors import DiscoveryError, SiteIssueCode, TemporaryAccessError

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 45.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_DATA_URI_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.I | re.S)
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}

SYSTEM_PROMPT = """你是媒体检索助手。把用户想找的内容转换成各平台的搜索关键词，只输出一个 JSON 对象。
规则：
1. 只输出 JSON，不要 Markdown 代码块，不要任何解释。
2. JSON 结构：{"description": "一句话概括用户想找的内容", "content_types": ["video"], "keywords": {"bilibili": ["关键词1", "关键词2"], "youtube": ["keyword one", "keyword two"]}}
3. 只为用户勾选的平台生成 keywords（未勾选的不要出现）。
4. 每个平台给 2-4 组关键词：中文平台（bilibili、xiaohongshu、douyin、kuaishou）用简短中文关键词；YouTube 用简短英文关键词，专有名词保留原文。
5. 每组关键词 1-4 个词，不要标点符号，不要 # 话题，不要整句描述。
6. content_types 从 video、image、live_photo 中选择，可多选。"""

REPAIR_PROMPT = "你上一条输出不是合法 JSON。请重新输出，只包含合法 JSON 对象，结构与字段保持不变。"

RERANK_PROMPT = """你是内容筛选助手。判断候选作品与用户需求的关联程度，只输出一个 JSON 对象。
规则：
1. 只输出 JSON，不要代码块与解释。
2. 结构：{"order": [候选编号...]}，按相关性从高到低排列，明显不相关的候选不要列入。
3. 候选编号只使用输入里给出的 i 字段。"""


@dataclass
class KeywordPlan:
    keywords: dict[str, list[str]]
    description: str = ""
    content_types: list[str] = field(default_factory=list)
    source: str = "raw"

    def keywords_for(self, platform_value: str, raw_query: str) -> list[str]:
        values = [k for k in self.keywords.get(platform_value, []) if k.strip()]
        return values or ([raw_query] if raw_query else [])


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中提取第一个合法 JSON 对象。"""

    candidates: list[str] = []
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text.strip())
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def validate_image_reference(value: str) -> str:
    """校验图片引用：公网 http(s) URL 或受支持的 data URI。

    返回可直接放进 image_url.url 的字符串。本地文件必须由前端转换为
    data URI 后提交；这里只做格式与大小校验，不发起网络请求。
    """

    value = value.strip()
    if not value:
        raise DiscoveryError("图片为空", issue_code=SiteIssueCode.LOCAL_CONFIGURATION)
    match = _DATA_URI_RE.match(value)
    if match:
        mime = match.group(1).lower()
        if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}:
            raise DiscoveryError(
                "只支持 PNG/JPEG/WebP/GIF 图片",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError) as exc:
            raise DiscoveryError(
                "图片 base64 数据无效",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            ) from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise DiscoveryError(
                "图片超过 8MB 限制，请压缩后再试",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        magic_mime = None
        for magic, name in _IMAGE_MAGIC.items():
            if raw.startswith(magic):
                magic_mime = name
                break
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            magic_mime = "image/webp"
        if magic_mime is None:
            raise DiscoveryError(
                "无法识别图片格式（仅支持 PNG/JPEG/WebP/GIF）",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        return value
    if value.lower().startswith(("http://", "https://")):
        return value
    raise DiscoveryError(
        "图片只支持 http(s) 链接或本地文件上传",
        issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
    )


class AgnesClient:
    """极简 OpenAI 兼容 Chat Completions 客户端（仅 requests，无新依赖）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.session = session or requests
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise TemporaryAccessError(
                "AI 服务响应超时，请稍后重试或关闭 AI 模式",
                issue_code=SiteIssueCode.NETWORK_ERROR,
            ) from exc
        except requests.RequestException as exc:
            raise TemporaryAccessError(
                "无法连接 AI 服务，请检查网络与 base_url 设置",
                issue_code=SiteIssueCode.NETWORK_ERROR,
            ) from exc
        status = response.status_code
        if status in (401, 403):
            raise DiscoveryError(
                "AI 服务拒绝访问：API Key 无效或权限不足，请在设置中更新",
                issue_code=SiteIssueCode.LOCAL_CONFIGURATION,
            )
        if status == 429:
            raise TemporaryAccessError(
                "AI 服务限流（429），请稍后重试",
                issue_code=SiteIssueCode.RATE_LIMITED,
            )
        if status >= 500:
            raise TemporaryAccessError(
                f"AI 服务暂时不可用（HTTP {status}）",
                issue_code=SiteIssueCode.SITE_UNAVAILABLE,
            )
        if status != 200:
            raise DiscoveryError(
                f"AI 服务返回异常状态（HTTP {status}）",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DiscoveryError(
                "AI 服务响应超过安全大小限制",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise DiscoveryError(
                "AI 服务返回的不是合法 JSON",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            ) from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DiscoveryError(
                "AI 服务响应缺少 choices[0].message.content",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            ) from exc
        if isinstance(content, list):
            # 部分兼容实现返回 content parts 数组
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise DiscoveryError(
                "AI 服务返回了空内容",
                issue_code=SiteIssueCode.SITE_RESPONSE_CHANGED,
            )
        return content


def _user_content(query: str, image: str | None, platforms: list[str]) -> Any:
    instruction = (
        f"用户想找的内容：{query or '（见图片）'}\n"
        f"需要搜索的平台：{', '.join(platforms)}\n"
        "请按系统要求输出 JSON。"
    )
    if not image:
        return instruction
    return [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": image}},
    ]


def _normalize_keywords(
    payload: dict[str, Any], platform_values: list[str], raw_query: str
) -> KeywordPlan | None:
    keywords_raw = payload.get("keywords")
    if not isinstance(keywords_raw, dict):
        return None
    keywords: dict[str, list[str]] = {}
    for platform in platform_values:
        values = keywords_raw.get(platform)
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str):
                continue
            text = re.sub(r"[#＃\n\r\t]+", " ", item).strip()
            text = re.sub(r"\s+", " ", text)
            if 0 < len(text) <= 40 and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= 4:
                break
        if cleaned:
            keywords[platform] = cleaned
    if not keywords:
        return None
    content_types = [
        item
        for item in payload.get("content_types", [])
        if isinstance(item, str) and item in {"video", "image", "live_photo"}
    ]
    description = str(payload.get("description") or "").strip()[:200]
    return KeywordPlan(
        keywords=keywords,
        description=description,
        content_types=content_types,
        source="ai",
    )


def plan_keywords(
    client: AgnesClient,
    *,
    query: str,
    image: str | None,
    platform_values: list[str],
) -> KeywordPlan:
    """生成各平台关键词；任何失败都退回原样关键词，不阻塞搜索。"""

    raw_plan = KeywordPlan(
        keywords={platform: [query] for platform in platform_values} if query else {},
        source="raw",
    )
    if not raw_plan.keywords:
        return raw_plan
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_content(query, image, platform_values)},
    ]
    try:
        content = client.chat(messages, max_tokens=800, temperature=0.2)
        payload = _extract_json_object(content)
        plan = _normalize_keywords(payload, platform_values, query) if payload else None
        if plan:
            return plan
        repaired = client.chat(
            messages
            + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": REPAIR_PROMPT},
            ],
            max_tokens=800,
            temperature=0.0,
        )
        payload = _extract_json_object(repaired)
        plan = _normalize_keywords(payload, platform_values, query) if payload else None
        if plan:
            return plan
    except DiscoveryError:
        raise
    except TemporaryAccessError:
        raise
    except Exception:  # noqa: BLE001 - 理解层失败必须降级而不是中断搜索
        return raw_plan
    return raw_plan


def rerank_hits(
    client: AgnesClient,
    *,
    query: str,
    hits: list[dict[str, Any]],
) -> list[int] | None:
    """返回按相关性排序的候选下标；失败或不适用时返回 None。"""

    if not query or len(hits) < 5:
        return None
    compact = [
        {
            "i": index,
            "platform": hit.get("platform"),
            "title": str(hit.get("title") or "")[:80],
            "author": str(hit.get("author") or "")[:40],
        }
        for index, hit in enumerate(hits[:20])
    ]
    messages = [
        {"role": "system", "content": RERANK_PROMPT},
        {
            "role": "user",
            "content": "用户需求：" + query + "\n候选：" + json.dumps(compact, ensure_ascii=False),
        },
    ]
    try:
        content = client.chat(messages, max_tokens=400, temperature=0.0)
    except Exception:  # noqa: BLE001 - 重排只是增强，失败保留原顺序
        return None
    payload = _extract_json_object(content)
    if not payload:
        return None
    order = payload.get("order")
    if not isinstance(order, list):
        return None
    seen: set[int] = set()
    result: list[int] = []
    for value in order:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if 0 <= value < len(hits) and value not in seen:
            seen.add(value)
            result.append(value)
    if not result:
        return None
    # 未提及的候选追加在末尾，避免因模型漏项丢结果。
    result.extend(index for index in range(len(hits)) if index not in seen)
    return result
