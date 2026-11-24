"""诊断包导出：白名单投影，剥离签名 query 与绝对路径。

不包含 Cookie、API Key、Authorization、临时直链或媒体 URL；文件信息仅保留
文件名、大小、后缀与是否存在。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1

_JOB_FIELDS = (
    "id",
    "status",
    "platform",
    "source_kind",
    "resolved_source_kind",
    "resolved_source_id",
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
    "revision",
    "total_items",
    "completed_items",
    "failed_items",
    "error",
    "warning",
    "issue_code",
    "issue_message",
    "diagnostic_code",
    "retryable",
)

_ITEM_FIELDS = (
    "id",
    "media_id",
    "title",
    "author",
    "upload_date",
    "media_type",
    "status",
    "resolution",
    "attempts",
    "error",
    "issue_code",
    "issue_message",
    "diagnostic_code",
    "playlist_index",
    "retryable",
)


def strip_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _file_facts(paths: list[str]) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for raw in paths:
        candidate = Path(raw)
        exists = False
        size = 0
        try:
            exists = candidate.is_file()
            if exists:
                size = candidate.stat().st_size
        except OSError:
            exists = False
        facts.append(
            {
                "name": candidate.name,
                "suffix": candidate.suffix.lower(),
                "size_bytes": size,
                "exists": exists,
            }
        )
    return facts


def _item_projection(item) -> dict[str, object]:
    payload = {
        field: _json_value(getattr(item, field, None)) for field in _ITEM_FIELDS
    }
    payload["file_facts"] = _file_facts(list(item.output_paths))
    return payload


def build_diagnostic_package(
    job,
    *,
    app_version: str,
    build_id: str,
) -> dict[str, object]:
    payload = {field: _json_value(getattr(job, field, None)) for field in _JOB_FIELDS}
    payload["source_url"] = strip_query(job.source_url)
    payload["output_dir_name"] = Path(job.output_dir).name if job.output_dir else ""
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app": {"version": app_version, "build_id": build_id},
        "job": payload,
        "items": [_item_projection(item) for item in job.items],
    }