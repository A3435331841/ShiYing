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


def test_capturing_status_has_label_and_cancel_path_is_wired():
    source = (Path(__file__).resolve().parents[1] / "app/static/home.js").read_text("utf-8")
    views_source = (Path(__file__).resolve().parents[1] / "app/static/task-views.js").read_text("utf-8")
    assert "capturing: '等待微信播放'" in source
    assert "'capturing', 'downloading', 'postprocessing', 'cancelling'" in views_source or "'capturing','downloading','cancelling'" in source
    assert 'data-action="cancel"' in source
    assert "`/api/wechat/jobs/${job.id}/cancel`" in source
