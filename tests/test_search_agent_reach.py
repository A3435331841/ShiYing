"""Agent-Reach CLI 后端：JSON 解析、错误码映射、缺失二进制。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.errors import AuthenticationRequiredError, DiscoveryError, SiteIssueCode, TemporaryAccessError
from app.search.backends import agent_reach as module
from app.search.backends.agent_reach import AgentReachBackend, hit_from_item, find_agent_reach
from app.models import Platform


def _runner(payload=None, stdout=None, returncode=0):
    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout if stdout is not None else json.dumps(payload),
            stderr="",
        )
    return run


def test_success_maps_items_and_filters_other_platforms():
    payload = {
        "schema_version": 1,
        "ok": True,
        "channel": "bilibili",
        "action": "search",
        "backend": "cli",
        "items": [
            {
                "url": "https://www.bilibili.com/video/BV1GJ411x7h7",
                "title": "B站结果",
                "author": "UP主",
                "cover": "https://i0.hdslb.com/a.jpg",
                "duration": 61,
            },
            {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "其他平台"},
        ],
        "meta": {},
        "error": None,
    }
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(payload))
    hits = backend.search(Platform.BILIBILI, "测试", 5)
    assert [hit.title for hit in hits] == ["B站结果"]
    assert hits[0].media_id == "BV1GJ411x7h7"


def test_need_login_maps_to_auth_error():
    payload = {"ok": False, "error": {"code": "NEED_LOGIN", "message": "login required"}}
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(payload))
    with pytest.raises(AuthenticationRequiredError) as info:
        backend.search(Platform.XIAOHONGSHU, "kw", 5)
    assert info.value.issue_code is SiteIssueCode.LOGIN_REQUIRED
    assert "agent-reach login" in str(info.value)


def test_need_setup_maps_to_local_configuration():
    payload = {"ok": False, "error": {"code": "NEED_SETUP"}}
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(payload))
    with pytest.raises(DiscoveryError) as info:
        backend.search(Platform.BILIBILI, "kw", 5)
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION


def test_rate_limited_maps_to_rate_limited():
    payload = {"ok": False, "error": {"code": "RATE_LIMITED"}}
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(payload))
    with pytest.raises(TemporaryAccessError) as info:
        backend.search(Platform.YOUTUBE, "kw", 5)
    assert info.value.issue_code is SiteIssueCode.RATE_LIMITED


def test_not_found_returns_empty():
    payload = {"ok": False, "error": {"code": "NOT_FOUND"}}
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(payload))
    assert backend.search(Platform.YOUTUBE, "kw", 5) == []


def test_malformed_output_maps_to_response_changed():
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(stdout="not json"))
    with pytest.raises(DiscoveryError) as info:
        backend.search(Platform.BILIBILI, "kw", 5)
    assert info.value.issue_code is SiteIssueCode.SITE_RESPONSE_CHANGED


def test_json_embedded_in_logs_is_tolerated():
    stdout = 'log line\n{"ok": true, "items": [{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}], "error": null}\n'
    backend = AgentReachBackend(executable="agent-reach", runner=_runner(stdout=stdout))
    hits = backend.search(Platform.YOUTUBE, "kw", 5)
    assert len(hits) == 1


def test_missing_binary_is_unavailable_and_raises(monkeypatch):
    monkeypatch.setattr(module, "find_agent_reach", lambda: None)
    backend = AgentReachBackend(executable=None)
    assert backend.available() is False
    with pytest.raises(DiscoveryError):
        backend.search(Platform.BILIBILI, "kw", 5)


def test_hit_from_item_rejects_untrusted_urls():
    assert hit_from_item(Platform.BILIBILI, {"url": "javascript:alert(1)", "title": "x"}, "kw") is None
    assert hit_from_item(Platform.BILIBILI, {"url": "https://example.com/x", "title": "x"}, "kw") is None
