"""Agnes 理解层：关键词 JSON 解析/修复/降级、图片校验、错误映射。"""

from __future__ import annotations

import base64
import json

import pytest
import requests

from app.errors import DiscoveryError, SiteIssueCode, TemporaryAccessError
from app.search.understand import (
    AgnesClient,
    KeywordPlan,
    _extract_json_object,
    plan_keywords,
    rerank_hits,
    validate_image_reference,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode("utf-8")

    def json(self):
        return self._payload


def _chat_payload(content: str):
    return {"choices": [{"message": {"content": content}}]}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _client(session):
    return AgnesClient(
        base_url="https://apihub.agnes-ai.cn/v1",
        api_key="sk-secret",
        model="agnes-3.0-flash",
        session=session,
    )


def test_extract_json_object_variants():
    assert _extract_json_object('{"a": 1}') == {"a": 1}
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_object('说明 {"a": {"b": 2}} 结束') == {"a": {"b": 2}}
    assert _extract_json_object("no json here") is None


def test_plan_keywords_parses_and_normalizes():
    payload = {
        "description": "橘猫短视频",
        "content_types": ["video"],
        "keywords": {
            "bilibili": ["橘猫 睡觉", "猫 键盘"],
            "youtube": ["orange cat sleeping", "cat on keyboard"],
        },
    }
    session = FakeSession([FakeResponse(payload=_chat_payload(json.dumps(payload, ensure_ascii=False)))])
    plan = plan_keywords(
        _client(session), query="橘猫睡觉", image=None, platform_values=["bilibili", "youtube"]
    )
    assert isinstance(plan, KeywordPlan)
    assert plan.source == "ai"
    assert plan.keywords["bilibili"] == ["橘猫 睡觉", "猫 键盘"]
    assert plan.description == "橘猫短视频"


def test_plan_keywords_uses_image_when_query_is_empty():
    payload = {"keywords": {"bilibili": ["橘猫 图片"]}}
    session = FakeSession(
        [FakeResponse(payload=_chat_payload(json.dumps(payload, ensure_ascii=False)))]
    )
    plan = plan_keywords(
        _client(session),
        query="",
        image="https://example.com/cat.jpg",
        platform_values=["bilibili"],
    )
    assert plan.source == "ai"
    assert plan.keywords["bilibili"] == ["橘猫 图片"]
    assert len(session.calls) == 1
    # 图片必须以 image_url 形式进入理解层请求，而不是被静默跳过。
    messages = session.calls[0][1]["json"]["messages"]
    assert messages[1]["content"][1]["image_url"]["url"] == "https://example.com/cat.jpg"


def test_plan_keywords_repairs_invalid_json_once():
    session = FakeSession(
        [
            FakeResponse(payload=_chat_payload("这不是 JSON")),
            FakeResponse(payload=_chat_payload('{"keywords": {"bilibili": ["修复成功"]}}')),
        ]
    )
    plan = plan_keywords(_client(session), query="x", image=None, platform_values=["bilibili"])
    assert plan.source == "ai"
    assert plan.keywords["bilibili"] == ["修复成功"]
    assert len(session.calls) == 2


def test_plan_keywords_falls_back_to_raw_after_two_failures():
    session = FakeSession(
        [
            FakeResponse(payload=_chat_payload("bad one")),
            FakeResponse(payload=_chat_payload("bad two")),
        ]
    )
    plan = plan_keywords(_client(session), query="原始关键词", image=None, platform_values=["bilibili"])
    assert plan.source == "raw"
    assert plan.keywords["bilibili"] == ["原始关键词"]


def test_http_401_maps_to_local_configuration():
    session = FakeSession([FakeResponse(status_code=401, payload={})])
    with pytest.raises(DiscoveryError) as info:
        _client(session).chat([{"role": "user", "content": "x"}])
    assert info.value.issue_code is SiteIssueCode.LOCAL_CONFIGURATION
    assert "sk-secret" not in str(info.value)


def test_http_429_maps_to_rate_limited():
    session = FakeSession([FakeResponse(status_code=429, payload={})])
    with pytest.raises(TemporaryAccessError) as info:
        _client(session).chat([{"role": "user", "content": "x"}])
    assert info.value.issue_code is SiteIssueCode.RATE_LIMITED


def test_timeout_maps_to_network_error():
    session = FakeSession([requests.Timeout("slow")])
    with pytest.raises(TemporaryAccessError) as info:
        _client(session).chat([{"role": "user", "content": "x"}])
    assert info.value.issue_code is SiteIssueCode.NETWORK_ERROR


def test_validate_image_reference_accepts_data_uri_and_url():
    data_uri = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
    assert validate_image_reference(data_uri) == data_uri
    assert validate_image_reference("https://example.com/a.jpg") == "https://example.com/a.jpg"


def test_validate_image_reference_rejects_bad_inputs():
    with pytest.raises(DiscoveryError):
        validate_image_reference("ftp://example.com/a.jpg")
    with pytest.raises(DiscoveryError):
        validate_image_reference("data:image/png;base64,not-base64!!!")
    # 伪装成 png 的文本内容
    fake = "data:image/png;base64," + base64.b64encode(b"plain text").decode("ascii")
    with pytest.raises(DiscoveryError):
        validate_image_reference(fake)
    oversize = "data:image/png;base64," + base64.b64encode(PNG_BYTES + b"\x00" * (8 * 1024 * 1024)).decode("ascii")
    with pytest.raises(DiscoveryError):
        validate_image_reference(oversize)


def test_rerank_hits_parses_order_and_appends_unmentioned():
    session = FakeSession([FakeResponse(payload=_chat_payload('{"order": [2, 0]}'))])
    order = rerank_hits(
        _client(session),
        query="猫",
        hits=[{"platform": "bilibili", "title": str(i)} for i in range(5)],
    )
    assert order == [2, 0, 1, 3, 4]


def test_rerank_hits_rejects_unshown_candidates_and_keeps_tail():
    session = FakeSession([FakeResponse(payload=_chat_payload('{"order": [25, 2]}'))])
    hits = [{"platform": "bilibili", "title": str(i)} for i in range(30)]
    order = rerank_hits(_client(session), query="猫", hits=hits)
    assert order == [2, *[index for index in range(30) if index != 2]]


def test_rerank_hits_failure_returns_none():
    session = FakeSession([FakeResponse(payload=_chat_payload("not json"))])
    assert rerank_hits(_client(session), query="猫", hits=[{"title": str(i)} for i in range(6)]) is None
