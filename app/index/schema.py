"""派生索引库结构：``jobs_index`` + FTS5 external content + schema 版本。"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_OBJECTS = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs_index (
        rowid INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL DEFAULT 0,
        file_mtime REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        platform TEXT NOT NULL,
        media_type TEXT NOT NULL,
        title TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        output_dir TEXT NOT NULL DEFAULT '',
        total_items INTEGER NOT NULL DEFAULT 0,
        completed_items INTEGER NOT NULL DEFAULT 0,
        failed_items INTEGER NOT NULL DEFAULT 0,
        file_paths TEXT NOT NULL DEFAULT '[]',
        total_bytes INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
        title,
        author,
        source_url,
        content='jobs_index',
        content_rowid='rowid'
    )
    """,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    for statement in _OBJECTS:
        conn.execute(statement)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row[0]) if row else 0
    if current != SCHEMA_VERSION:
        conn.execute("DROP TABLE IF EXISTS jobs_fts")
        conn.execute("DROP TABLE IF EXISTS jobs_index")
        for statement in _OBJECTS[1:]:
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()