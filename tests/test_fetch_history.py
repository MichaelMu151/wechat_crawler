import json
from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.services import fetch_history as fetch_history_module
from wechat_archive.services.fetch_history import (
    SessionPool,
    _request_history_with_failover,
    fetch_history_for_accounts,
)


class HistoryResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FailoverClient:
    def __init__(self) -> None:
        self.uins: list[str] = []

    def get(self, _url: str, **kwargs: Any) -> HistoryResponse:
        uin = kwargs["params"]["uin"]
        self.uins.append(uin)
        if uin == "bad":
            raise RuntimeError("invalid session")
        general = {
            "list": [
                {
                    "comm_msg_info": {"datetime": 1704067200},
                    "app_msg_ext_info": {
                        "title": "可抓取文章",
                        "content_url": "https://mp.weixin.qq.com/s/good",
                    },
                }
            ]
        }
        return HistoryResponse(
            {
                "base_resp": {"ret": 0},
                "general_msg_list": json.dumps(general),
                "can_msg_continue": 0,
            }
        )


def test_history_page_fails_over_to_next_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (nickname_input, biz, resolve_status, list_status)
            VALUES ('sample', 'biz', 'ok', 'pending')
            """
        )
    monkeypatch.setattr(
        fetch_history_module,
        "load_sessions",
        lambda _path: [
            {"name": "bad", "uin": "bad", "key": "key", "cookie": "cookie"},
            {"name": "good", "uin": "good", "key": "key", "cookie": "cookie"},
        ],
    )
    client = FailoverClient()
    cfg = {
        "paths": {"sessions_dir": str(tmp_path / "sessions")},
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "history_page_size": 10,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
        },
    }

    result = fetch_history_for_accounts(db, client, cfg)

    article = db.fetchone("SELECT title FROM articles")
    account = db.fetchone("SELECT list_status FROM accounts")
    assert result["accounts_ok"] == 1
    assert client.uins == ["bad", "good"]
    assert article is not None
    assert article["title"] == "可抓取文章"
    assert account is not None
    assert account["list_status"] == "done"


class SequenceClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.offsets: list[int] = []
        self.uins: list[str] = []

    def get(self, _url: str, **kwargs: Any) -> HistoryResponse:
        self.offsets.append(int(kwargs["params"]["offset"]))
        self.uins.append(kwargs["params"]["uin"])
        return HistoryResponse(self.payloads.pop(0))


def history_payload(timestamp: int, suffix: str, can_continue: int) -> dict[str, Any]:
    general = {
        "list": [
            {
                "comm_msg_info": {"datetime": timestamp},
                "app_msg_ext_info": {
                    "title": suffix,
                    "content_url": f"https://mp.weixin.qq.com/s/{suffix}",
                },
            }
        ]
    }
    return {
        "base_resp": {"ret": 0},
        "general_msg_list": json.dumps(general),
        "can_msg_continue": can_continue,
    }


def test_history_existing_checkpoint_keeps_callback_and_stops_at_watermark(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        account_id = conn.execute(
            """
            INSERT INTO accounts (nickname_input, biz, resolve_status, list_status)
            VALUES ('sample', 'biz', 'ok', 'done')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO crawl_checkpoints (account_id, newest_publish_ts)
            VALUES (?, 1704067200)
            """,
            (account_id,),
        )
    monkeypatch.setattr(
        fetch_history_module,
        "load_sessions",
        lambda _path: [
            {"name": "good", "uin": "good", "key": "key", "cookie": "cookie"}
        ],
    )
    client = SequenceClient(
        [
            history_payload(1706745600, "new", 1),
            history_payload(1704067200, "watermark", 1),
        ]
    )
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    cfg = {
        "paths": {"sessions_dir": str(tmp_path / "sessions")},
        "crawl": {
            "sleep_min": 0,
            "sleep_max": 0,
            "history_page_size": 10,
            "account_batch_size": 1,
            "start_date": "2018-01-01",
            "end_date": "2026-12-31",
        },
    }

    result = fetch_history_for_accounts(db, client, cfg, checkpoint=checkpoint)

    assert result["accounts_ok"] == 1
    assert client.offsets == [0, 10]
    assert checkpoints >= 3
    assert db.fetchone("SELECT id FROM articles WHERE title='watermark'") is None


def test_history_rate_limit_retries_same_session(monkeypatch: Any) -> None:
    monkeypatch.setattr(fetch_history_module, "cooperative_sleep", lambda *_args: None)
    client = SequenceClient(
        [
            {"base_resp": {"ret": -3, "err_msg": "访问过于频繁"}},
            history_payload(1704067200, "ok", 0),
        ]
    )
    pool = SessionPool(
        [
            {"name": "first", "uin": "first", "key": "key", "cookie": "cookie"},
            {"name": "second", "uin": "second", "key": "key", "cookie": "cookie"},
        ]
    )

    payload = _request_history_with_failover(
        client, "biz", 0, 10, pool, rate_limit_retries=1
    )

    assert payload["base_resp"]["ret"] == 0
    assert client.uins == ["first", "first"]
