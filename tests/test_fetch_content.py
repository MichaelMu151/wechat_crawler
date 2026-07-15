from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.services import fetch_content as fetch_content_module
from wechat_archive.services.fetch_content import fetch_pending_contents


class FailingClient:
    def get_text(self, _url: str) -> tuple[str, str]:
        raise TimeoutError("network timeout")


def test_content_failure_exhausts_retry_budget(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('sample')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO articles (account_id, url, status)
            VALUES (?, 'https://mp.weixin.qq.com/s/example', 'listed')
            """,
            (account_id,),
        )
    cfg = {
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
            "content_max_retries": 1,
        }
    }

    result = fetch_pending_contents(db, FailingClient(), cfg)

    row = db.fetchone("SELECT status, retry_count FROM articles")
    assert result["failed"] == 1
    assert row is not None
    assert row["status"] == "failed"
    assert row["retry_count"] == 1


class SuccessfulClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_text(self, url: str) -> tuple[str, str]:
        self.calls += 1
        html = (
            '<meta property="og:title" content="批量文章">'
            '<div id="js_content"><p>正文</p></div>'
        )
        return url, html


def test_content_processes_multiple_bounded_batches(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(fetch_content_module, "cooperative_sleep", lambda *_args: None)
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('sample')"
        ).lastrowid
        for index in range(5):
            conn.execute(
                """
                INSERT INTO articles (account_id, url, status)
                VALUES (?, ?, 'listed')
                """,
                (account_id, f"https://mp.weixin.qq.com/s/{index}"),
            )
    cfg = {
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
            "content_max_retries": 1,
            "content_batch_size": 2,
        }
    }
    progress: list[tuple[int, int]] = []
    client = SuccessfulClient()

    result = fetch_pending_contents(
        db, client, cfg, progress=lambda current, total: progress.append((current, total))
    )

    assert result["ok"] == 5
    assert result["total"] == 5
    assert client.calls == 5
    assert progress[0] == (0, 5)
    assert progress[-1] == (5, 5)


class RateLimitedClient:
    def get_text(self, _url: str) -> tuple[str, str]:
        raise RuntimeError("rate_limited")


def test_rate_limit_stays_retryable_with_long_cooldown(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(fetch_content_module, "cooperative_sleep", lambda *_args: None)
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('sample')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO articles (account_id, url, status)
            VALUES (?, 'https://mp.weixin.qq.com/s/limited', 'listed')
            """,
            (account_id,),
        )
    cfg = {
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
            "content_max_retries": 1,
            "content_batch_size": 10,
            "content_rate_limit_cooldown": 1800,
        }
    }

    fetch_pending_contents(db, RateLimitedClient(), cfg)

    row = db.fetchone(
        "SELECT status, content_error, next_retry_at FROM articles"
    )
    assert row is not None
    assert row["status"] == "retry_wait"
    assert row["content_error"] == "rate_limited"
    assert row["next_retry_at"] is not None
