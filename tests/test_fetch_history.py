from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.services import fetch_history as fetch_history_module
from wechat_archive.services.fetch_history import fetch_history_for_accounts


class FakePlatform:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def list_articles(self, fakeid: str, begin: int = 0, count: int = 20, keyword=None):
        self.calls.append((fakeid, begin, count))
        if begin > 0:
            return {"articles": [], "total": 1, "begin": begin, "count": 0, "can_continue": False}
        return {
            "articles": [
                {
                    "aid": "265000_1",
                    "title": "可抓取文章",
                    "author": "作者",
                    "digest": "摘要",
                    "cover_url": "https://mmbiz.qpic.cn/cover/0",
                    "url": "https://mp.weixin.qq.com/s/good",
                    "publish_ts": 1704067200,
                    "publish_time": "2024-01-01 08:00:00",
                    "mid": "265000",
                    "idx": 1,
                    "raw_list_json": "{}",
                }
            ],
            "total": 1,
            "begin": 0,
            "count": 1,
            "can_continue": False,
        }


def test_fetch_history_uses_platform_client(tmp_path: Path, monkeypatch: Any) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (nickname_input, biz, resolve_status, list_status)
            VALUES ('sample', 'MzFakeId==', 'ok', 'pending')
            """
        )

    fake = FakePlatform()
    monkeypatch.setattr(
        fetch_history_module,
        "build_history_client",
        lambda _cfg: fake,
    )
    cfg = {
        "paths": {"sessions_dir": str(tmp_path / "sessions")},
        "platform": {"backend": "platform"},
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "history_page_size": 20,
            "account_batch_size": 50,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
            "history_rate_limit_retries": 1,
            "history_rate_limit_cooldown": 0,
        },
    }
    stats = fetch_history_for_accounts(db, None, cfg)
    assert stats["accounts_ok"] == 1
    assert stats["articles_upserted"] >= 1
    assert fake.calls and fake.calls[0][0] == "MzFakeId=="
    row = db.fetchone("SELECT title, status, biz FROM articles LIMIT 1")
    assert row["title"] == "可抓取文章"
    assert row["status"] == "listed"
    assert row["biz"] == "MzFakeId=="
    account = db.fetchone("SELECT list_status FROM accounts WHERE id=1")
    assert account["list_status"] == "done"


def test_platform_auth_error_marks_need_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from wechat_archive.platform_client import PlatformAuthError

    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (nickname_input, biz, resolve_status, list_status)
            VALUES ('sample', 'MzFakeId==', 'ok', 'pending')
            """
        )

    class BadPlatform:
        def list_articles(self, **_kwargs):
            raise PlatformAuthError("login expired")

    monkeypatch.setattr(
        fetch_history_module,
        "build_history_client",
        lambda _cfg: BadPlatform(),
    )
    cfg = {
        "paths": {"sessions_dir": str(tmp_path / "sessions")},
        "platform": {"backend": "platform"},
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "history_page_size": 20,
            "account_batch_size": 50,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
            "history_rate_limit_retries": 0,
            "history_rate_limit_cooldown": 0,
        },
    }
    stats = fetch_history_for_accounts(db, None, cfg)
    assert stats["accounts_fail"] == 1
    account = db.fetchone("SELECT list_status, list_error FROM accounts WHERE id=1")
    assert account["list_status"] == "need_session"
    assert "expired" in (account["list_error"] or "").lower() or "登录" in (
        account["list_error"] or ""
    )
