"""AI 搜索前端交互（Node VM 注入，不启动浏览器）。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _run_home_script(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    marker = "  initialize();"
    assert marker in source
    source = source.replace(
        marker,
        """
  window.searchTest = {
    openSearch, submitSearch, renderSearchResults, updateSearchCount,
    downloadSearchSelected, state,
    setImage(value) { searchImageDataUrl = value; },
    setApi(value) { api = value; },
    setHooks() { toast = () => {}; refresh = async () => {}; },
  };
""",
    )
    script = """
const vm = require('vm');
const assert = require('assert');
const source = SOURCE;
const nodes = NODES;
const queryAll = QUERY_ALL;
const window = {};
const document = {
  querySelector: selector => nodes[selector],
  querySelectorAll: selector => queryAll(selector),
};
vm.runInNewContext(source, {window, document, URL, console, Date, Promise});
const test = window.searchTest;
test.setHooks();
const apiCalls = [];
test.setApi(async (path, method = 'GET', body) => {
  apiCalls.push([path, method, body]);
  if (path === '/api/search/status') {
    return {
      enabled: true, key_configured: true, model: 'agnes-3.0-flash', base_url: 'https://x',
      agent_reach: {available: false, install_hint: 'uv tool install ...'},
      platforms: [
        {value: 'bilibili', label: 'B站', searchable: true},
        {value: 'youtube', label: 'YouTube', searchable: true},
      ],
      unsearchable: {
        'selected-douyin': {label: '抖音精选', reason: '没有独立搜索入口'},
        wechat: {label: '视频号', reason: '没有公开搜索接口'},
      },
    };
  }
  if (path === '/api/search') {
    return {
      plan: {keywords: {bilibili: ['猫']}, description: '橘猫短视频', source: 'ai'},
      results: [{platform: 'bilibili', hits: [{media_id: 'BV1GJ411x7h7'}], issue: null}],
      hits: [
        {platform: 'bilibili', media_id: 'BV1GJ411x7h7', title: '测试标题',
         author: 'UP主', cover_url: 'https://i0.hdslb.com/a.jpg', duration_seconds: 65},
      ],
    };
  }
  if (path.startsWith('/api/jobs/exists')) {
    return {exists: body === undefined ? false : false, job: null};
  }
  if (path === '/api/jobs') {
    return {id: 'job-1'};
  }
  return {};
});
(async () => {
BODY
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    script = (
        script.replace("SOURCE", json.dumps(source, ensure_ascii=False), 1)
        .replace("NODES", body.get("nodes"), 1)
        .replace("QUERY_ALL", body.get("query_all"), 1)
        .replace("BODY", body.get("script"), 1)
    )
    path = tmp_path / "search-test.js"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=20
    )


def _nodes_js(extra: str = "") -> str:
    return (
        """{
  '#search-dialog': {showModal() { this.open = true; }, open: false},
  '#search-results-section': {hidden: true},
  '#search-results': {innerHTML: '', hidden: true},
  '#search-results-meta': {textContent: ''},
  '#search-message': {textContent: ''},
  '#search-selected-count': {textContent: '0'},
  '#search-download-selected': {disabled: true},
  '#search-image-state': {textContent: ''},
  '#search-plan-note': {hidden: true, textContent: ''},
  '#search-no-ai': {checked: false},
  '#search-query': {value: ''},
  '#search-image-url': {value: ''},
  '#search-limit': {value: '10'},
  '#search-submit': {disabled: false, textContent: '搜索'},
  '#search-platforms': {innerHTML: ''},
  '#search-image-file': {addEventListener() {}},
  """
        + extra
        + "}"
    )


def test_submit_search_sends_platforms_and_renders_results(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": """selector => {
  const boxes = [
    {value: 'bilibili', checked: true, disabled: false},
    {value: 'youtube', checked: false, disabled: false},
  ];
  if (selector.includes('#search-platforms')) {
    if (selector.includes(':checked')) return boxes.filter(box => box.checked);
    return boxes;
  }
  if (selector.includes('#search-results')) {
    return [];
  }
  return [];
}""",
            "script": """
await test.openSearch();
assert.ok(nodes['#search-platforms'].innerHTML.includes('bilibili'), 'platform html');
assert.ok(nodes['#search-platforms'].innerHTML.includes('视频号'), 'unsearchable html');
nodes['#search-query'].value = '橘猫睡觉';
await test.submitSearch();
const searchCall = apiCalls.find(call => call[0] === '/api/search');
assert.ok(searchCall, 'search call');
assert.strictEqual(searchCall[2].query, '橘猫睡觉');
assert.strictEqual(JSON.stringify(searchCall[2].platforms), JSON.stringify(['bilibili']));
assert.strictEqual(searchCall[2].ai_mode, true);
assert.strictEqual(searchCall[2].limit, 10);
assert.ok(nodes['#search-results'].innerHTML.includes('测试标题'), 'result title; html=' + JSON.stringify(nodes['#search-results'].innerHTML) + '; msg=' + nodes['#search-message'].textContent);
assert.ok(nodes['#search-results'].innerHTML.includes('/api/search/thumbnail?platform=bilibili'), 'thumb url');
assert.ok(nodes['#search-results-section'].hidden === false, 'results visible');
assert.ok(nodes['#search-message'].textContent === '', 'no error: ' + nodes['#search-message'].textContent);
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_selection_count_and_download_skip_existing(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": """selector => {
  const boxes = [
    {value: '0', checked: true, closest: () => ({classList: {toggle() {}}})},
    {value: '1', checked: true, closest: () => ({classList: {toggle() {}}})},
  ];
  if (selector.includes('#search-results')) {
    if (selector.includes(':checked')) return boxes.filter(box => box.checked);
    return boxes;
  }
  return [];
}""",
            "script": """
test.state.searchHits = [
  {platform: 'bilibili', media_id: 'BV1GJ411x7h7', source_url: 'https://www.bilibili.com/video/BV1GJ411x7h7'},
  {platform: 'bilibili', media_id: 'BV1xx411c7mD', source_url: 'https://www.bilibili.com/video/BV1xx411c7mD'},
];
let existsCalls = 0;
test.setApi(async (path, method, body) => {
  apiCalls.push([path, method, body]);
  if (path.startsWith('/api/jobs/exists')) {
    existsCalls += 1;
    return {exists: existsCalls === 1};
  }
  if (path === '/api/jobs') return {id: 'job-1'};
  return {};
});
test.updateSearchCount();
assert.strictEqual(nodes['#search-selected-count'].textContent, '2');
assert.strictEqual(nodes['#search-download-selected'].disabled, false);
await test.downloadSearchSelected();
const jobCalls = apiCalls.filter(call => call[0] === '/api/jobs');
assert.strictEqual(jobCalls.length, 1, 'existing job should be skipped');
assert.strictEqual(jobCalls[0][2].url, 'https://www.bilibili.com/video/BV1xx411c7mD');
assert(nodes['#search-message'].textContent.includes('已创建 1 个任务'));
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_empty_selection_disables_download(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": "selector => []",
            "script": """
test.updateSearchCount();
assert.strictEqual(nodes['#search-selected-count'].textContent, '0');
assert.strictEqual(nodes['#search-download-selected'].disabled, true);
""",
        },
    )
    assert result.returncode == 0, result.stderr
