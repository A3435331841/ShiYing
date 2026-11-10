from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app import main, wechat


def test_open_folder_only_uses_saved_job_paths(monkeypatch, tmp_path):
    opened = []
    def open_folder(path):
        opened.append(path)
        return True
    monkeypatch.setattr(main, "open_folder_foreground", open_folder)
    monkeypatch.setattr(main, "get_config", lambda: SimpleNamespace(download_dir=str(tmp_path / "downloads")))
    monkeypatch.setattr(main.manager, "get_job", lambda _: SimpleNamespace(output_path=tmp_path / "author", items=[]))
    client = TestClient(main.app, base_url="http://localhost")
    assert client.post('/api/folder/open', json={}).status_code == 200
    assert client.post('/api/folder/open', json={'job_id':'test'}).status_code == 200
    assert opened == [str(tmp_path / 'downloads'), str(tmp_path / 'author')]
    prepared = client.post('/api/folder/open', json={'prepare_only':True}).json()
    assert prepared == {'status':'ready', 'path':str(tmp_path / 'downloads')}
    assert len(opened) == 2
    before = list(opened)
    assert client.post('/api/folder/open', json={}, headers={'origin':'https://other.example'}).status_code == 403
    assert client.post('/api/folder/open', json={}, headers={'sec-fetch-site':'cross-site'}).status_code == 403
    assert opened == before


def test_existing_explorer_window_is_reused(monkeypatch, tmp_path):
    import sys
    import types
    import app.desktop as desktop

    class Method:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)

    user32 = SimpleNamespace(
        GetForegroundWindow=Method(lambda: 42), SetForegroundWindow=Method(lambda handle: 1),
        IsIconic=Method(lambda handle: 0), ShowWindow=Method(lambda *args: 1),
    )
    class ComObject:
        def __init__(self, values):
            self.values = values
        def GetType(self):
            return self
        def InvokeMember(self, name, flags, binder, obj, args):
            value = self.values[name]
            return value(*args) if callable(value) else value

    window = ComObject({"LocationURL": tmp_path.as_uri(), "HWND": 42})
    windows = ComObject({"Count": 1, "Item": lambda index: window})
    shell = ComObject({"Windows": lambda: windows})
    class Array:
        def __class_getitem__(cls, item):
            return list
    class Thread:
        def __init__(self, callback):
            self.callback = callback
        def SetApartmentState(self, state):
            pass
        def Start(self):
            self.callback()
        def Join(self):
            pass

    system = types.ModuleType("System")
    system.Activator = SimpleNamespace(CreateInstance=lambda _: shell)
    system.Type = SimpleNamespace(GetTypeFromProgID=lambda _: object())
    system.Array, system.Int32, system.Object = Array, int, object
    reflection = types.ModuleType("System.Reflection")
    reflection.BindingFlags = SimpleNamespace(InvokeMethod=1, GetProperty=2)
    threading = types.ModuleType("System.Threading")
    threading.ApartmentState, threading.Thread, threading.ThreadStart = SimpleNamespace(STA=1), Thread, lambda f: f
    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setitem(sys.modules, "System.Reflection", reflection)
    monkeypatch.setitem(sys.modules, "System.Threading", threading)
    monkeypatch.setattr(desktop.ctypes, "WinDLL", lambda *args, **kwargs: user32, raising=False)
    opened = []
    monkeypatch.setattr(desktop.os, "startfile", lambda *args, **kwargs: opened.append(args), raising=False)
    assert desktop.open_folder_foreground(str(tmp_path))
    assert opened == []


def test_wechat_cancel_and_cookie_clear(monkeypatch, tmp_path):
    import threading
    monkeypatch.setattr(wechat, 'STATE', tmp_path / 'jobs.json')
    monkeypatch.setattr(wechat, 'SECRET', tmp_path / 'cookie.txt')
    monkeypatch.setattr(wechat, 'JOBS', [{'id':'test', 'status':'queued'}])
    event = threading.Event()
    monkeypatch.setattr(wechat, 'CANCELLATIONS', {'test':event})
    client = TestClient(main.app, base_url="http://localhost")
    assert client.post('/api/wechat/jobs/test/cancel').json()['status'] == 'cancelling'
    assert event.is_set()
    assert client.post('/api/wechat/jobs/missing/cancel').status_code == 404
    assert client.post('/api/wechat/cookie', json={'cookie':'example=value'}).status_code == 200
    assert client.get('/api/wechat/status').json()['cookie_configured']
    assert client.post('/api/wechat/cookie', json={'cookie':''}).status_code == 200
    assert not wechat.SECRET.exists()


def test_new_home_is_wired_and_public_downloads_default_to_d_drive():
    client = TestClient(main.app, base_url="http://localhost")
    page = client.get('/').text
    assert 'home.js?v=' in page and 'home.css' in page
    assert '解析并下载' in page and 'data-ui="shiying"' in page
    assert main.AppConfig().download_dir == str(Path(main.PROJECT_ROOT) / 'downloads')
    assert not main.AppConfig().use_chrome_cookies
    assert main.manager.downloader_config.temporary_root == str(main.PROJECT_ROOT / 'cache')


def test_completed_file_size_comes_from_final_files(tmp_path):
    from app.models import DownloadJob, DownloadItem, Platform, SourceKind, ItemStatus
    final = tmp_path / 'merged.mp4'
    final.write_bytes(b'video-plus-audio')
    job = DownloadJob(id='test', source_url='https://www.bilibili.com/video/BV1GJ411x7h7', platform=Platform.BILIBILI, source_kind=SourceKind.ITEM, output_root=str(tmp_path), items=[DownloadItem(id='item', source_url='https://www.bilibili.com/video/BV1GJ411x7h7', status=ItemStatus.COMPLETED, output_paths=[str(final)])])
    assert main._public_job(job).items[0].metadata['output_size_bytes'] == len(b'video-plus-audio')
    assert 'output_size_bytes' not in job.items[0].metadata
