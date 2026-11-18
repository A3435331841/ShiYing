"""python -m app.search CLI：输出契约与错误路径。"""

from __future__ import annotations

import json

import pytest

from app.errors import DiscoveryError, SiteIssueCode
from app.search import __main__ as cli
from app.search.models import SearchHit, SearchPlan, SearchResponse


class FakeService:
    last_request = None

    def __init__(self, **kwargs):
        pass

    def search(self, request):
        FakeService.last_request = request
        return SearchResponse(
            plan=SearchPlan(keywords={"bilibili": ["猫"]}),
            results=[],
            hits=[
                SearchHit(
                    platform=request.platforms[0],
                    source_url="https://www.bilibili.com/video/BV1GJ411x7h7",
                    media_id="BV1GJ411x7h7",
                    title="CLI 结果",
                )
            ],
        )


class FailingService:
    def __init__(self, **kwargs):
        pass

    def search(self, request):
        raise DiscoveryError("配置缺失", issue_code=SiteIssueCode.LOCAL_CONFIGURATION)


def test_cli_outputs_json_and_parses_platforms(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SearchService", FakeService)
    exit_code = cli.main(["猫", "--platforms", "bilibili", "--no-ai", "--limit", "3"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["hits"][0]["title"] == "CLI 结果"
    assert FakeService.last_request.query == "猫"
    assert FakeService.last_request.ai_mode is False
    assert FakeService.last_request.limit == 3


def test_cli_reports_errors_as_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SearchService", FailingService)
    exit_code = cli.main(["猫", "--platforms", "bilibili"])
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == SiteIssueCode.LOCAL_CONFIGURATION.value


def test_cli_rejects_unknown_platform(capsys):
    with pytest.raises(SystemExit):
        cli.main(["猫", "--platforms", "wechat"])
