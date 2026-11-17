import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_batch_links_route_independently_and_stop_on_uncertain_response(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    marker = "  initialize();"
    assert marker in source
    source = source.replace(marker, """
  window.batchTest = {
    extractShareLinks, submit, state,
    setApi(value) { api = value; },
    setHooks() { put = () => {}; render = () => {}; toast = () => {}; connection = () => {}; }
  };
""")
    script = """
const vm = require('vm');
const assert = require('assert');
const source = SOURCE;
const input = {value: ''}, message = {textContent: ''}, button = {textContent: ''};
const nodes = {'#share-input': input, '#form-message': message, '#submit-button span': button};
const window = {};
vm.runInNewContext(source, {window, document: {querySelector: selector => nodes[selector]}, URL, console});
const test = window.batchTest;
test.state.ready = true;
test.setHooks();
const calls = [];
test.setApi(async (path, method, body) => {
  calls.push([path, body.url]);
  if (body.url.includes('invalid.example')) {
    const error = new Error('不支持的链接'); error.httpStatus = 422; throw error;
  }
  if (body.url.includes('uncertain.example')) throw new Error('connection lost');
  return {id: String(calls.length)};
});
input.value = [
  '分享 https://v.douyin.com/abc/。',
  '[视频号](https://weixin.qq.com/sph/target)',
  '重复 https://v.douyin.com/abc/',
  'https://invalid.example/item',
  'https://uncertain.example/item',
  'https://www.bilibili.com/video/BV1xx411c7mD',
].join('\\n');
(async () => {
  await test.submit({preventDefault() {}});
  assert.deepStrictEqual(calls.map(x => x[0]), ['/api/jobs','/api/jobs','/api/jobs','/api/jobs']);
  assert.strictEqual(calls.filter(x => x[1] === 'https://v.douyin.com/abc/').length, 1);
  assert(input.value.includes('https://invalid.example/item'));
  assert(input.value.includes('https://uncertain.example/item'));
  assert(input.value.includes('https://www.bilibili.com/video/BV1xx411c7mD'));
  assert(!input.value.includes('https://v.douyin.com/abc/'));
  assert(message.textContent.includes('请先刷新任务列表'));
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("SOURCE", json.dumps(source, ensure_ascii=False), 1)
    path = tmp_path / "batch-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr


def test_record_removal_requires_confirmation_and_ignores_stale_events(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    views_source = (Path(__file__).resolve().parents[1] / "app/static/task-views.js").read_text("utf-8")
    source = source.replace("  initialize();", """
  window.removalTest = {
    state, put, requestRemoval, confirmRemoval,
    setApi(value) { api = value; },
    setHooks() { render = () => {}; toast = () => {}; }
  };
""")
    script = """
const vm = require('vm'), assert = require('assert');
const nodes = {
  '#remove-description': {textContent: ''},
  '#remove-message': {textContent: ''},
  '#remove-dialog': {open: false, showModal() {this.open=true;}, close() {this.open=false;}},
  '#confirm-remove': {disabled: false},
};
const window = {};
vm.runInNewContext(VIEWS_CODE, {window, console});
vm.runInNewContext(SOURCE_CODE, {window, document: {querySelector: selector => nodes[selector]}, URL, console});
const test = window.removalTest, calls = [];
test.setHooks();
test.setApi(async (path, method) => { calls.push([path, method]); return {deleted:true}; });
test.put({id:'done', status:'completed', revision: 1});
test.put({id:'running', status:'downloading', revision: 1});
test.requestRemoval(['j:done','j:running']);
assert(nodes['#remove-dialog'].open);
assert(nodes['#remove-description'].textContent.includes('1 条'));
assert(nodes['#remove-description'].textContent.includes('文件和文件夹会保留'));
assert.strictEqual(calls.length, 0);
(async () => {
  await test.confirmRemoval();
  assert.deepStrictEqual(calls, [['/api/jobs/done','DELETE']]);
  assert(!test.state.jobs.has('j:done'));
  assert(test.state.jobs.has('j:running'));
  test.put({id:'done', status:'completed', revision: 2});
  assert(!test.state.jobs.has('j:done'));
  assert(!nodes['#remove-dialog'].open);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    script = script.replace("VIEWS_CODE", json.dumps(views_source, ensure_ascii=False)).replace("SOURCE_CODE", json.dumps(source, ensure_ascii=False))
    path = tmp_path / "removal-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr


def test_link_insights_counts_only_urls_not_prose(tmp_path):
    """无法识别只数 URL 级问题；分享文案里的普通中文片段不计入警示。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    marker = "  initialize();"
    assert marker in source
    source = source.replace(marker, """
  window.insightsTest = {
    updateLinkInsights,
    nodes: {input: null, box: null, invalidBox: null},
  };
""")
    script = """
const vm = require('vm');
const assert = require('assert');
const source = SOURCE;
const input = {value: ''}, box = {hidden: true, innerHTML: ''}, invalidBox = {hidden: true, textContent: ''};
const nodes = {'#share-input': input, '#link-insights': box, '#link-invalid-list': invalidBox};
const window = {};
vm.runInNewContext(source, {window, document: {querySelector: selector => nodes[selector]}, URL, console});
const test = window.insightsTest;

// 支持平台的链接 + 整段中文分享文案：不得出现"无法识别"警示。
input.value = '【B站】某视频 https://www.bilibili.com/video/BV1GJ411x7h7 快来UP主的空间看看';
test.updateLinkInsights();
assert(!box.hidden);
assert(box.innerHTML.includes('识别到 <strong>1</strong> 条链接'));
assert(box.innerHTML.includes('无法识别 <strong>0</strong>'));
assert(invalidBox.hidden);

// 不支持的平台 URL：计入无法识别并列出样例。
input.value = '看看这个 https://weibo.com/x/123';
test.updateLinkInsights();
assert(box.innerHTML.includes('无法识别 <strong>1</strong>'));
assert(!invalidBox.hidden);
assert(invalidBox.textContent.includes('https://weibo.com/x/123'));

// 纯中文文案（无任何链接）：只显示识别到 0 条，不误报无法识别。
input.value = '这只是一段没有链接的文字';
test.updateLinkInsights();
assert(box.innerHTML.includes('识别到 <strong>0</strong> 条链接'));
assert(box.innerHTML.includes('无法识别 <strong>0</strong>'));

// 畸形链接同样计入无法识别。
input.value = 'https://[bad-url';
test.updateLinkInsights();
assert(box.innerHTML.includes('无法识别 <strong>1</strong>'));
""".replace("SOURCE", json.dumps(source, ensure_ascii=False), 1)
    path = tmp_path / "insights-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr


def test_full_exit_flow_two_step_confirm_bridge_and_fallback(tmp_path):
    """完全退出：两步确认；有桌面桥走 quit_app，无桥给出停止脚本指引。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    marker = "  initialize();"
    assert marker in source
    source = source.replace(marker, """
  window.exitTest = {
    fullExit,
    setHooks() { toast = () => {}; },
  };
""")
    script = """
const vm = require('vm');
const assert = require('assert');
const source = SOURCE;
const button = {textContent: '完全退出', disabled: false, isConnected: true};
const stateEl = {hidden: true, textContent: ''};
const nodes = {'#full-exit': button, '#exit-state': stateEl};
const window = {setTimeout: () => 0, pywebview: {}};
const quitCalls = [];
vm.runInNewContext(source, {window, document: {querySelector: s => nodes[s]}, URL, console, Date});
const test = window.exitTest;
test.setHooks();

// 第一次点击只进入确认态，不触发退出。
test.fullExit();
assert.strictEqual(button.textContent, '确认完全退出？');
assert.strictEqual(quitCalls.length, 0);

// 无 pywebview 桥（浏览器直连）：确认后给出停止脚本指引，不抛错。
test.fullExit();
assert.strictEqual(quitCalls.length, 0);
assert(stateEl.textContent.includes('停止拾影.bat'));
assert(stateEl.textContent.includes('scripts/unix/stop.sh'));
assert.strictEqual(button.textContent, '完全退出');
assert.strictEqual(button.disabled, false);

// 有桥且成功：调用一次 quit_app 并提示服务已停止。
window.pywebview = {api: {quit_app: async () => { quitCalls.push(1); return {ok: true}; }}};
test.fullExit();
assert.strictEqual(button.textContent, '确认完全退出？');
(async () => {
  await test.fullExit();
  assert.strictEqual(quitCalls.length, 1);
  assert(stateEl.textContent.includes('本机服务已停止'));

  // 桥拒绝（StopRefusedError 语义）：展示后端给出的原因并恢复按钮。
  window.pywebview = {api: {quit_app: async () => ({ok: false, message: '无法确认本机服务身份，已取消退出'})}};
  test.fullExit();
  assert.strictEqual(button.textContent, '确认完全退出？');
  await test.fullExit();
  assert(stateEl.textContent.includes('无法确认本机服务身份'));
  assert.strictEqual(button.disabled, false);
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("SOURCE", json.dumps(source, ensure_ascii=False), 1)
    path = tmp_path / "exit-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr


def test_capturing_status_has_label_and_cancel_path_is_wired():
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    views_source = (Path(__file__).resolve().parents[1] / "app/static/task-views.js").read_text("utf-8")
    assert "capturing: '等待微信播放'" in source
    assert "'capturing', 'downloading', 'postprocessing', 'cancelling'" in views_source or "'capturing','downloading','cancelling'" in source
    assert 'data-action="cancel"' in source
    assert "`/api/wechat/jobs/${job.id}/cancel`" in source
