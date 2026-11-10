from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


HOME_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "home.js"

# The only source transformation is in memory: expose the real closure functions
# and skip initialization, which would otherwise start polling and access the API.
# A fresh Node process and VM are used for every scenario; no app state is loaded.
_NODE_HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

class Element {
  constructor(tag, classes = '') {
    this.tagName = tag;
    this.parentNode = null;
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.open = false;
    const names = new Set(classes.split(/\s+/).filter(Boolean));
    this.classList = {
      add: (...values) => values.forEach(value => names.add(value)),
      remove: (...values) => values.forEach(value => names.delete(value)),
      contains: value => names.has(value),
      toggle(value, force) {
        const present = force === undefined ? !names.has(value) : Boolean(force);
        if (present) names.add(value); else names.delete(value);
        return present;
      },
    };
  }
  get isConnected() {
    for (let node = this; node; node = node.parentNode) {
      if (node.documentRoot) return true;
    }
    return false;
  }
  append(child) {
    child.remove();
    child.parentNode = this;
    this.children.push(child);
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.children;
    const index = siblings.indexOf(this);
    assert.notEqual(index, -1, 'a child must occur in its parent');
    siblings.splice(index, 1);
    this.parentNode = null;
  }
  replaceWith(replacement) {
    if (replacement === this || !this.parentNode) return;
    const parent = this.parentNode;
    replacement.remove();
    const index = parent.children.indexOf(this);
    assert.notEqual(index, -1, 'the replaced node must occur in its parent');
    parent.children[index] = replacement;
    replacement.parentNode = parent;
    this.parentNode = null;
  }
  querySelectorAll(selector) {
    const found = [];
    const matches = node => selector === '[data-key]'
      ? Object.hasOwn(node.dataset, 'key')
      : selector.startsWith('.')
        ? node.classList.contains(selector.slice(1))
        : node.tagName === selector;
    const visit = node => {
      for (const child of node.children) {
        if (matches(child)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  setAttribute() {}
}

class TaskList extends Element {
  constructor() {
    super('div');
    this.documentRoot = true;
    this.requests = [];
    this.writes = 0;
    this._html = undefined;
  }
  set innerHTML(html) {
    this.writes++;
    // Assigning innerHTML detaches the old tree. A cached wrapper can later be
    // moved back into the new tree by the real render() implementation.
    for (const child of [...this.children]) child.remove();
    const rows = html.matchAll(/<article\b[^>]*\bdata-key="([^"]+)"[^>]*>([\s\S]*?)<\/article>/g);
    for (const [, key, contents] of rows) {
      const row = new Element('article');
      row.dataset.key = key;
      const wrap = new Element('div', 'task-thumb-wrap');
      row.append(wrap);
      this.append(row);
      const match = contents.match(/<img\b[^>]*\bclass="(task-thumb-img(?:\s+[^"]*)?)"[^>]*>/);
      if (!match) continue;
      const tag = match[0];
      const attr = name => {
        const value = tag.match(new RegExp('\\b' + name + '="([^"]*)"'));
        assert.ok(value, `rendered cover is missing ${name}: ${tag}`);
        return value[1];
      };
      const img = new Element('img', match[1]);
      img.src = attr('src');
      img.onload = attr('onload');
      img.onerror = attr('onerror');
      img.complete = false;
      img.naturalWidth = 0;
      wrap.append(img);
      // This records a synthetic cover-src assignment, never an HTTP request.
      // It also records a temporary new image later replaced by a cached wrap.
      this.requests.push({src: img.src, img});
    }
  }
}

function createHarness() {
  const list = new TaskList();
  const elements = new Map();
  const document = {
    activeElement: null,
    querySelector(selector) {
      if (selector === '#task-list') return list;
      if (!elements.has(selector)) {
        const element = new Element('div');
        element.documentRoot = true;
        if (selector === '#list-error') element.hidden = true;
        if (selector === '#empty-state') {
          element.append(new Element('h3'));
          element.append(new Element('p'));
        }
        elements.set(selector, element);
      }
      return elements.get(selector);
    },
    querySelectorAll(selector) {
      assert.ok(['#task-tabs button', '#media-tabs button'].includes(selector),
        `unexpected document selector: ${selector}`);
      return [];
    },
  };
  // Only the home view and simple jobs are used. These unrelated view helpers
  // are inert substitutes; the cover handlers, put(), and render() are real.
  const active = job => ['queued', 'discovering', 'capturing', 'downloading',
    'postprocessing', 'cancelling'].includes(job.status);
  const taskViews = {
    isActive: active,
    needsAction: job => job.status !== 'completed' && job.status !== 'paused' && !active(job),
    files: () => [],
    fileAvailability: () => 'unknown',
    mediaType: () => 'other',
    completionTime: () => '',
    counts(entries) {
      const jobs = entries.map(([, job]) => job);
      return {
        unfinished: jobs.filter(job => job.status !== 'completed').length,
        active: jobs.filter(active).length,
        paused: jobs.filter(job => job.status === 'paused').length,
        failed: jobs.filter(this.needsAction).length,
        completed: jobs.filter(job => job.status === 'completed').length,
      };
    },
    select(entries, options) {
      assert.equal(options.view, 'home', 'the fake only implements the home view');
      return entries;
    },
  };
  const window = {ShiyingTaskViews: taskViews};
  const forbidden = () => { throw new Error('network access is forbidden in this test'); };
  const context = vm.createContext({
    window, document, URL, console, fetch: forbidden,
    EventSource: class { constructor() { forbidden(); } },
    XMLHttpRequest: class { constructor() { forbidden(); } },
  });
  vm.runInContext(HOME_SOURCE, context, {filename: 'home.js', timeout: 5000});
  const api = window.__coverLifecycleTest;
  assert.ok(api, 'home.js did not expose the test closure');

  function fire(img, type, width = 0) {
    assert.ok(img, `cannot fire ${type} without an image`);
    img.complete = true;
    img.naturalWidth = width;
    // Execute the actual inline handler rendered for this particular request,
    // preserving its captured status and its image as `this`.
    const handler = type === 'load' ? img.onload : img.onerror;
    vm.runInContext(`(function() { ${handler} })`, context, {timeout: 5000}).call(img);
  }
  return {
    api, list,
    cover: () => list.querySelector('.task-thumb-img'),
    wrap: () => list.querySelector('.task-thumb-wrap'),
    error: img => fire(img, 'error'), // model an image HTTP 404
    load: (img, width = 640) => fire(img, 'load', width),
  };
}

const ID = 'cover-job';
function job(status = 'downloading', revision = 1, title = 'Original title') {
  return {id: ID, status, revision, title, platform: 'douyin', items: []};
}
function start(h) {
  assert.equal(h.api.put(job()), `j:${ID}`);
  h.api.render();
  const img = h.cover();
  assert.ok(img, 'initial render must create the cover image');
  assert.equal(img.isConnected, true);
  assert.equal(h.list.requests.length, 1);
  assert.equal(img.src, `/api/jobs/${ID}/cover`);
  assert.match(img.onerror, /'downloading'/, 'the handler must capture request-time status');
  return img;
}
function update(h, status, revision, title) {
  h.api.put(job(status, revision, title));
  h.api.render();
}

const scenarios = {
  active_error_suppressed_until_status_changes() {
    const h = createHarness(), original = start(h);
    h.error(original);
    assert.equal(original.isConnected, false, '404 must remove the failed image');
    assert.equal(h.cover(), null);
    assert.equal(h.api.failedCovers.has(ID), true);
    assert.equal(h.list._html, null, 'removing an image must invalidate the HTML cache');
    h.api.render();
    assert.equal(h.cover(), null);
    assert.equal(h.list.requests.length, 1);
    assert.ok(!h.list._html.includes('task-thumb-img'));

    for (let revision = 2; revision <= 4; revision++) {
      const previousHtml = h.list._html;
      update(h, 'downloading', revision, `Active update ${revision}`);
      assert.notEqual(h.list._html, previousHtml, 'exercise a real DOM replacement');
      assert.equal(h.cover(), null, 'same-status updates must keep the 404 suppressed');
      assert.equal(h.list.requests.length, 1, 'same-status updates must not refetch');
      assert.equal(h.api.failedCovers.has(ID), true);
    }
    h.api.put(job('paused', 5, 'Paused'));
    assert.equal(h.api.failedCovers.has(ID), false, 'a status change clears suppression');
    h.api.render();
    assert.equal(h.list.requests.length, 2);
    assert.ok(h.cover());
    assert.equal(h.cover().isConnected, true);
    assert.match(h.cover().onerror, /'paused'/);
  },

  completion_retry_and_completed_error_suppression() {
    const h = createHarness(), active = start(h);
    h.error(active);
    h.api.render();
    h.api.put(job('completed', 2, 'Finished'));
    assert.equal(h.api.failedCovers.has(ID), false);
    h.api.render();
    const completed = h.cover();
    assert.ok(completed, 'completion must retry an earlier active 404');
    assert.notEqual(completed, active);
    assert.equal(completed.isConnected, true);
    assert.equal(h.list.requests.length, 2);
    assert.match(completed.onerror, /'completed'/);

    h.error(completed);
    assert.equal(completed.isConnected, false);
    assert.equal(h.api.failedCovers.has(ID), true);
    assert.equal(h.list._html, null);
    h.api.render();
    assert.equal(h.cover(), null);
    assert.equal(h.list.requests.length, 2, 'completed 404 must not immediately retry');
    const writes = h.list.writes;
    for (let poll = 0; poll < 3; poll++) {
      h.api.put(job('completed', 2, 'Finished'));
      h.api.render();
      assert.equal(h.list.writes, writes, 'unchanged completed HTML should not be replaced');
      assert.equal(h.list.requests.length, 2, 'unchanged completed polls must not refetch');
      assert.equal(h.api.failedCovers.has(ID), true);
    }
    for (let revision = 3; revision <= 5; revision++) {
      update(h, 'completed', revision, `Completed update ${revision}`);
      assert.equal(h.cover(), null, 'even changed completed rows must suppress the cover');
      assert.equal(h.list.requests.length, 2, 'completed 404 must not refetch repeatedly');
      assert.equal(h.api.failedCovers.has(ID), true);
    }
  },

  late_active_error_after_completion_retries_once() {
    const h = createHarness(), active = start(h);
    // Completion arrives while the earlier active request is still attached.
    h.api.put(job('completed', 2, 'Finished'));
    assert.equal(active.isConnected, true);
    h.error(active);
    const completed = h.cover();
    assert.equal(active.isConnected, false);
    assert.ok(completed, 'late active 404 must immediately retry the completed job');
    assert.notEqual(completed, active);
    assert.equal(completed.isConnected, true);
    assert.equal(h.api.failedCovers.has(ID), false);
    assert.equal(h.list.requests.length, 2);
    assert.match(completed.onerror, /'completed'/);
    h.error(completed);
    assert.equal(h.api.failedCovers.has(ID), true);
    assert.equal(h.list.requests.length, 2, 'a completed-request 404 cannot retry itself');
    h.api.render();
    assert.equal(h.cover(), null);
    assert.equal(h.list.requests.length, 2);
  },

  detached_old_error_cannot_invalidate_loaded_replacement() {
    const h = createHarness(), old = start(h);
    update(h, 'completed', 2, 'Finished');
    const current = h.cover();
    assert.notEqual(current, old);
    assert.equal(old.isConnected, false, 'rerender must detach the old image');
    assert.equal(current.isConnected, true);
    h.load(current);
    assert.equal(h.api.loadedCovers.has(ID), true);
    assert.equal(current.classList.contains('loaded'), true);
    h.api.render(); // synchronize generated HTML while retaining the loaded node
    assert.equal(h.cover(), current);
    assert.equal(current.isConnected, true);
    const html = h.list._html, writes = h.list.writes, requests = h.list.requests.length;
    h.error(old);
    assert.equal(h.api.loadedCovers.has(ID), true, 'stale error must not forget the new load');
    assert.equal(h.api.failedCovers.has(ID), false, 'stale error must not poison the new cover');
    assert.equal(h.list._html, html, 'stale error must not invalidate the list cache');
    assert.equal(h.cover(), current);
    assert.equal(current.isConnected, true);
    h.api.render();
    assert.equal(h.list.writes, writes);
    assert.equal(h.list.requests.length, requests);
  },

  detached_old_load_cannot_mark_current_loaded() {
    const h = createHarness(), old = start(h);
    update(h, 'completed', 2, 'Finished');
    const current = h.cover();
    assert.equal(old.isConnected, false);
    assert.equal(current.isConnected, true);
    const html = h.list._html;
    h.load(old);
    assert.equal(h.api.loadedCovers.has(ID), false, 'stale load must not mark the new request loaded');
    assert.equal(old.classList.contains('loaded'), false);
    assert.equal(current.classList.contains('loaded'), false);
    assert.equal(h.list._html, html);
    update(h, 'completed', 3, 'Finished, updated');
    assert.notEqual(h.cover(), current, 'an unloaded current image must not be retained');
    assert.equal(current.isConnected, false);
    assert.equal(h.cover().isConnected, true);
    assert.equal(h.cover().classList.contains('loaded'), false);
  },

  detached_old_load_cannot_clear_newer_failure() {
    const h = createHarness(), old = start(h);
    update(h, 'completed', 2, 'Finished');
    const current = h.cover();
    assert.equal(old.isConnected, false);
    assert.equal(current.isConnected, true);
    h.error(current);
    assert.equal(current.isConnected, false);
    assert.equal(h.api.failedCovers.has(ID), true);
    h.load(old);
    assert.equal(h.api.failedCovers.has(ID), true, 'stale load must not clear a newer 404');
    assert.equal(h.api.loadedCovers.has(ID), false);
    assert.equal(old.classList.contains('loaded'), false);
    h.api.render();
    assert.equal(h.cover(), null);
    assert.equal(h.list.requests.length, 2, 'stale load must not cause another request');
  },

  broken_complete_image_not_retained() { checkBrokenImage(false); },
  broken_loaded_class_image_not_retained() { checkBrokenImage(true); },

  loaded_nonzero_image_retained() {
    const h = createHarness(), original = start(h), wrap = h.wrap();
    h.load(original, 640);
    assert.equal(original.complete, true);
    assert.equal(original.naturalWidth, 640);
    assert.equal(original.classList.contains('loaded'), true);
    assert.equal(h.api.loadedCovers.has(ID), true);
    update(h, 'downloading', 2, 'Updated while downloading');
    assert.equal(h.cover(), original, 'successfully loaded image must be reused');
    assert.equal(h.wrap(), wrap, 'the original wrapper must be moved into the new row');
    assert.equal(wrap.isConnected, true);
    assert.equal(original.isConnected, true);
    const transient = h.list.requests.at(-1).img;
    assert.notEqual(transient, original, 'rerender first constructed a replacement');
    assert.equal(transient.isConnected, false, 'replaced temporary image must be detached');
    h.error(transient);
    assert.equal(h.api.loadedCovers.has(ID), true);
    assert.equal(h.api.failedCovers.has(ID), false);
    assert.equal(h.cover(), original);
    update(h, 'completed', 3, 'Finished');
    assert.equal(h.cover(), original, 'a loaded image also survives a status transition');
    assert.equal(original.isConnected, true);
  },
};

function checkBrokenImage(markLoaded) {
  const h = createHarness(), original = start(h), wrap = h.wrap();
  original.complete = true;
  original.naturalWidth = 0;
  if (markLoaded) original.classList.add('loaded');
  update(h, 'downloading', 2, 'Updated while downloading');
  assert.notEqual(h.cover(), original,
    'complete=true with naturalWidth=0 is broken and must not be retained');
  assert.notEqual(h.wrap(), wrap);
  assert.equal(original.isConnected, false);
  assert.equal(wrap.isConnected, false);
  assert.equal(h.cover().isConnected, true);
  assert.equal(h.list.requests.length, 2);
}

function runScenario(name) {
  assert.ok(Object.hasOwn(scenarios, name), `unknown scenario: ${name}`);
  scenarios[name]();
  process.stdout.write(`ok ${name}`);
}
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "active_error_suppressed_until_status_changes",
        "completion_retry_and_completed_error_suppression",
        "late_active_error_after_completion_retries_once",
        "detached_old_error_cannot_invalidate_loaded_replacement",
        "detached_old_load_cannot_mark_current_loaded",
        "detached_old_load_cannot_clear_newer_failure",
        "broken_complete_image_not_retained",
        "broken_loaded_class_image_not_retained",
        "loaded_nonzero_image_retained",
    ],
)
def test_cover_lifecycle_in_isolated_node_vm(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")

    source = HOME_JS.read_text(encoding="utf-8")
    initialization = "  initialize();\n})();"
    assert source.count(initialization) == 1, "home.js initialization marker changed"
    source = source.replace(
        initialization,
        "  window.__coverLifecycleTest = {state, put, render, failedCovers, loadedCovers};\n})();",
        1,
    )
    script = (
        f"const HOME_SOURCE = {json.dumps(source)};\n"
        + _NODE_HARNESS
        + f"\nrunScenario({json.dumps(scenario)});\n"
    )
    result = subprocess.run(
        [node, "-"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, (
        f"Node scenario {scenario!r} failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout == f"ok {scenario}"
