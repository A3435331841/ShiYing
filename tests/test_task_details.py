from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import desktop, main, wechat
from app.models import DownloadItem, DownloadJob, ItemStatus, JobStatus, Platform, SourceKind


ROOT = Path(__file__).resolve().parents[1]


def saved_job(tmp_path, *, paths=None):
    media = tmp_path / "作品.mp4"
    media.write_bytes(b"saved-video")
    job = DownloadJob(
        id="details-job", source_url="https://www.bilibili.com/video/BV1GJ411x7h7",
        platform=Platform.BILIBILI, source_kind=SourceKind.ITEM,
        output_root=str(tmp_path), status=JobStatus.COMPLETED,
        items=[DownloadItem(
            id="item", source_url="https://www.bilibili.com/video/BV1GJ411x7h7",
            title="已保存作品", status=ItemStatus.COMPLETED,
            output_paths=paths if paths is not None else [str(media)],
        )],
    )
    job.refresh_counts()
    return job


def test_public_files_are_live_facts_not_persisted_metadata(tmp_path):
    job = saved_job(tmp_path)
    job.items[0].metadata["output_size_bytes"] = 999999
    facts = main._public_job(job).items[0].metadata
    assert facts["output_files"] == [{
        "index": 0, "name": "作品.mp4", "path": job.items[0].output_paths[0],
        "exists": True, "size_bytes": len(b"saved-video"), "can_open": True,
    }]
    assert facts["output_size_bytes"] == len(b"saved-video")
    assert "output_files" not in job.items[0].metadata
    Path(job.items[0].output_paths[0]).unlink()
    missing = main._public_job(job).items[0].metadata
    assert missing["output_files"][0]["exists"] is False
    assert missing["output_files"][0]["can_open"] is False
    assert "output_size_bytes" not in missing
    assert job.items[0].metadata["output_size_bytes"] == 999999


def test_partial_item_keeps_available_outputs(tmp_path):
    job = saved_job(tmp_path)
    job.items[0].status = ItemStatus.FAILED
    assert main._public_job(job).items[0].metadata["output_files"][0]["can_open"]


def test_file_facts_distinguish_unreadable_from_missing(monkeypatch, tmp_path):
    target = tmp_path / "offline.mp4"
    original = Path.stat

    def denied(self, *args, **kwargs):
        if self == target:
            raise PermissionError("offline volume")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    facts = desktop.saved_file_facts([str(target), str(tmp_path / "missing.mp4")])
    assert facts[0]["exists"] is None
    assert facts[1]["exists"] is False
    assert not facts[0]["can_open"]


def test_file_open_uses_only_saved_job_item_and_original_index(app_manager, monkeypatch, tmp_path):
    job = saved_job(tmp_path)
    second = tmp_path / "第二个文件.webm"
    second.write_bytes(b"video")
    job.items[0].output_paths.append(str(second))
    app_manager._jobs[job.id] = job
    opened = []
    monkeypatch.setattr(main, "open_saved_file", lambda path, **options: opened.append((path, options)))
    client = TestClient(main.app, base_url="http://localhost")
    request = {"job_id": job.id, "item_id": "item", "file_index": 1}
    assert client.post("/api/files/open", json=request).json()["status"] == "opened"
    assert client.post("/api/files/open", json={**request, "reveal": True}).json()["status"] == "revealed"
    assert opened == [(str(second), {"reveal": False}), (str(second), {"reveal": True})]
    assert client.post("/api/files/open", json={**request, "path": "C:/Windows/notepad.exe"}).status_code == 422
    assert client.post("/api/files/open", json={**request, "item_id": "another-job-item"}).status_code == 404
    for invalid in (-1, 9, True, "0"):
        assert client.post("/api/files/open", json={**request, "file_index": invalid}).status_code in (404, 422)
    assert len(opened) == 2


def test_file_open_rejects_cross_site_requests(app_manager, monkeypatch, tmp_path):
    job = saved_job(tmp_path)
    app_manager._jobs[job.id] = job
    monkeypatch.setattr(main, "open_saved_file", lambda *a, **k: pytest.fail("must not open"))
    client = TestClient(main.app, base_url="http://localhost")
    body = {"job_id": job.id, "item_id": "item"}
    assert client.post("/api/files/open", json=body, headers={"origin": "https://other.example"}).status_code == 403
    assert client.post("/api/files/open", json=body, headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_missing_file_has_actionable_error(app_manager, tmp_path):
    job = saved_job(tmp_path)
    Path(job.items[0].output_paths[0]).unlink()
    app_manager._jobs[job.id] = job
    response = TestClient(main.app, base_url="http://localhost").post(
        "/api/files/open", json={"job_id": job.id, "item_id": "item"},
    )
    assert response.status_code == 404
    assert "移动或删除" in response.json()["detail"]
    assert job.id in app_manager._jobs


@pytest.mark.parametrize("suffix", [".exe", ".bat", ".ps1", ".cmd", ".html", ".url", ".lnk", ".svg"])
def test_downloaded_non_media_cannot_be_executed(monkeypatch, tmp_path, suffix):
    target = tmp_path / ("download" + suffix)
    target.write_text("untrusted", encoding="utf-8")
    monkeypatch.setattr(desktop.os, "startfile", lambda *a, **k: pytest.fail("must not execute"), raising=False)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: pytest.fail("must not execute"))
    assert not desktop.saved_file_facts([str(target)])[0]["can_open"]
    with pytest.raises(ValueError, match="不能直接打开"):
        desktop.open_saved_file(str(target))


def test_open_media_and_reveal_pass_paths_as_arguments(monkeypatch, tmp_path):
    target = tmp_path / "视频 空格 & 字符.mp4"
    target.write_bytes(b"video")
    calls = []
    monkeypatch.setattr(desktop.os, "startfile", lambda path: calls.append(path), raising=False)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args: calls.append(args))
    desktop.open_saved_file(str(target))
    desktop.open_saved_file(str(target), reveal=True)
    assert len(calls) == 2
    if desktop.sys.platform == "win32":
        assert calls == [str(target.resolve()), ["explorer.exe", "/select,", str(target.resolve())]]
    elif desktop.sys.platform == "darwin":
        assert calls == [["open", str(target.resolve())], ["open", "-R", str(target.resolve())]]
    else:
        assert calls == [["xdg-open", str(target.resolve())], ["xdg-open", str(target.parent.resolve())]]


def test_legacy_wechat_uses_same_file_facts_and_checks_scope(monkeypatch, tmp_path):
    target = tmp_path / "旧视频号.mp4"
    target.write_bytes(b"video")
    monkeypatch.setattr(wechat, "JOBS", [{"id": "old", "path": str(target), "output_root": str(tmp_path)}])
    opened = []
    monkeypatch.setattr(main, "open_saved_file", lambda path, **kw: opened.append(path))
    assert wechat.list_jobs()[0]["output_files"][0]["exists"] is True
    client = TestClient(main.app, base_url="http://localhost")
    assert client.post("/api/files/open", json={"job_id": "old", "wechat": True}).status_code == 200
    assert opened == [str(target.resolve())]
    wechat.JOBS[0]["output_root"] = str(tmp_path / "another-directory")
    assert client.post("/api/files/open", json={"job_id": "old", "wechat": True}).status_code == 400
    assert len(opened) == 1


def test_details_render_scope_progress_and_actions(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    home = (ROOT / "app/static/home.js").read_text("utf-8").replace("  initialize();", """
  window.detailsTest = { state, detailItems, detailFiles, detailSummary, detailQuality,
    detailProgress, detailUrl, renderDetails, detailAct,
    setApi(value) { api = value; }, setToast(value) { toast = value; } };
""")
    task_views = (ROOT / "app/static/task-views.js").read_text("utf-8") if (ROOT / "app/static/task-views.js").exists() else ""
    script = r"""
const vm = require('vm'), assert = require('assert');
const node = () => ({innerHTML:'', dataset:{}, querySelectorAll:() => [], contains:() => false, scrollTop:0});
const nodes = {'#details-content':node(), '#details-actions':node(), '#details-dialog':{dataset:{}, close(){this.closed=true;}}};
const window = {}, document = {querySelector: selector => nodes[selector], activeElement:{}};
const context = vm.createContext({window, document, URL, console});
vm.runInContext(TASK_VIEWS, context);
vm.runInContext(HOME, context);
const test = window.detailsTest;
const file = {index:0, name:'a.mp4', path:'D:\\saved\\a.mp4', exists:true, can_open:true, size_bytes:1024};
const item = {id:'chosen', title:'所选作品 <script>', media_type:'video', status:'completed', resolution:'1920x1080', selected_format:'30080+30280', output_paths:[file.path], metadata:{output_files:[file]}};
const job = {id:'job', status:'completed', source_kind:'playlist', source_url:'https://space.bilibili.com/1/lists/2', platform:'bilibili', author:'作者', selected_item_ids:['chosen'], total_items:1, items:[item, {id:'unselected', title:'不应出现在详情', status:'skipped'}]};
test.state.detailId='j:job'; test.state.jobs.set('j:job',job); test.renderDetails();
let html = nodes['#details-content'].innerHTML;
const visible = html.split('<details class="detail-technical"')[0];
assert(visible.includes('已完成 1 / 1 项'));
assert(visible.includes('本次仅下载选中的 1 项'));
assert(visible.includes('1080p') && visible.includes('MP4'));
assert(!visible.includes('30080+30280') && html.includes('提取器格式编号：30080+30280'));
assert(!html.includes('不应出现在详情'));
assert(visible.includes('&lt;script&gt;') && !visible.includes('<script>'));
assert(nodes['#details-actions'].innerHTML.includes('打开下载文件夹'));
assert(html.includes('rel="noopener noreferrer"'));
assert.strictEqual(test.detailUrl('javascript:alert(1)'), '');
assert.strictEqual(test.detailUrl('file:///C:/secret.txt'), '');
assert.strictEqual(test.detailQuality({resolution:'704x1280'}), '704 × 1280');
job.status='downloading'; item.status='downloading'; item.progress={downloaded_bytes:512};
let progress = test.detailProgress(job, [item]);
assert(!progress.includes('aria-valuenow') && !progress.includes('0%'));
item.status='postprocessing'; item.progress={percent:100};
progress = test.detailProgress(job,[item]);
assert(progress.includes('合并完成后才算下载完成'));
assert(!progress.includes('100%') && !progress.includes('aria-valuenow'));
job.status='partial'; item.status='failed'; item.error='网络中断'; job.discovery_complete=false;
test.renderDetails(); html=nodes['#details-content'].innerHTML;
assert(html.includes('失败 1 项') && html.includes('不能视为整个合集下载完整'));
assert(html.includes('重试此项') && nodes['#details-actions'].innerHTML.includes('重试未完成项'));
// 确保不会同时在 detail-notice 中重复渲染失败报错
assert(!html.includes('class="detail-notice">网络中断<'), '详情弹窗不应在黄色警告框中重复渲染失败错误');
file.exists=false; file.can_open=false; file.size_bytes=null;
job.status='completed'; item.status='completed'; delete item.error;
test.renderDetails(); html=nodes['#details-content'].innerHTML;
assert(html.includes('文件已移动或删除') && html.includes('记录不会因此删除'));
assert(!nodes['#details-actions'].innerHTML.includes('打开下载文件夹'));
const summary = test.detailSummary(job);
assert.strictEqual(summary.missing,1); assert.strictEqual(summary.available,0);
const calls=[], notices=[];
test.setApi(async (...args) => {calls.push(args); return {status:'opened'};});
test.setToast(text => notices.push(text));
file.exists=true; file.can_open=true;
const button={disabled:false,isConnected:true,dataset:{detailAction:'open',itemId:'chosen',fileIndex:'0'}};
(async () => {
  await test.detailAct({target:{closest:()=>button}});
  assert.strictEqual(calls[0][0],'/api/files/open');
  assert.strictEqual(calls[0][2].item_id,'chosen');
  assert.strictEqual(calls[0][2].file_index,0);
  assert.strictEqual(calls[0][2].reveal,false);
  assert(!Object.hasOwn(calls[0][2],'path'));
  assert(notices[0].includes('系统默认应用'));
  assert.strictEqual(button.disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    script = script.replace("TASK_VIEWS", json.dumps(task_views, ensure_ascii=False), 1).replace("HOME", json.dumps(home, ensure_ascii=False), 1)
    path = tmp_path / "details-test.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
