from pathlib import Path

import pytest

from wechat_archive.db import Database
from wechat_archive.services.job_control import JobCancelled, JobControl


def test_job_control_updates_heartbeat_and_honors_cancel(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        job_id = conn.execute(
            "INSERT INTO jobs (stage, status) VALUES ('content', 'running')"
        ).lastrowid
    control = JobControl(db, int(job_id), heartbeat_interval=0)

    control.checkpoint()

    row = db.fetchone("SELECT heartbeat_at FROM jobs WHERE id=?", (job_id,))
    assert row is not None
    assert row["heartbeat_at"] is not None

    db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    with pytest.raises(JobCancelled):
        control.checkpoint()
