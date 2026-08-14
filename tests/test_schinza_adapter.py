import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from wechat_archive.db import Database
from wechat_archive.platform_client import PlatformAuthError, PlatformRateLimited
from wechat_archive.schinza_client import (
    SchinzaGetmsgClient,
    credential_status,
    parse_getmsg_payload,
    summarize_schinza_accounts,
)
from wechat_archive.services.import_schinza import import_schinza_credentials


def _account(name: str = "测试号", *, expired: bool = False) -> dict:
    expires = datetime.now() + timedelta(minutes=-1 if expired else 30)
    return {
        "id": "schinza-1",
        "name": name,
        "status": "expired" if expired else "active",
        "expires_at": expires.isoformat(timespec="seconds"),
        "credentials": {
            "__biz": "MzWechatBiz==",
            "uin": "123456",
            "key": "secret-key",
            "pass_ticket": "secret-ticket",
        },
    }


def _write_store(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"accounts": rows}, ensure_ascii=False), encoding="utf-8")


def test_schinza_store_status_and_import_do_not_copy_secrets(tmp_path: Path) -> None:
    source = tmp_path / "accounts.json"
    _write_store(source, [_account()])
    summary = summarize_schinza_accounts(source)
    assert summary["active"] == 1

    db = Database(tmp_path / "archive.db")
    db.execute(
        "INSERT INTO accounts (nickname_input) VALUES (?)",
        ("测试号",),
    )
    result = import_schinza_credentials(db, source)
    assert result["matched"] == 1
    row = db.fetchone(
        """
        SELECT wechat_biz, schinza_account_id, history_backend, resolve_status
        FROM accounts
        """
    )
    assert row["wechat_biz"] == "MzWechatBiz=="
    assert row["history_backend"] == "schinza_getmsg"
    assert row["resolve_status"] == "ok"
    assert "secret-key" not in (tmp_path / "archive.db").read_bytes().decode(
        "latin1", errors="ignore"
    )


def test_import_marks_expired_credentials_need_session(tmp_path: Path) -> None:
    source = tmp_path / "accounts.json"
    row = _account(expired=True)
    _write_store(source, [row])
    assert credential_status(row)[0] is False
    db = Database(tmp_path / "archive.db")
    db.execute("INSERT INTO accounts (nickname_input) VALUES ('测试号')")
    result = import_schinza_credentials(db, source)
    assert result["expired"] == 1
    account = db.fetchone("SELECT list_status, list_error FROM accounts")
    assert account["list_status"] == "need_session"
    assert "过期" in account["list_error"]


def test_parse_getmsg_expands_multi_articles_and_offset() -> None:
    payload = {
        "ret": 0,
        "can_msg_continue": 1,
        "next_offset": 10,
        "general_msg_list": json.dumps(
            {
                "list": [
                    {
                        "comm_msg_info": {"datetime": 1704067200},
                        "app_msg_ext_info": {
                            "title": "头条",
                            "content_url": "https://mp.weixin.qq.com/s/a?mid=1&idx=1&sn=x",
                            "multi_app_msg_item_list": [
                                {
                                    "title": "次条",
                                    "content_url": "https://mp.weixin.qq.com/s/b?mid=1&idx=2&sn=y",
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
    }
    page = parse_getmsg_payload(payload, begin=0)
    assert [row["title"] for row in page["articles"]] == ["头条", "次条"]
    assert page["next_begin"] == 10
    assert page["publish_fetched"] == 1
    assert page["can_continue"] is True


@pytest.mark.parametrize("ret,errmsg,error", [(-6, "unknownerror", PlatformRateLimited), (-3, "session expired", PlatformAuthError)])
def test_parse_getmsg_classifies_wechat_errors(ret, errmsg, error) -> None:
    with pytest.raises(error):
        parse_getmsg_payload({"ret": ret, "errmsg": errmsg})


def test_client_rejects_expired_before_network(tmp_path: Path) -> None:
    source = tmp_path / "accounts.json"
    _write_store(source, [_account(expired=True)])
    client = SchinzaGetmsgClient(source)
    with pytest.raises(PlatformAuthError) as caught:
        client.list_articles("MzWechatBiz==")
    assert "secret" not in str(caught.value)


def test_client_classifies_non_json_rate_limit_page(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accounts.json"
    _write_store(source, [_account()])

    class Response:
        status_code = 200
        text = "<html>你的访问过于频繁</html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    class Session:
        trust_env = True

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("wechat_archive.schinza_client.requests.Session", Session)
    client = SchinzaGetmsgClient(source)
    with pytest.raises(PlatformRateLimited) as caught:
        client.list_articles("MzWechatBiz==")
    assert "secret-key" not in str(caught.value)
