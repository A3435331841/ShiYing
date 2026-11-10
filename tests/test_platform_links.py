from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_SITES = {
    "抖音": "https://www.douyin.com/",
    "抖音精选": "https://jingxuan.douyin.com/",
    "快手": "https://www.kuaishou.com/",
    "视频号": "https://channels.weixin.qq.com/",
    "小红书": "https://www.xiaohongshu.com/explore",
    "B站": "https://www.bilibili.com/",
    "YouTube": "https://www.youtube.com/",
}


class PlatformLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "platform-chip" in attrs.get("class", "").split():
            self.current = {"tag": tag, "attrs": attrs, "text": "", "icons": 0}
            self.links.append(self.current)
        elif tag == "img" and self.current is not None:
            self.current["icons"] += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if self.current is not None and tag == self.current["tag"]:
            self.current = None


def test_all_platform_icons_are_safe_official_links():
    page = PlatformLinks()
    page.feed((ROOT / "app/static/index.html").read_text(encoding="utf-8"))

    assert len(page.links) == len(OFFICIAL_SITES)
    assert [link["text"].strip() for link in page.links] == list(OFFICIAL_SITES)
    for link in page.links:
        attrs = link["attrs"]
        assert link["tag"] == "a"
        assert attrs["href"] == OFFICIAL_SITES[link["text"].strip()]
        assert attrs["target"] == "_blank"
        assert {"noopener", "noreferrer"} <= set(attrs["rel"].split())
        assert "默认浏览器" in attrs["title"]
        assert link["icons"] == 1
        assert not any(name.startswith("on") for name in attrs)


def test_limited_web_portals_are_described_honestly():
    page = PlatformLinks()
    page.feed((ROOT / "app/static/index.html").read_text(encoding="utf-8"))
    links = {link["text"].strip(): link["attrs"] for link in page.links}

    assert "应用下载" in links["抖音精选"]["title"]
    assert "视频号助手" in links["视频号"]["title"]
    assert "微信内" in links["视频号"]["title"]


def test_desktop_external_links_use_the_system_browser(monkeypatch):
    import launch_shiying

    started = []
    opened = []
    window = SimpleNamespace()
    webview = SimpleNamespace(
        settings={"OPEN_EXTERNAL_LINKS_IN_BROWSER": False},
        create_window=lambda *args, **kwargs: window,
        start=lambda **kwargs: started.append(kwargs),
    )
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(launch_shiying, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(launch_shiying, "healthy", lambda: True)
    monkeypatch.setattr(launch_shiying.webbrowser, "open", lambda url: opened.append(url))

    launch_shiying.launch()

    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert started[0]["gui"] == "edgechromium"
    assert opened == []


def test_failed_task_provides_safe_external_work_link(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")

    source = (ROOT / "app/static/home.js").read_text("utf-8")
    views_source = (ROOT / "app/static/task-views.js").read_text("utf-8")
    source = source.replace(
        "  initialize();",
        """
  window.testHarness = {
    detailUrl, renderTaskRow, formatTaskError, formatDetailIssue, renderDetails, state
  };
""",
    )
    script = f"""
const vm = require('vm'), assert = require('assert');
const window = {{
  ShiyingRuntime: {{
    localizedPrimaryJobIssueMessage: () => '发生了什么：测试异常',
    statusLabel: () => '下载失败',
  }}
}};
vm.runInNewContext({json.dumps(views_source)}, {{window, console}});
vm.runInNewContext({json.dumps(source)}, {{
  window,
  document: {{
    querySelector: () => ({{className: '', append() {{}}, textContent: ''}}),
    querySelectorAll: () => []
  }},
  URL,
  console
}});
const {{ detailUrl, renderTaskRow, formatTaskError, formatDetailIssue }} = window.testHarness;

// 1. detailUrl 安全校验
assert.strictEqual(detailUrl('https://weixin.qq.com/sph/test'), 'https://weixin.qq.com/sph/test');
assert.strictEqual(detailUrl('http://example.com/video/123'), 'http://example.com/video/123');
assert.strictEqual(detailUrl('https://user:pass@example.com/secret'), 'https://example.com/secret');
assert.strictEqual(detailUrl('javascript:alert(1)'), '');
assert.strictEqual(detailUrl('file:///C:/secret.txt'), '');
assert.strictEqual(detailUrl('data:text/html,hack'), '');
assert.strictEqual(detailUrl('vbscript:msgbox'), '');
assert.strictEqual(detailUrl(''), '');
assert.strictEqual(detailUrl(null), '');

// 2. 失败任务渲染外链按钮与菜单项
const failedJob = {{
  id: 'job-1',
  source_url: 'https://channels.weixin.qq.com/sph/12345',
  platform: 'wechat',
  status: 'failed',
  retryable: true,
  items: []
}};
const html = renderTaskRow('j:job-1', failedJob, false);
assert(html.includes('class="icon-button web-action"'), 'operations 应包含 web-action 外链按钮');
assert(html.includes('href="https://channels.weixin.qq.com/sph/12345"'));
assert(html.includes('target="_blank"'));
assert(html.includes('rel="noopener noreferrer"'));
assert(html.includes('在浏览器中打开作品'));
assert(html.includes('title="在默认浏览器中打开原网页观看"'));

// 3. 危险协议不渲染外链按钮
const xssJob = {{
  id: 'job-2',
  source_url: 'javascript:alert(1)',
  platform: 'wechat',
  status: 'failed',
  retryable: true,
  items: []
}};
const xssHtml = renderTaskRow('j:job-2', xssJob, false);
assert(!xssHtml.includes('web-action'), '危险协议不应渲染 web-action');
assert(!xssHtml.includes('javascript:alert(1)'), '危险协议不应进入 DOM');

// 4. 合集任务区分
const playlistJob = {{
  id: 'job-3',
  source_url: 'https://www.bilibili.com/medialist/play/123',
  platform: 'bilibili',
  source_kind: 'playlist',
  status: 'failed',
  retryable: true,
  items: []
}};
const playlistHtml = renderTaskRow('j:job-3', playlistJob, false);
assert(!playlistHtml.includes('class="icon-button web-action"'), '合集任务列表行不渲染单作品直达按钮');
assert(playlistHtml.includes('在浏览器中打开合集'), '合集更多菜单应提示在浏览器中打开合集');

// 5. 错误信息人性化提取 (formatTaskError)
const boilerplateIssue = `发生了什么：程序遇到了尚未归类的异常，暂时无法仅凭现有响应确定网站做了什么。
解决办法：先从原链接重试一次；如果相同错误再次出现...
技术详情：腾讯未返回播放信息。普通视频请更新腾讯元宝 Cookie 后重试。`;
const cleanError = formatTaskError(boilerplateIssue);
assert(!cleanError.includes('尚未归类的异常'), '列表首行错误不应显示泛化八股文');
assert(cleanError.includes('腾讯未返回播放信息'), '列表首行错误应提取关键事实');
assert(!cleanError.startsWith('技术详情：'), '应剥离技术详情标签');

const normalIssue = `发生了什么：网站明确返回了限流信号。程序已经停止继续请求。
解决办法：等待1-2分钟。`;
assert.strictEqual(formatTaskError(normalIssue), '网站明确返回了限流信号。程序已经停止继续请求。');

// 6. 详情弹窗结构化呈现 (formatDetailIssue)
const structuredHtml = formatDetailIssue(boilerplateIssue);
assert(structuredHtml.includes('class="detail-issue-label">原因<'), '结构化错误卡片应包含原因标签');
assert(structuredHtml.includes('class="detail-issue-label">建议<'), '结构化错误卡片应包含建议标签');
assert(structuredHtml.includes('腾讯未返回播放信息'), '结构化卡片应包含关键错误内容');
"""
    path = tmp_path / "link-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr

