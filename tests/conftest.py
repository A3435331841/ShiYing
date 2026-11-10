from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app_manager(tmp_path, monkeypatch):
    """Run the real FastAPI app against a throwaway job store.

    Without this, tests that POST /api/jobs write into the developer's real
    data/state and their fake sample links show up in the product's task list.
    """

    from app import main
    from app.task_manager import DownloadManager

    manager = DownloadManager(
        state_dir=tmp_path / "state",
        default_output_root=tmp_path / "downloads",
        max_workers=1,
    )
    monkeypatch.setattr(main, "manager", manager)
    try:
        yield manager
    finally:
        manager.shutdown(wait=False, cancel_running=True)
