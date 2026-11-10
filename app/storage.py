from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .models import DownloadJob


def _fsync_directory(path: Path) -> None:
    """POSIX 上让目录项改动落盘；与 runtime.write_runtime_record 同一标准。

    文件内容由 tmp+fsync+replace 保证不损坏；缺少目录 fsync 时，
    崩溃可让最近数秒的 replace/unlink 回滚（任务状态丢失或复活）。
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass


class JobNotFoundError(KeyError):
    pass


def _replace_with_retry(source: Path, target: Path) -> None:
    delays = JsonJobStore._RETRY_DELAYS
    for index, delay in enumerate(delays):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if index == len(delays) - 1:
                raise
            time.sleep(delay)


def _read_text_with_retry(path: Path) -> str:
    delays = JsonJobStore._RETRY_DELAYS
    for index, delay in enumerate(delays):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if index == len(delays) - 1:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable retry state")


class JsonJobStore:
    """Persist each job separately so one interrupted write cannot corrupt all jobs."""

    # Windows 上杀毒/索引器可能短暂持有目标 json 句柄，replace/read 抛瞬时
    # PermissionError；有限重试覆盖绝大多数窗口，失败仍按原语义抛出。
    _RETRY_DELAYS = (0.05, 0.1, 0.2)

    _SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _job_path(self, job_id: str) -> Path:
        if not self._SAFE_ID.fullmatch(job_id):
            raise ValueError("Invalid job ID")
        return self.state_dir / f"{job_id}.json"

    def save(self, job: DownloadJob) -> None:
        payload = job.model_dump(mode="json")
        target = self._job_path(job.id)
        temporary = self.state_dir / f".{job.id}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(temporary, target)
                _fsync_directory(self.state_dir)
            finally:
                temporary.unlink(missing_ok=True)

    def get(self, job_id: str) -> DownloadJob:
        path = self._job_path(job_id)
        with self._lock:
            if not path.is_file():
                raise JobNotFoundError(job_id)
            return DownloadJob.model_validate_json(_read_text_with_retry(path))

    def delete(self, job_id: str) -> None:
        path = self._job_path(job_id)
        with self._lock:
            path.unlink(missing_ok=True)
            _fsync_directory(self.state_dir)

    def load_all(self) -> tuple[list[DownloadJob], list[str]]:
        jobs: list[DownloadJob] = []
        warnings: list[str] = []
        with self._lock:
            for path in sorted(self.state_dir.glob("*.json")):
                try:
                    jobs.append(
                        DownloadJob.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                    )
                except Exception as exc:
                    warnings.append(f"Could not load {path.name}: {exc}")
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs, warnings
