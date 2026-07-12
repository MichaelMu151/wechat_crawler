from pathlib import Path

from wechat_archive.db import Database
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
