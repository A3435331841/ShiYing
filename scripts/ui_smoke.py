"""Native WebView2 integration check; uses the same HTML, JS and local APIs as the app.

Run after starting the local service. Downloads one public Bilibili sample to the
configured directory. No browser profile or login credentials are used.
"""
import json
import sys
import time
import traceback
from pathlib import Path
from urllib.request import urlopen

import webview

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launch_shiying import DesktopAPI

RESULT = ROOT / 'data' / ('folder-focus.json' if '--folder-focus' in sys.argv else 'ui-smoke.json')
checks = []
outcome = {'passed': False, 'checks': checks}


def api(path):
    with urlopen('http://127.0.0.1:8766' + path, timeout=5) as response:
        return json.load(response)


def wait_for(js, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(js)
        if value:
            return value
        time.sleep(.25)
    raise AssertionError('Timed out waiting for: ' + js)


def check(condition, label):
    assert condition, label
    checks.append(label)


def run():
    try:
        window.events.loaded.wait(20)
        wait_for("document.querySelector('#connection').classList.contains('online')")
        if '--folder-focus' in sys.argv:
            window.evaluate_js("window.__folderFeedback=[]; new MutationObserver(records=>records.forEach(r=>r.addedNodes.forEach(n=>window.__folderFeedback.push(n.textContent)))).observe(document.querySelector('#toast-region'),{childList:true})")
            # Use actual bridge calls and the shared click handlers. Success text
            # is emitted only after GetForegroundWindow matches the target HWND.
            for selector in ('[data-open-folder]', '.task-row .operations [data-action=folder]'):
                window.show()
                window.evaluate_js("document.querySelector('#toast-region').replaceChildren()")
                window.evaluate_js('document.querySelector(' + json.dumps(selector) + ').click()')
                wait_for("document.querySelector('#toast-region').textContent.includes('宸叉樉绀哄湪鍓嶅彴')", 12)
                checks.append(selector + ' activated matching Explorer window')
            outcome['passed'] = True
            return
        check(window.evaluate_js("document.querySelector('#save-path').textContent").startswith('D:\\Shiying'), 'home ready with D drive output')
        window.evaluate_js("window.__qaErrors=[];window.addEventListener('error',e=>window.__qaErrors.push(e.message));window.addEventListener('unhandledrejection',e=>window.__qaErrors.push(String(e.reason)));")
        window.evaluate_js("document.querySelector('[data-open-settings]').click()")
        wait_for("document.querySelector('#settings-dialog').open")
        check(window.evaluate_js("document.querySelector('#choose-directory').hidden === false"), 'native folder chooser exposed')
        check(window.evaluate_js("document.querySelector('#use-cookies').checked === false"), 'public mode avoids Chrome cookie lock')
        window.evaluate_js("document.querySelector('#settings-form').requestSubmit()")
        wait_for("!document.querySelector('#settings-dialog').open")
        checks.append('settings saved through actual form')
        window.evaluate_js("document.querySelector('#theme-toggle').click()")
        check(window.evaluate_js("document.documentElement.dataset.theme === 'dark'"), 'dark theme toggle')
        window.evaluate_js("document.querySelector('#theme-toggle').click()")
        window.evaluate_js("document.querySelector('[data-view=completed]').click()")
        check(window.evaluate_js("document.querySelector('#composer').hidden"), 'completed task navigation')
        window.evaluate_js("document.querySelector('[data-view=home]').click()")
        if len(sys.argv) > 1:
            job = api('/api/jobs/' + sys.argv[1])
        else:
            window.evaluate_js("document.querySelector('#share-input').value='鍒嗕韩 https://www.bilibili.com/video/BV1GJ411x7h7';document.querySelector('#link-form').requestSubmit()")
            wait_for("document.querySelector('#share-input').value === ''")
            job = next(j for j in api('/api/jobs') if j['platform'] == 'bilibili')
        job_id = job['id']
        outcome['job_id'] = job_id
        check(job['output_root'].startswith('D:\\Shiying'), 'real job uses D output')
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            job = api('/api/jobs/' + job_id)
            if job['status'] in ('completed', 'failed', 'needs_auth', 'partial'):
                break
            time.sleep(1)
        outcome['download_status'] = job['status']
        outcome['download_error'] = job.get('error') or job.get('auth_message')
        check(job['status'] == 'completed', 'Bilibili actual download completed')
        paths = [Path(p) for i in job['items'] for p in i['output_paths']]
        check(bool(paths) and all(p.is_file() and p.stat().st_size > 0 for p in paths), 'downloaded media files exist')
        outcome['files'] = [{'path':str(p),'bytes':p.stat().st_size} for p in paths]
        wait_for("[...document.querySelectorAll('.task-row')].some(r=>r.dataset.key==='j:" + job_id + "'&&r.textContent.includes('已完成'))")
        final_size = sum(p.stat().st_size for p in paths)
        expected_size = f'{final_size / 1024 / 1024:.1f} MB'
        check(window.evaluate_js("document.querySelector('[data-key=\"j:" + job_id + "\"] .size-cell').textContent") == expected_size, 'completed row displays actual merged file size')
        window.evaluate_js("document.querySelector('[data-key=\"j:" + job_id + "\"] .task-title').click()")
        check(window.evaluate_js("document.querySelector('#details-dialog').open && document.querySelector('#details-content').textContent.includes('Shiying')"), 'completed details show saved files')
        window.evaluate_js("document.querySelector('#details-dialog').close()")
        check(window.evaluate_js("window.__qaErrors.length === 0"), 'no runtime JavaScript errors')
        outcome['passed'] = True
    except Exception:
        outcome['error'] = traceback.format_exc()
        if '--folder-focus' in sys.argv:
            outcome['feedback'] = window.evaluate_js('window.__folderFeedback')
    finally:
        RESULT.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
        window.destroy()


desktop_api = DesktopAPI()
deadline = time.monotonic() + 20
while True:
    try:
        api('/api/health')
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(.25)
window = webview.create_window('鎷惧奖鐣岄潰楠屾敹', 'http://127.0.0.1:8766', hidden='--folder-focus' not in sys.argv, js_api=desktop_api, width=1380, height=900)
desktop_api._window = window
webview.start(run, gui='edgechromium', storage_path=str(ROOT / 'data/ui-test-webview'))
print(RESULT.read_text(encoding='utf-8'))
sys.exit(0 if outcome['passed'] else 1)
