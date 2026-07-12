from pathlib import Path

from wechat_archive.db import Database


def test_schema_migrates_and_recovers_running_accounts(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (nickname_input, list_status)
            VALUES ('test', 'running')
            """
        )

    recovered = db.recover_stale_work()

    account = db.fetchone("SELECT * FROM accounts WHERE nickname_input='test'")
    assert recovered["accounts"] == 1
    assert account is not None
    assert account["list_status"] == "failed"
    assert "safe to retry" in account["list_error"]


def test_iter_rows_streams_batches(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    db.executemany(
        "INSERT INTO accounts (nickname_input) VALUES (?)",
        [(f"account-{index}",) for index in range(7)],
    )
    rows = list(db.iter_rows("SELECT * FROM accounts ORDER BY id", batch_size=2))
    assert [row["nickname_input"] for row in rows] == [
        f"account-{index}" for index in range(7)
    ]
