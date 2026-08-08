from pathlib import Path

from wechat_archive.config import apply_profile, load_config
from wechat_archive.db import Database
from wechat_archive.services.ops import (
    aggregate_errors,
    build_doctor_report,
    classify_error,
    reset_failed_resolves,
    suggest_next_actions,
)


def test_classify_error_kinds() -> None:
    assert classify_error("rate_limited: freq control") == "rate_limited"
    assert classify_error("appmsgpublish failed: ret=200013 msg=freq control") == (
        "rate_limited"
    )
    assert classify_error("auth: login expired") == "auth"
    assert classify_error("interrupted: safe to retry") == "interrupted"
    assert classify_error("api: invalid") == "api"


def test_doctor_and_retry_resolve(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (nickname_input, resolve_status, list_status, resolve_error)
            VALUES ('bad', 'failed', 'pending', 'api: no match')
            """
        )
        conn.execute(
            """
            INSERT INTO accounts (
                nickname_input, biz, resolve_status, list_status, list_error
            ) VALUES (
                'rl', 'MzX==', 'ok', 'retry_wait',
                'rate_limited: appmsgpublish failed: ret=200013 msg=freq control'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO articles (account_id, url, status, content_error)
            VALUES (2, 'https://mp.weixin.qq.com/s/x', 'failed', 'timeout')
            """
        )
    cfg = {
        "paths": {
            "database": str(tmp_path / "archive.db"),
            "sessions_dir": str(tmp_path / "sessions"),
            "platform_credentials": str(tmp_path / "sessions" / "c.yaml"),
        },
        "platform": {"backend": "platform"},
        "crawl": {"history_global_cooldown": 3600},
    }
    errors = aggregate_errors(db)
    assert errors["list_by_kind"].get("rate_limited", 0) >= 1
    assert errors["resolve_by_kind"].get("api", 0) >= 1
    actions = suggest_next_actions(db, cfg)
    assert any("限流" in a or "rate" in a.lower() or "history" in a for a in actions)
    report = build_doctor_report(db, cfg)
    assert "next_actions" in report
    n = reset_failed_resolves(db)
    assert n == 1
    row = db.fetchone("SELECT resolve_status FROM accounts WHERE nickname_input='bad'")
    assert row["resolve_status"] == "pending"


def test_apply_profile_safe() -> None:
    base = load_config()
    safe = apply_profile(base, "safe")
    assert safe["crawl"]["content_concurrency"] == 1
    assert safe["crawl"]["history_circuit_breaker_threshold"] == 2
    assert safe["crawl"]["sleep_min"] >= 15
