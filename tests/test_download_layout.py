from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.downloader import DiscoveryResult, DownloadOutcome, DownloaderConfig
from app.models import DownloadItem, MediaType
from app.task_manager import DownloadManager


@pytest.mark.parametrize('images, count', [(False, 1), (True, 1), (True, 2)])
def test_organized_download_uses_actual_destination_after_discovery(tmp_path, monkeypatch, images, count):
    url = 'https://www.youtube.com/watch?v=abcdefghijk'

    class Engine:
        def discover(self, *args, **kwargs):
            return DiscoveryResult(author='Single Author', items=[DownloadItem(
                id=f'work-{i}', media_id=f'abcdefghijk{i}', source_url=url,
                title=f'A short work {i}', media_type=MediaType.IMAGE if images else MediaType.VIDEO,
            ) for i in range(count)])

        def download_item(self, item, platform, output_dir, **kwargs):
            assert item.metadata["_compact_asset_names"] is images
            folder = Path(output_dir)
            folder.mkdir(parents=True, exist_ok=True)
            paths = [folder / name for name in (['01.png', '02.png'] if images else ['video.mp4'])]
            for path in paths:
                path.write_bytes(b'verified test output')
            return DownloadOutcome(output_paths=[str(p) for p in paths])

    manager = DownloadManager(state_dir=tmp_path/'state', default_output_root=tmp_path/'downloads',
                              organized_output=True, downloader_config=DownloaderConfig(cookie_browser=None))
    monkeypatch.setattr(manager, '_engine_for_job', lambda *args: Engine())
    try:
        created = manager.create_job(url, cookie_browser=None)
        manager._futures[created.id].result(timeout=5)
        job = manager.get_job(created.id)
        assert job.status.value == 'completed', job.error
        folder = Path(job.output_dir)
        author_folder = tmp_path/'downloads'/'YouTube'/'Single Author'
        assert folder.parent == author_folder if images and count == 1 else folder == author_folder
        assert not list((tmp_path/'downloads').glob('*.mp4'))
        parents = set()
        for item in job.items:
            for path in item.output_paths:
                assert Path(path).is_file()
                parent = Path(path).parent
                assert parent.parent == author_folder if images else parent == author_folder
                parents.add(parent)
        assert len(parents) == count
        assert not (tmp_path/'downloads'/'Single Author').exists()
    finally:
        manager.shutdown(wait=True)


def test_task_folder_uses_final_file_parent_instead_of_stale_saved_folder(monkeypatch, tmp_path):
    actual = tmp_path/'downloads'/'video.mp4'
    monkeypatch.setattr(main.manager, 'get_job', lambda _: SimpleNamespace(
        output_path=tmp_path/'old-folder', items=[SimpleNamespace(output_paths=[str(actual)])]))
    result = TestClient(main.app, base_url="http://localhost").post('/api/folder/open', json={'job_id':'saved', 'prepare_only':True})
    assert result.status_code == 200
    assert result.json()['path'] == str(actual.parent)
    assert not (tmp_path/'old-folder').exists()
