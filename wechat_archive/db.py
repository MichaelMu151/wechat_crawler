from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname_input  TEXT NOT NULL,
    sample_url      TEXT,
    biz             TEXT UNIQUE,
    account_name    TEXT,
    username        TEXT,
    head_img        TEXT,
    resolve_status  TEXT NOT NULL DEFAULT 'pending',
      -- pending | ok | failed
    list_status     TEXT NOT NULL DEFAULT 'pending',
      -- pending | running | done | failed | need_session
    resolve_error   TEXT,
    list_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    biz             TEXT,
    sn              TEXT,
    mid             TEXT,
    idx             INTEGER,
    title           TEXT,
    author          TEXT,
    digest          TEXT,
    publish_time    TEXT,
    publish_ts      INTEGER,
    url             TEXT,
    cover_url       TEXT,
    content_html    TEXT,
    content_text    TEXT,
    status          TEXT NOT NULL DEFAULT 'listed',
      -- listed | ok | deleted | failed | out_of_range
    content_error   TEXT,
    raw_list_json   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(account_id, sn),
    UNIQUE(url)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at     TEXT,
    ok_count        INTEGER DEFAULT 0,
    fail_count      INTEGER DEFAULT 0,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total  INTEGER,
    result_json     TEXT,
    error           TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    started_at      TEXT,
    heartbeat_at    TEXT,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    message         TEXT,
    data_json       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS crawl_checkpoints (
    account_id      INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    history_offset  INTEGER NOT NULL DEFAULT 0,
    newest_publish_ts INTEGER,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account_id);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_publish ON articles(publish_ts);
CREATE INDEX IF NOT EXISTS idx_accounts_biz ON accounts(biz);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);
"""

ARTICLE_COLUMNS = {
    "normalized_url": "TEXT",
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
    "next_retry_at": "TEXT",
    "last_fetched_at": "TEXT",
}

ACCOUNT_COLUMNS = {
    "last_listed_at": "TEXT",
    # Schinza's getmsg identity is separate from the legacy ``biz`` column,
    # which existing installations use for a public-platform fakeid.
    "wechat_biz": "TEXT",
    "schinza_account_id": "TEXT",
    "history_backend": "TEXT",
}


class Database:
    def __init__(self, path: str | Path, options: dict[str, Any] | None = None):
        self.path = Path(path)
        self.options = options or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            f"PRAGMA synchronous={self.options.get('synchronous', 'NORMAL')}"
        )
        conn.execute(
            f"PRAGMA cache_size={int(self.options.get('cache_size_kib', -65536))}"
        )
        conn.execute(
            f"PRAGMA mmap_size={int(self.options.get('mmap_size', 268435456))}"
        )
        conn.execute(
            "PRAGMA wal_autocheckpoint="
            f"{int(self.options.get('wal_autocheckpoint', 1000))}"
        )
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn, "articles", ARTICLE_COLUMNS)
            self._ensure_columns(conn, "accounts", ACCOUNT_COLUMNS)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_retry "
                "ON articles(status, next_retry_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_normalized_url "
                "ON articles(normalized_url)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_list "
                "ON articles(COALESCE(publish_ts, 0) DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_accounts_wechat_biz "
                "ON accounts(wechat_biz)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_accounts_history_backend "
                "ON accounts(history_backend, list_status, id)"
            )
            self._init_fts(conn)

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @staticmethod
    def _init_fts(conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                    title, author, digest, content_text,
                    content='articles', content_rowid='id',
                    tokenize='unicode61'
                )
                """
            )
            conn.executescript(
                """
                DROP TRIGGER IF EXISTS articles_fts_ai;
                DROP TRIGGER IF EXISTS articles_fts_ad;
                DROP TRIGGER IF EXISTS articles_fts_au;
                """
            )
            conn.executescript(
                """
                CREATE TRIGGER articles_fts_ai AFTER INSERT ON articles BEGIN
                    INSERT INTO articles_fts(rowid, title, author, digest, content_text)
                    VALUES (new.id, new.title, new.author, new.digest, new.content_text);
                END;
                CREATE TRIGGER articles_fts_ad AFTER DELETE ON articles BEGIN
                    INSERT INTO articles_fts(articles_fts, rowid, title, author, digest, content_text)
                    VALUES ('delete', old.id, old.title, old.author, old.digest, old.content_text);
                END;
                CREATE TRIGGER articles_fts_au AFTER UPDATE OF
                    title, author, digest, content_text ON articles
                WHEN old.title IS NOT new.title
                  OR old.author IS NOT new.author
                  OR old.digest IS NOT new.digest
                  OR old.content_text IS NOT new.content_text
                BEGIN
                    INSERT INTO articles_fts(articles_fts, rowid, title, author, digest, content_text)
                    VALUES ('delete', old.id, old.title, old.author, old.digest, old.content_text);
                    INSERT INTO articles_fts(rowid, title, author, digest, content_text)
                    VALUES (new.id, new.title, new.author, new.digest, new.content_text);
                END;
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0]
            if count == 0:
                conn.execute("INSERT INTO articles_fts(articles_fts) VALUES ('rebuild')")
        except sqlite3.OperationalError:
            # Some minimal SQLite builds do not include FTS5; API falls back to LIKE.
            pass

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.connection() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, seq) -> None:
        with self.connection() as conn:
            conn.executemany(sql, seq)

    def iter_rows(
        self, sql: str, params: tuple | dict = (), batch_size: int = 500
    ) -> Iterable[sqlite3.Row]:
        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            while rows := cursor.fetchmany(batch_size):
                yield from rows

    def fetchall(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def recover_stale_work(self, stale_minutes: int = 30) -> dict[str, int]:
        """Make interrupted account and job work eligible for another attempt.

        stale_minutes <= 0 means reclaim all running accounts (CLI history start).
        """
        with self.connection() as conn:
            if stale_minutes <= 0:
                accounts = conn.execute(
                    """
                    UPDATE accounts
                    SET list_status='failed',
                        list_error='interrupted: safe to retry',
                        updated_at=datetime('now','localtime')
                    WHERE list_status='running'
                    """
                ).rowcount
                jobs = conn.execute(
                    """
                    UPDATE jobs
                    SET status='failed', error='worker heartbeat expired',
                        finished_at=datetime('now','localtime')
                    WHERE status='running'
                    """
                ).rowcount
            else:
                stale = f"-{stale_minutes} minutes"
                accounts = conn.execute(
                    """
                    UPDATE accounts
                    SET list_status='failed',
                        list_error='interrupted: safe to retry',
                        updated_at=datetime('now','localtime')
                    WHERE list_status='running'
                      AND updated_at < datetime('now', ?, 'localtime')
                    """,
                    (stale,),
                ).rowcount
                jobs = conn.execute(
                    """
                    UPDATE jobs
                    SET status='failed', error='worker heartbeat expired',
                        finished_at=datetime('now','localtime')
                    WHERE status='running'
                      AND COALESCE(heartbeat_at, started_at) <
                          datetime('now', ?, 'localtime')
                    """,
                    (stale,),
                ).rowcount
        return {"accounts": accounts, "jobs": jobs}

    def recover_interrupted_jobs(self) -> dict[str, int]:
        """Requeue jobs owned by a previous local server process."""
        with self.connection() as conn:
            cancelled = conn.execute(
                """
                UPDATE jobs SET status='cancelled',
                    finished_at=datetime('now','localtime')
                WHERE status IN ('pending','running') AND cancel_requested=1
                """
            ).rowcount
            pending = conn.execute(
                """
                UPDATE jobs SET status='pending', error=NULL, finished_at=NULL
                WHERE status='running' AND cancel_requested=0
                """
            ).rowcount
            accounts = conn.execute(
                """
                UPDATE accounts
                SET list_status='failed', list_error='interrupted: safe to retry',
                    updated_at=datetime('now','localtime')
                WHERE list_status='running'
                """
            ).rowcount
        return {"jobs_requeued": pending, "jobs_cancelled": cancelled, "accounts": accounts}

    def cleanup_job_events(self, retention_days: int = 30) -> int:
        with self.connection() as conn:
            return conn.execute(
                """
                DELETE FROM job_events
                WHERE created_at < datetime('now', ?, 'localtime')
                """,
                (f"-{max(1, retention_days)} days",),
            ).rowcount

    def fts_available(self) -> bool:
        row = self.fetchone(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles_fts'"
        )
        return row is not None

    def backup(self, destination: str | Path) -> Path:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        target = sqlite3.connect(destination_path)
        try:
            source.backup(target, pages=1000, sleep=0.05)
        finally:
            target.close()
            source.close()
        return destination_path

    def maintain(self, event_retention_days: int = 30) -> dict[str, Any]:
        deleted_events = self.cleanup_job_events(event_retention_days)
        with self.connection() as conn:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            conn.execute("ANALYZE")
            conn.execute("PRAGMA optimize")
        with self._connect() as conn:
            checkpoint = tuple(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        return {
            "integrity": integrity,
            "deleted_events": deleted_events,
            "wal_checkpoint": checkpoint,
        }
