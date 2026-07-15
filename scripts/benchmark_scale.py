from __future__ import annotations

import argparse
import resource
import tempfile
import time
from pathlib import Path

from wechat_archive.db import Database
from wechat_archive.services.article_queries import article_page


def timed(label: str, fn):
    started = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic SQLite scale benchmark")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()

    temporary = None
    if args.database is None:
        temporary = tempfile.TemporaryDirectory(prefix="wechat-archive-bench-")
        database_path = Path(temporary.name) / "benchmark.db"
    else:
        database_path = args.database
    db = Database(database_path)
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('benchmark')"
        ).lastrowid

    chunk_size = 5_000
    started = time.perf_counter()
    for start in range(0, args.rows, chunk_size):
        stop = min(args.rows, start + chunk_size)
        db.executemany(
            """
            INSERT INTO articles (
                account_id, title, content_text, publish_ts, url, normalized_url, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    account_id,
                    f"benchmark article {index}",
                    f"research archive needle batch {index}",
                    1_800_000_000 - index,
                    f"https://mp.weixin.qq.com/s/benchmark-{index}",
                    f"https://mp.weixin.qq.com/s/benchmark-{index}",
                    "listed" if index % 3 else "ok",
                )
                for index in range(start, stop)
            ],
        )
    print(f"insert_{args.rows}: {time.perf_counter() - started:.3f}s")

    timed(
        "pending_queue_500",
        lambda: db.fetchall(
            """
            SELECT id FROM articles
            WHERE status IN ('listed','retry_wait')
              AND (next_retry_at IS NULL OR next_retry_at <= datetime('now','localtime'))
            ORDER BY id LIMIT 500
            """
        ),
    )
    first = timed("article_first_page", lambda: article_page(db, limit=50))
    cursor = first["next_cursor"]
    timed(
        "article_cursor_page",
        lambda: article_page(
            db,
            limit=50,
            before_ts=cursor["before_ts"],
            before_id=cursor["before_id"],
        ),
    )
    timed(
        "article_deep_offset",
        lambda: article_page(db, limit=50, offset=max(0, args.rows - 50)),
    )
    timed("fts_search", lambda: article_page(db, q="needle", limit=50))

    size_mb = database_path.stat().st_size / 1024 / 1024
    peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"database: {database_path}")
    print(f"database_size: {size_mb:.1f} MiB")
    print(f"peak_rss_platform_units: {peak_kib}")
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
