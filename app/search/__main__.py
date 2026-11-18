"""独立使用形态：python -m app.search --json "关键词"。

只依赖本包与下载引擎，不启动 Web 服务；输出与 /api/search 相同的 JSON
结构（SearchResponse.model_dump）。用于验证包边界与将来抽离为独立 CLI。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..downloader import DownloaderConfig, MediaDownloader
from ..errors import DownloaderCoreError, SiteIssueCode
from ..models import Platform
from .key_store import SearchKeyStore
from .models import SEARCHABLE_PLATFORMS, SearchRequest
from .service import SearchConfig, SearchService


def _parse_platforms(value: str) -> list[Platform]:
    mapping = {platform.value: platform for platform in SEARCHABLE_PLATFORMS}
    result: list[Platform] = []
    for part in value.split(","):
        key = part.strip().lower()
        if not key:
            continue
        platform = mapping.get(key)
        if platform is None:
            raise SystemExit(f"不支持的平台：{part}（可选：{', '.join(mapping)}）")
        result.append(platform)
    return result or list(SEARCHABLE_PLATFORMS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.search",
        description="拾影 AI 搜索（独立 CLI）：描述/图片 → 各平台候选链接",
    )
    parser.add_argument("query", nargs="?", default="", help="内容描述；也可只给 --image")
    parser.add_argument("--image", default=None, help="图片 URL 或 data:image/...;base64,...")
    parser.add_argument(
        "--platforms",
        default=",".join(platform.value for platform in SEARCHABLE_PLATFORMS),
        help="逗号分隔的平台列表",
    )
    parser.add_argument("--limit", type=int, default=10, help="每平台条数（1-30）")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 理解，直接关键词搜索")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON（默认也是 JSON）")
    parser.add_argument("--base-url", default=os.environ.get("AGNES_BASE_URL", "https://api.agnes-ai.cn/v1"))
    parser.add_argument("--model", default=os.environ.get("AGNES_MODEL", "agnes-3.0-flash"))
    parser.add_argument(
        "--key-file",
        default=os.environ.get("AGNES_KEY_FILE", str(Path("data") / "llm-api-key.txt")),
    )
    parser.add_argument("--use-chrome-cookies", action="store_true")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args(argv)

    config = SearchConfig(
        enabled=True,
        base_url=args.base_url,
        model=args.model,
        use_chrome_cookies=args.use_chrome_cookies,
        chrome_profile=args.profile,
    )
    service = SearchService(
        config_provider=lambda: config,
        key_store=SearchKeyStore(Path(args.key_file)),
        engine_factory=lambda: MediaDownloader(
            DownloaderConfig(
                cookie_browser="chrome" if args.use_chrome_cookies else None,
                cookie_profile=args.profile,
            )
        ),
    )
    request = SearchRequest(
        query=args.query or None,
        image=args.image,
        platforms=_parse_platforms(args.platforms),
        limit=args.limit,
        ai_mode=not args.no_ai,
    )
    try:
        response = service.search(request)
    except DownloaderCoreError as exc:
        issue = exc.issue_code.value if exc.issue_code else SiteIssueCode.UNKNOWN.value
        print(
            json.dumps(
                {"ok": False, "error": {"code": issue, "message": str(exc)}},
                ensure_ascii=False,
            )
        )
        return 1
    payload = response.model_dump(mode="json")
    payload["ok"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
