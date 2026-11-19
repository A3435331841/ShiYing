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
  // VM 没有完整的刷新/SSE 环境，不执行 initialize() 的后台流程；显式导出 bind，
  // 让测试先注册页面上的真实监听，再通过 fire() 触发点击/输入事件。
  window.searchTest = {
    api, bind, initialize, openSearch, submitSearch, renderSearchResults, updateSearchCount,
    downloadSearchSelected, setSearchSelection, bindSearchImage, state,
    setImage(value) { searchImageDataUrl = value; },
    getImage() { return searchImageDataUrl; },
    setApi(value) { api = value; },
    setBind(value) { bind = value; },
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
let fetchImpl = null;
class FakeFileReader {
  readAsDataURL(file) {
    if (file && file.__error) {
      if (this.onerror) this.onerror(new Error('read failed'));
      return;
    }
    this.result = file ? file.__dataUrl || '' : '';
    if (this.onload) this.onload();
  }
}
const window = {};
window.addEventListener = () => {};
const document = {
  // bind() 会为页面里的所有控件注册监听；缺失的节点用可记录监听的占位节点补上，
  // 这样测试可以真正点击按钮，而不是直接调用内部函数绕过绑定。
  querySelector: selector => {
    const node = nodes[selector] || (nodes[selector] = {});
    if (!node.handlers) node.handlers = {};
    if (!node.addEventListener) {
      node.addEventListener = function (type, handler) {
        (this.handlers[type] || (this.handlers[type] = [])).push(handler);
      };
    }
    return node;
  },
  querySelectorAll: selector => queryAll(selector),
  documentElement: {dataset: {}},
  body: {innerText: ''},
  hidden: false,
  addEventListener: () => {},
  createElement: () => ({classList: {add() {}}, append() {}, remove() {}, addEventListener() {}}),
};
vm.runInNewContext(source, {
  window, document, URL, console, Date, Promise, AbortSignal, FileReader: FakeFileReader,
  fetch: (...args) => {
    if (!fetchImpl) throw new Error('fetch was not stubbed');
    return fetchImpl(...args);
  },
});
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
async function fire(selector, type, extra = {}) {
  const node = nodes[selector];
  assert.ok(
    node && node.handlers && node.handlers[type] && node.handlers[type].length,
    'missing ' + type + ' listener on ' + selector,
  );
  for (const handler of node.handlers[type]) {
    await handler({target: node, currentTarget: node, preventDefault() {}, ...extra});
  }
}
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
  '#search-image-file': {},
  """
        + extra
        + "}"
    )


# 从 renderSearchPlatforms / renderSearchResults 真正写出的 HTML 解析复选框，
# 让“平台选择”和“结果选择”都来自渲染结果，而不是测试里另写一份死数据。
_DOM_QUERY_ALL = """selector => {
  const boxesFor = containerSelector => {
    const container = nodes[containerSelector] || (nodes[containerSelector] = {});
    const html = String(container.innerHTML || '');
    if (container._html !== html) {
      container._html = html;
      const boxes = [];
      const pattern = /<input type="checkbox" value="([^"]*)"([^>]*)>/g;
      let match;
      while ((match = pattern.exec(html))) {
        const attrs = match[2];
        boxes.push({
          value: match[1],
          checked: attrs.indexOf('checked') >= 0,
          disabled: attrs.indexOf('disabled') >= 0,
          closest() { return {classList: {toggle() {}}}; },
        });
      }
      container._boxes = boxes;
    }
    return container._boxes;
  };
  if (selector.indexOf('#search-platforms') >= 0) {
    const boxes = boxesFor('#search-platforms');
    return selector.indexOf(':checked') >= 0
      ? boxes.filter(box => box.checked && !box.disabled)
      : boxes;
  }
  if (selector.indexOf('#search-results') >= 0) {
    const boxes = boxesFor('#search-results');
    return selector.indexOf(':checked') >= 0 ? boxes.filter(box => box.checked) : boxes;
  }
  return [];
}"""


def test_submit_search_sends_platforms_and_renders_results(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": _DOM_QUERY_ALL,
            "script": """
test.bind();
await fire('#ai-search-button', 'click');
assert.ok(nodes['#search-platforms'].innerHTML.includes('bilibili'), 'platform html');
assert.ok(nodes['#search-platforms'].innerHTML.includes('视频号'), 'unsearchable html');
// 平台勾选来自 renderSearchPlatforms 写出的 HTML，而不是测试里另写的死数据。
const platformBoxes = document.querySelectorAll('#search-platforms input[type="checkbox"]');
const enabledPlatforms = platformBoxes.filter(box => !box.disabled);
assert.strictEqual(enabledPlatforms.length, 2, 'rendered platform checkboxes');
assert.strictEqual(platformBoxes.length, 4, 'unsearchable platforms stay disabled');
enabledPlatforms.find(box => box.value === 'youtube').checked = false;
nodes['#search-query'].value = '橘猫睡觉';
await fire('#search-submit', 'click');
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
            "query_all": _DOM_QUERY_ALL,
            "script": """
const hits = [
  {platform: 'bilibili', media_id: 'BV1GJ411x7h7', source_url: 'https://www.bilibili.com/video/BV1GJ411x7h7', title: 'A'},
  {platform: 'bilibili', media_id: 'BV1xx411c7mD', source_url: 'https://www.bilibili.com/video/BV1xx411c7mD', title: 'B'},
];
let existsCalls = 0;
test.setApi(async (path, method = 'GET', body) => {
  apiCalls.push([path, method, body]);
  if (path === '/api/search/status') {
    return {
      enabled: true, key_configured: true, model: 'agnes-3.0-flash', base_url: 'https://x',
      agent_reach: {available: false},
      platforms: [{value: 'bilibili', label: 'B站'}], unsearchable: {},
    };
  }
  if (path === '/api/search') {
    return {
      plan: {keywords: {bilibili: ['猫']}, description: '', source: 'raw'},
      results: [{platform: 'bilibili', hits: hits, issue: null}],
      hits,
    };
  }
  if (path.startsWith('/api/jobs/exists')) {
    existsCalls += 1;
    return {exists: existsCalls === 1};
  }
  if (path === '/api/jobs') return {id: 'job-1'};
  return {};
});
test.bind();
await fire('#ai-search-button', 'click');
nodes['#search-query'].value = '猫';
await fire('#search-submit', 'click');
// 结果复选框来自 renderSearchResults 写出的 HTML，state.searchHits 也由真实响应填充。
const boxes = document.querySelectorAll('#search-results input[type="checkbox"]');
assert.strictEqual(boxes.length, 2, 'rendered result checkboxes');
assert.strictEqual(nodes['#search-selected-count'].textContent, '2');
assert.strictEqual(nodes['#search-download-selected'].disabled, false);
boxes[0].checked = false;
await fire('#search-results', 'change');
assert.strictEqual(nodes['#search-selected-count'].textContent, '1');
boxes[0].checked = true;
await fire('#search-unselect-all', 'click');
assert.strictEqual(nodes['#search-selected-count'].textContent, '0');
assert.strictEqual(nodes['#search-download-selected'].disabled, true);
await fire('#search-select-all', 'click');
assert.strictEqual(nodes['#search-selected-count'].textContent, '2');
assert.strictEqual(nodes['#search-download-selected'].disabled, false);
await fire('#search-download-selected', 'click');
const jobCalls = apiCalls.filter(call => call[0] === '/api/jobs');
assert.strictEqual(jobCalls.length, 1, 'existing job should be skipped');
assert.strictEqual(jobCalls[0][2].url, 'https://www.bilibili.com/video/BV1xx411c7mD');
assert(nodes['#search-message'].textContent.includes('已创建 1 个任务'));
assert(nodes['#search-message'].textContent.includes('跳过 1 个已有任务'));
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_api_surfaces_object_detail_and_pydantic_detail(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": "selector => []",
            "script": """
const cases = [
  [{detail: {message: '图片搜索需要先在设置中配置 AI API Key', issue_code: 'local_configuration'}},
   422, '图片搜索需要先在设置中配置 AI API Key'],
  [{detail: [{msg: '该平台暂不支持搜索：wechat'}]}, 422, '该平台暂不支持搜索：wechat'],
  [{}, 500, '请求失败（500），请稍后重试。'],
];
for (const [payload, status, expected] of cases) {
  fetchImpl = async () => ({ok: false, status, json: async () => payload});
  let message = null;
  try {
    await test.api('/api/search', 'POST', {query: 'x'});
  } catch (error) {
    message = error.message;
    assert.strictEqual(error.httpStatus, status);
  }
  assert.strictEqual(message, expected);
}
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_reopening_search_discards_stale_uploaded_image(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": """selector => {
  const boxes = [{value: 'bilibili', checked: true, disabled: false}];
  if (selector.includes('#search-platforms')) {
    if (selector.includes(':checked')) return boxes.filter(box => box.checked);
    return boxes;
  }
  return [];
}""",
            "script": """
test.setImage('data:image/png;base64,AAA');
nodes['#search-image-file'].value = 'C:\\\\fakepath\\\\old.png';
await test.openSearch();
assert.strictEqual(test.getImage(), '', 'stale image should be cleared');
assert.strictEqual(nodes['#search-image-file'].value, '', 'file input should be cleared');
assert.strictEqual(nodes['#search-image-state'].textContent, '');
nodes['#search-query'].value = '第二个描述';
nodes['#search-image-url'].value = 'https://example.com/other.png';
await test.submitSearch();
const searchCall = apiCalls.find(call => call[0] === '/api/search');
assert.ok(searchCall, 'search call');
assert.strictEqual(searchCall[2].query, '第二个描述');
assert.strictEqual(searchCall[2].image, 'https://example.com/other.png');
assert.strictEqual(nodes['#search-message'].textContent, '');
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_local_image_is_checked_by_encoded_data_url_length(tmp_path):
    file_node = """'#search-image-file': (() => {
  const node = {value: '', files: [], handler: null, addEventListener(type, fn) { if (type === 'change') this.handler = fn; }};
  return node;
})(),"""
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(file_node),
            "query_all": "selector => []",
            "script": """
test.bindSearchImage();
const fileNode = nodes['#search-image-file'];
const LIMIT = 8 * 1024 * 1024;
function choose(size, dataUrl) {
  fileNode.value = 'C:\\\\fakepath\\\\big.png';
  fileNode.files = [{name: 'big.png', size, __dataUrl: dataUrl}];
  fileNode.handler();
}
choose(LIMIT + 1, 'data:image/png;base64,' + 'A'.repeat(100));
assert.strictEqual(test.getImage(), '', 'raw oversize should be rejected');
assert.ok(nodes['#search-image-state'].textContent.includes('8MB'), nodes['#search-image-state'].textContent);
assert.strictEqual(fileNode.value, '', 'file input reset after raw rejection');
// 后端上限是 8MiB 原始字节换算出的 data URI 字符数（app/search/models.py 的
// (8MiB+2)//3*4+64），6MB 原图编码后约 8.4M 字符，必须被前端放行。
const ENCODED_LIMIT = 11184876;
const sixMegabyte = 'data:image/png;base64,' + 'A'.repeat(8400000);
choose(6 * 1024 * 1024, sixMegabyte);
assert.strictEqual(test.getImage(), sixMegabyte, '6MB image must pass the encoded limit');
choose(8 * 1024 * 1024, 'x'.repeat(ENCODED_LIMIT));
assert.strictEqual(test.getImage().length, ENCODED_LIMIT, 'data URL at the limit must be accepted');
choose(8 * 1024 * 1024, 'x'.repeat(ENCODED_LIMIT + 1));
assert.strictEqual(test.getImage(), '', 'base64 oversize should be rejected');
assert.ok(nodes['#search-image-state'].textContent.includes('超过上限'), nodes['#search-image-state'].textContent);
assert.strictEqual(fileNode.value, '', 'file input reset after encoded rejection');
const valid = 'data:image/png;base64,' + 'A'.repeat(100);
choose(75, valid);
assert.strictEqual(test.getImage(), valid);
assert.ok(nodes['#search-image-state'].textContent.includes('已选择：big.png'));
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_empty_selection_disables_download(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": _DOM_QUERY_ALL,
            "script": """
test.bind();
// 通过真正的“取消全选”按钮触发计数，而不是直接调用 updateSearchCount。
await fire('#search-unselect-all', 'click');
assert.strictEqual(nodes['#search-selected-count'].textContent, '0');
assert.strictEqual(nodes['#search-download-selected'].disabled, true);
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_image_upload_binding_sets_image_for_submit(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": _DOM_QUERY_ALL,
            "script": """
test.bind();
await fire('#ai-search-button', 'click');
nodes['#search-image-file'].files = [
  {name: '猫.png', size: 1024, __dataUrl: 'data:image/png;base64,AAAA'},
];
await fire('#search-image-file', 'change');
assert.strictEqual(nodes['#search-image-state'].textContent, '已选择：猫.png');
assert.strictEqual(test.getImage(), 'data:image/png;base64,AAAA');
await fire('#search-submit', 'click');
const searchCall = apiCalls.find(call => call[0] === '/api/search');
assert.ok(searchCall, 'search call');
assert.strictEqual(searchCall[2].query, null);
assert.strictEqual(searchCall[2].image, 'data:image/png;base64,AAAA');
assert.strictEqual(searchCall[2].platforms.length, 2);
""",
        },
    )
    assert result.returncode == 0, result.stderr


def test_initialize_calls_bind(tmp_path):
    result = _run_home_script(
        tmp_path,
        {
            "nodes": _nodes_js(),
            "query_all": _DOM_QUERY_ALL,
            "script": """
let bindCalls = 0;
test.setBind(() => { bindCalls += 1; throw new Error('stop-after-bind'); });
let failure = null;
try { await test.initialize(); } catch (error) { failure = error; }
assert.ok(failure, 'initialize should stop after bind');
assert.strictEqual(failure.message, 'stop-after-bind');
assert.strictEqual(bindCalls, 1, 'initialize must call bind');
""",
        },
    )
    assert result.returncode == 0, result.stderr
