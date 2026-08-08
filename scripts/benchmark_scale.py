from __future__ import annotations

import argparse
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

from wechat_archive.config import PROFILES, apply_profile, load_config
from wechat_archive.db import Database
from wechat_archive.services.article_queries import article_page
from wechat_archive.services import fetch_content as fetch_content_module
from wechat_archive.services.fetch_content import fetch_pending_contents


def timed(label: str, fn):
    started = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s")
    return value


def _mock_content_benchmark(rows: int, concurrency: int) -> dict[str, Any]:
    """Mock HTTP content crawl to compare profile throughput (no network)."""
    temporary = tempfile.TemporaryDirectory(prefix="wechat-content-bench-")
    db = Database(Path(temporary.name) / "bench.db")
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('bench')"
        ).lastrowid
        for i in range(rows):
            conn.execute(
                """
                INSERT INTO articles (account_id, url, status)
                VALUES (?, ?, 'listed')
                """,
                (account_id, f"https://mp.weixin.qq.com/s/bench-{i}"),
            )

    class FastClient:
        def get_text(self, url: str) -> tuple[str, str]:
            html = (
                '<meta property="og:title" content="t">'
                '<div id="js_content"><p>body</p></div>'
            )
            return url, html

        def close(self) -> None:
            return None

    cfg = apply_profile(load_config(), "balanced")
    cfg["crawl"]["content_concurrency"] = concurrency
    cfg["crawl"]["content_sleep_min"] = 0
    cfg["crawl"]["content_sleep_max"] = 0
    cfg["crawl"]["content_batch_size"] = 50
    # patch sleep in caller via monkey-style: set sleeps to 0 already
    started = time.perf_counter()
    # bypass cooperative_sleep cost
    original = fetch_content_module.cooperative_sleep
    fetch_content_module.cooperative_sleep = lambda *_a, **_k: None
    try:
        stats = fetch_pending_contents(db, FastClient(), cfg, limit=rows)
    finally:
        fetch_content_module.cooperative_sleep = original
        temporary.cleanup()
    elapsed = max(0.001, time.perf_counter() - started)
    per_hour = stats["ok"] / elapsed * 3600
    return {
        "ok": stats["ok"],
        "elapsed_s": round(elapsed, 3),
        "articles_per_hour_equiv": round(per_hour, 1),
        "concurrency": concurrency,
        "circuit_open": stats.get("circuit_open"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic SQLite scale benchmark")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--content-mock",
        type=int,
        default=0,
        help="Also run mock content throughput bench with N articles",
    )
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
    print("profiles:", ", ".join(sorted(PROFILES)))
    if args.content_mock:
        for concurrency in (1, 2, 3):
            result = _mock_content_benchmark(args.content_mock, concurrency)
            print(f"content_mock_c{concurrency}: {result}")
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
