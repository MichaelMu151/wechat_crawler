from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.services.jobs import JobRunner


def test_create_reuses_active_job(tmp_path: Path, monkeypatch: Any) -> None:
    db = Database(tmp_path / "archive.db")
    runner = JobRunner(db, {"jobs": {"workers": 1}})
    submitted: list[int] = []
    monkeypatch.setattr(runner, "submit", submitted.append)

    first = runner.create("content")
    second = runner.create("content")

    assert first == second
    assert submitted == [first]
    row = db.fetchone("SELECT COUNT(*) AS count FROM jobs")
    assert row is not None
    assert row["count"] == 1
    runner.executor.shutdown(wait=False)
