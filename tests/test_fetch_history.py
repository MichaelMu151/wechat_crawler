import json
from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.services import fetch_history as fetch_history_module
from wechat_archive.services.fetch_history import fetch_history_for_accounts


class HistoryResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

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
