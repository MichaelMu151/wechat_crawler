from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.services.fetch_content import fetch_pending_contents
from wechat_archive.services.fetch_history import fetch_history_for_accounts
from wechat_archive.services.import_accounts import import_name_list
from wechat_archive.services.resolve_accounts import resolve_pending_accounts


class JobRunner:
    """Small persistent job runner for a single-machine archive deployment."""

    def __init__(self, db: Database, cfg: dict[str, Any], workers: int = 1):
        self.db = db
        self.cfg = cfg
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="wechat-job"
        )
        self._submitted: set[int] = set()
        self._lock = threading.Lock()

    def create(self, stage: str, payload: dict[str, Any] | None = None) -> int:
        payload = payload or {}
        with self.db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO jobs (stage, payload_json) VALUES (?, ?)",
                (stage, json.dumps(payload, ensure_ascii=False)),
            )
            job_id = int(cursor.lastrowid)
        self.submit(job_id)
        return job_id

    def submit(self, job_id: int) -> None:
        with self._lock:
            if job_id in self._submitted:
                return
            self._submitted.add(job_id)
        self.executor.submit(self._run, job_id)

    def resume_pending(self) -> None:
        for row in self.db.fetchall(
            "SELECT id FROM jobs WHERE status='pending' ORDER BY id"
        ):
            self.submit(int(row["id"]))

    def cancel(self, job_id: int) -> None:
        self.db.execute(
            "UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN "
            "('pending','running')",
            (job_id,),
        )
        self.event(job_id, "cancel_requested", "任务将在安全检查点停止")

    def event(
        self,
        job_id: int,
        event_type: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO job_events (job_id, event_type, message, data_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                message,
                json.dumps(data, ensure_ascii=False) if data is not None else None,
            ),
        )

    def _run(self, job_id: int) -> None:
        try:
            row = self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
            if row is None or row["status"] != "pending":
                return
            if row["cancel_requested"]:
                self._finish(job_id, "cancelled", None)
                return
            self.db.execute(
                """
                UPDATE jobs SET status='running',
                    started_at=datetime('now','localtime'),
                    heartbeat_at=datetime('now','localtime')
                WHERE id=?
                """,
                (job_id,),
            )
            self.event(job_id, "started", f"开始 {row['stage']} 任务")
            payload = json.loads(row["payload_json"] or "{}")
            result = self._dispatch(row["stage"], payload)
            latest = self.db.fetchone(
                "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
            )
            status = "cancelled" if latest and latest["cancel_requested"] else "done"
            self._finish(job_id, status, result)
        except Exception as exc:
            self.db.execute(
                """
                UPDATE jobs SET status='failed', error=?,
                    finished_at=datetime('now','localtime')
                WHERE id=?
                """,
                (str(exc)[:1000], job_id),
            )
            self.event(job_id, "error", str(exc)[:1000])
        finally:
            with self._lock:
                self._submitted.discard(job_id)

    def _dispatch(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = HttpClient(self.cfg)
        try:
            if stage == "import":
                return import_name_list(
                    self.db, payload.get("path") or self.cfg["paths"]["name_list"]
                )
            if stage == "resolve":
                return resolve_pending_accounts(
                    self.db, client, self.cfg, limit=payload.get("limit")
                )
            if stage == "history":
                return fetch_history_for_accounts(
                    self.db,
                    client,
                    self.cfg,
                    limit_accounts=payload.get("limit"),
                )
            if stage == "content":
                return fetch_pending_contents(
                    self.db, client, self.cfg, limit=payload.get("limit")
                )
            raise ValueError(f"unsupported job stage: {stage}")
        finally:
            client.close()

    def _finish(
        self, job_id: int, status: str, result: dict[str, Any] | None
    ) -> None:
        self.db.execute(
            """
            UPDATE jobs SET status=?, result_json=?,
                finished_at=datetime('now','localtime'),
                heartbeat_at=datetime('now','localtime')
            WHERE id=?
            """,
            (
                status,
                json.dumps(result, ensure_ascii=False) if result else None,
                job_id,
            ),
        )
        self.event(job_id, status, f"任务状态：{status}", result)
