"""派生索引 Sidecar：加速资料库检索，可随时删除重建。

事实源始终是 ``data/state/*.json``；本包只读事实源，任何写入仅落在
``data/index/jobs.sqlite``。
"""

from .backend import IndexBackend, IndexedJob
from .indexer import JobIndexer, build_entry, entry_from_job, filter_entries
from .sqlite_backend import SQLiteIndexBackend

__all__ = [
    "IndexBackend",
    "IndexedJob",
    "JobIndexer",
    "SQLiteIndexBackend",
    "build_entry",
    "entry_from_job",
    "filter_entries",
]