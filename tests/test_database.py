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


def test_recover_stale_work_keeps_recent_running_jobs(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (stage, status, started_at, heartbeat_at)
            VALUES (
                'content',
                'running',
                datetime('now','localtime'),
                datetime('now','localtime')
            )
            """
        )

    recovered = db.recover_stale_work(stale_minutes=30)

    job = db.fetchone("SELECT status FROM jobs")
    assert recovered["jobs"] == 0
    assert job is not None
    assert job["status"] == "running"


def test_restart_requeues_running_jobs(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (stage, status, heartbeat_at)
            VALUES ('content', 'running', datetime('now','localtime'))
            """
        )

    recovered = db.recover_interrupted_jobs()

    job = db.fetchone("SELECT status FROM jobs")
    assert recovered["jobs_requeued"] == 1
    assert job is not None
    assert job["status"] == "pending"


def test_online_backup_is_consistent(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    db.execute("INSERT INTO accounts (nickname_input) VALUES ('backup')")

    backup_path = db.backup(tmp_path / "backup.db")
    backup = Database(backup_path)

    row = backup.fetchone("SELECT nickname_input FROM accounts")
    assert row is not None
    assert row["nickname_input"] == "backup"


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
