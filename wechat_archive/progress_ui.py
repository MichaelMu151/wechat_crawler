"""终端进度展示：总进度 + 当前账号进度。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


ProgressFn = Callable[..., None]


@dataclass
class CrawlProgress:
    """把 fetch_* 的 progress 回调接到 Rich 进度条，并可选写入进度日志。"""

    title: str = "抓取中"
    log_path: Path | None = None
    console: Console = field(default_factory=Console)
    _progress: Progress | None = None
    _overall: TaskID | None = None
    _account: TaskID | None = None
    _started: float = 0.0
    _last_log: float = 0.0
    _ok: int = 0
    _failed: int = 0
    _deleted: int = 0

    def __enter__(self) -> "CrawlProgress":
        self._started = time.monotonic()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TextColumn("{task.fields[rate]}"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
            console=self.console,
            refresh_per_second=4,
            expand=True,
        )
        self._progress.start()
        self._overall = self._progress.add_task(
            self.title, total=None, rate="--"
        )
        self._account = self._progress.add_task(
            "当前账号: —", total=None, rate=""
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def callback(self, current: int, total: int | None = None, **info: Any) -> None:
        if self._progress is None or self._overall is None or self._account is None:
            return

        if info.get("ok") is not None:
            self._ok = int(info["ok"])
        if info.get("failed") is not None:
            self._failed = int(info["failed"])
        if info.get("deleted") is not None:
            self._deleted = int(info["deleted"])

        elapsed = max(0.001, time.monotonic() - self._started)
        rate = current / elapsed * 60.0 if current else 0.0
        rate_text = f"{rate:.1f}/分 · ok={self._ok} fail={self._failed}"
        if self._deleted:
            rate_text += f" del={self._deleted}"

        desc = self.title
        if info.get("phase"):
            desc = f"{self.title} · {info['phase']}"
        if info.get("note"):
            desc = f"{desc} · {info['note']}"

        self._progress.update(
            self._overall,
            completed=current,
            total=total if total and total > 0 else None,
            description=desc,
            rate=rate_text,
        )

        account = info.get("account") or "—"
        acc_done = info.get("account_done")
        acc_total = info.get("account_total")
        acc_desc = f"当前账号: {account}"
        if acc_done is not None and acc_total is not None:
            acc_desc = f"当前账号: {account}（本号 {acc_done}/{acc_total}）"
        elif acc_done is not None:
            acc_desc = f"当前账号: {account}（本号已处理 {acc_done}）"

        self._progress.update(
            self._account,
            completed=acc_done if acc_done is not None else current,
            total=acc_total if acc_total and acc_total > 0 else None,
            description=acc_desc,
            rate="",
        )

        if self.log_path and (
            current == total
            or current == 0
            or time.monotonic() - self._last_log >= 15
        ):
            self._write_log(current, total, info, rate)
            self._last_log = time.monotonic()

    def _write_log(
        self,
        current: int,
        total: int | None,
        info: dict[str, Any],
        rate: float,
    ) -> None:
        assert self.log_path is not None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": self.title,
            "current": current,
            "total": total,
            "rate_per_min": round(rate, 2),
            "ok": self._ok,
            "failed": self._failed,
            "deleted": self._deleted,
            **{k: v for k, v in info.items() if v is not None},
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def noop_progress(current: int, total: int | None = None, **_info: Any) -> None:
    return None
