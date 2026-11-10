import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_task_views_module_exists_and_exports_pure_functions():
    views_path = ROOT / "app/static/task-views.js"
    assert views_path.exists()
    content = views_path.read_text("utf-8")
    assert "isActive" in content
    assert "needsAction" in content
    assert "mediaType" in content
    assert "counts" in content
    assert "select" in content


def test_task_views_counts_and_partition(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    views_source = (ROOT / "app/static/task-views.js").read_text("utf-8")
    script = """
const vm = require('vm'), assert = require('assert');
const window = {};
vm.runInNewContext(VIEWS_CODE, {window, console});
const { counts, select, mediaType } = window.ShiyingTaskViews;

const entries = [
  ['j:1', { id: '1', status: 'downloading', platform: 'douyin', items: [{ media_type: 'video' }] }],
  ['j:2', { id: '2', status: 'paused', platform: 'douyin', items: [{ media_type: 'video' }] }],
  ['j:3', { id: '3', status: 'failed', platform: 'bilibili', items: [{ media_type: 'video' }] }],
  ['j:4', { id: '4', status: 'completed', platform: 'xiaohongshu', items: [{ media_type: 'image' }] }],
  ['j:5', { id: '5', status: 'completed', platform: 'bilibili', items: [{ media_type: 'video' }] }],
  ['j:6', { id: '6', status: 'partial', platform: 'kuaishou', items: [{ media_type: 'image' }] }]
];

const c = counts(entries);
assert.strictEqual(c.unfinished, 4); // downloading, paused, failed, partial
assert.strictEqual(c.active, 1);
assert.strictEqual(c.paused, 1);
assert.strictEqual(c.failed, 2); // failed, partial
assert.strictEqual(c.completed, 2);

// 'tasks' view must never include completed jobs
const tasksAll = select(entries, { view: 'tasks', filter: 'all' });
assert.strictEqual(tasksAll.length, 4);
assert(tasksAll.every(([, j]) => j.status !== 'completed'));

// 'completed' view must only include completed jobs
const completedAll = select(entries, { view: 'completed', type: 'all', platform: 'all' });
assert.strictEqual(completedAll.length, 2);
assert(completedAll.every(([, j]) => j.status === 'completed'));

// Filter by media type
const imagesOnly = select(entries, { view: 'completed', type: 'image', platform: 'all' });
assert.strictEqual(imagesOnly.length, 1);
assert.strictEqual(imagesOnly[0][0], 'j:4');

// Filter by platform
const biliOnly = select(entries, { view: 'completed', type: 'all', platform: 'bilibili' });
assert.strictEqual(biliOnly.length, 1);
assert.strictEqual(biliOnly[0][0], 'j:5');
"""
    script = script.replace("VIEWS_CODE", json.dumps(views_source, ensure_ascii=False))
    path = tmp_path / "test-views.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr


def test_index_html_has_redesigned_controls():
    html = (ROOT / "app/static/index.html").read_text("utf-8")
    assert 'id="tasks-section"' in html
    assert 'data-view="home"' in html
    assert 'id="task-filters"' in html
    assert 'id="task-tabs"' in html
    assert 'id="media-tabs"' in html
    assert 'id="completed-controls"' in html
    assert 'id="completed-platform"' in html
    assert 'id="completed-sort"' in html
    assert 'id="completed-batch"' in html
    assert 'id="manage-completed"' in html
    assert 'task-views.js' in html
    # Ensure legacy button clear-completed is replaced by batch management
    assert 'id="clear-completed"' not in html
