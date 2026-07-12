from __future__ import annotations

import time

from wechat_archive.db import Database


class JobCancelled(RuntimeError):
    """Raised when a background job reaches a safe cancellation point."""


class JobControl:
    def __init__(self, db: Database, job_id: int, heartbeat_interval: float = 10.0):
        self.db = db
        self.job_id = job_id
        self.heartbeat_interval = heartbeat_interval
        self._last_heartbeat = 0.0

    def checkpoint(self) -> None:
        """Update liveness and stop at safe item/account boundaries."""
        now = time.monotonic()
        if now - self._last_heartbeat >= self.heartbeat_interval:
            self.db.execute(
                "UPDATE jobs SET heartbeat_at=datetime('now','localtime') WHERE id=?",
                (self.job_id,),
            )
            self._last_heartbeat = now
        row = self.db.fetchone(
            "SELECT cancel_requested FROM jobs WHERE id=?", (self.job_id,)
        )
        if row and row["cancel_requested"]:
            raise JobCancelled("任务已取消")
