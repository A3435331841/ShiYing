"""YouTube 搜索后端：条目规范化、去重与不可信链接丢弃（离线）。"""

from __future__ import annotations

from app.models import Platform
from app.search.backends.ytdlp import YtDlpSearchBackend, to_hit


class FakeEngine:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def search(self, query, platform, limit=10):
        self.calls.append((query, platform, limit))
        return self.entries


def test_youtube_entry_normalized_with_derived_thumbnail():
    hit = to_hit(
        {
            "id": "dQw4w9WgXcQ",
            "title": "  Test Video  ",
            "uploader": "Creator",
            "duration": 213,
            "upload_date": "20240102",
        },
        "test",
    )
    assert hit is not None
    assert hit.platform is Platform.YOUTUBE
    assert hit.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert hit.media_id == "dQw4w9WgXcQ"
    assert hit.title == "Test Video"
    assert hit.author == "Creator"
    assert hit.duration_seconds == 213
    assert hit.published_at == "2024-01-02"
    # ytsearch 扁平条目通常无缩略图：按 video id 推导固定封面
    assert hit.cover_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_invalid_or_missing_video_id_is_dropped():
    assert to_hit({"title": "no id"}, "kw") is None
    assert to_hit({"id": "short", "title": "bad id"}, "kw") is None
    # 只有非 YouTube 链接时也拒绝（不会把外部 URL 当结果）
    assert to_hit({"url": "https://example.com/video"}, "kw") is None


def test_backend_deduplicates_and_respects_limit():
    engine = FakeEngine(
        [
            {"id": "aaaaaaaaaaa", "title": "A"},
            {"id": "aaaaaaaaaaa", "title": "A duplicate"},
            {"id": "bbbbbbbbbbb", "title": "B"},
            {"id": "ccccccccccc", "title": "C"},
        ]
    )
    backend = YtDlpSearchBackend(engine)
    hits = backend.search(Platform.YOUTUBE, "kw", 2)
    assert [hit.title for hit in hits] == ["A", "B"]
    assert engine.calls == [("kw", Platform.YOUTUBE, 2)]


def test_backend_rejects_non_youtube_platform():
    backend = YtDlpSearchBackend(FakeEngine([]))
    try:
        backend.search(Platform.BILIBILI, "kw", 5)
    except Exception as exc:  # noqa: BLE001 - 只验证错误类型与问题码
        assert "yt-dlp" in str(exc)
    else:
        raise AssertionError("BILIBILI 不应走 yt-dlp 后端")
