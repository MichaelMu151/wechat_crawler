from __future__ import annotations

import time
from collections.abc import Callable

from wechat_archive.db import Database


class JobCancelled(RuntimeError):
    """Raised when a background job reaches a safe cancellation point."""


class JobControl:
    def __init__(
        self,
        db: Database,
        job_id: int,
        heartbeat_interval: float = 10.0,
        progress_interval: float = 5.0,
    ):
        self.db = db
        self.job_id = job_id
        self.heartbeat_interval = heartbeat_interval
        self.progress_interval = progress_interval
        self._last_heartbeat = 0.0
        self._last_progress = 0.0

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

    def update_progress(self, current: int, total: int | None = None) -> None:
        now = time.monotonic()
        if (
            current > 0
            and (total is None or current < total)
            and now - self._last_progress < self.progress_interval
        ):
            self.checkpoint()
            return
        self.db.execute(
            """
            UPDATE jobs
            SET progress_current=?,
                progress_total=COALESCE(?, progress_total),
                heartbeat_at=datetime('now','localtime')
            WHERE id=?
            """,
            (current, total, self.job_id),
        )
        self._last_heartbeat = now
        self._last_progress = now
        self.checkpoint()


def cooperative_sleep(
    seconds: float,
    checkpoint: Callable[[], None] | None = None,
    slice_seconds: float = 5.0,
) -> None:
    """Sleep in bounded slices so long cooldowns remain cancellable."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if checkpoint:
            checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(slice_seconds, remaining))
