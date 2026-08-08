from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.services.job_control import cooperative_sleep
from wechat_archive.url_utils import article_sn, normalize_article_url


class ContentRateLimited(RuntimeError):
    """The public article endpoint asked the crawler to slow down."""


class AdaptiveDelay:
    """成功时略降延迟，限流时抬升，避免长期固定慢速。"""

    def __init__(self, sleep_min: float, sleep_max: float):
        self.min_d = max(0.0, float(sleep_min))
        self.max_d = max(self.min_d, float(sleep_max))
        self.current = (self.min_d + self.max_d) / 2.0 if self.max_d > 0 else 0.0
        self._success_streak = 0
        self._lock = threading.Lock()

    def next_delay(self) -> float:
        with self._lock:
            if self.current <= 0:
                return 0.0
            lo = max(self.min_d, self.current * 0.75)
            hi = max(lo, min(self.max_d * 1.25, self.current * 1.25))
            return random.uniform(lo, hi)

    def on_success(self) -> None:
        with self._lock:
            self._success_streak += 1
            if self._success_streak >= 25 and self.current > self.min_d:
                self.current = max(self.min_d, self.current * 0.92)
                self._success_streak = 0

    def on_rate_limit(self) -> None:
        with self._lock:
            self._success_streak = 0
            boosted = self.current * 1.7 if self.current > 0 else max(2.0, self.max_d)
            self.current = min(max(self.max_d, 8.0), boosted)


def fetch_pending_contents(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[..., None] | None = None,
) -> dict[str, int]:
    """Fetch listed/retryable content with bounded concurrency and adaptive delay."""
    crawl = cfg["crawl"]
    sleep_min = float(crawl.get("content_sleep_min", crawl.get("sleep_min", 2)))
    sleep_max = float(crawl.get("content_sleep_max", crawl.get("sleep_max", 4)))
    concurrency = max(1, int(crawl.get("content_concurrency", 2)))
    start = datetime.strptime(crawl["start_date"], "%Y-%m-%d")
    end = datetime.strptime(crawl["end_date"], "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    max_retries = int(crawl.get("content_max_retries", 3))
    # 并发时用较小批次，降低整批打满后才发现限流的浪费
    configured_batch = max(1, int(crawl.get("content_batch_size", 500)))
    batch_size = (
        min(configured_batch, max(concurrency * 8, concurrency))
        if concurrency > 1
        else configured_batch
    )
    rate_limit_cooldown = max(1, int(crawl.get("content_rate_limit_cooldown", 600)))
    breaker_threshold = max(
        1, int(crawl.get("content_circuit_breaker_threshold", 5))
    )
    consecutive_rate_limits = 0
    circuit_open = False

    with db.connection() as conn:
        skipped = conn.execute(
            """
            UPDATE articles
            SET status='out_of_range', updated_at=datetime('now','localtime')
            WHERE status IN ('listed', 'retry_wait')
              AND publish_ts IS NOT NULL
              AND (publish_ts < ? OR publish_ts > ?)
            """,
            (start_ts, end_ts),
        ).rowcount

    count_row = db.fetchone(
        """
        SELECT COUNT(*) AS count
        FROM articles
        WHERE status IN ('listed', 'retry_wait')
          AND (next_retry_at IS NULL OR next_retry_at <= datetime('now','localtime'))
          AND url IS NOT NULL AND url != ''
          AND (publish_ts IS NULL OR publish_ts BETWEEN ? AND ?)
        """,
        (start_ts, end_ts),
    )
    eligible_total = int(count_row["count"]) if count_row else 0
    target_total = min(eligible_total, limit) if limit is not None else eligible_total
    if progress:
        progress(
            0,
            target_total,
            phase="正文",
            note=f"并发={concurrency}, 间隔≈{sleep_min}-{sleep_max}s",
        )

    ok = deleted = failed = processed = 0
    delay = AdaptiveDelay(sleep_min, sleep_max)
    account_targets = _account_pending_counts(db, start_ts, end_ts)
    account_done: dict[int, int] = {}
    # requests.Session 连接池可并发；共用 client 保证测试桩与配置代理一致
    shared_client = client

    while processed < target_total:
        current_batch_size = min(batch_size, target_total - processed)
        rows = db.fetchall(
            """
            SELECT ar.id, ar.url, ar.publish_ts, ar.retry_count, ar.account_id,
                   COALESCE(a.account_name, a.nickname_input, '') AS account_name
            FROM articles ar
            JOIN accounts a ON a.id = ar.account_id
            WHERE ar.status IN ('listed', 'retry_wait')
              AND (ar.next_retry_at IS NULL OR ar.next_retry_at <= datetime('now','localtime'))
              AND ar.url IS NOT NULL AND ar.url != ''
              AND (ar.publish_ts IS NULL OR ar.publish_ts BETWEEN ? AND ?)
            ORDER BY ar.account_id, ar.id
            LIMIT ?
            """,
            (start_ts, end_ts, current_batch_size),
        )
        if not rows:
            break

        def handle_one(row: Any) -> dict[str, Any]:
            if checkpoint:
                checkpoint()
            article_id = int(row["id"])
            url = row["url"]
            publish_ts = row["publish_ts"]
            account_id = int(row["account_id"])
            account_name = row["account_name"] or f"#{account_id}"
            parsed: dict[str, Any] | None = None
            last_error: Exception | None = None
            rate_limited = False
            attempts_left = max(1, max_retries - int(row["retry_count"]))
            for attempt in range(attempts_left):
                if checkpoint:
                    checkpoint()
                try:
                    wait = delay.next_delay()
                    if wait > 0:
                        cooperative_sleep(wait, checkpoint, slice_seconds=1.0)
                    final_url, html_text = shared_client.get_text(url)
                    parsed = parse_article_html(html_text, final_url)
                    if parsed["status"] == "failed":
                        error = parsed.get("error") or "article parse failed"
                        if error == "rate_limited":
                            raise ContentRateLimited(error)
                        raise RuntimeError(error)
                    delay.on_success()
                    break
                except ContentRateLimited as exc:
                    last_error = exc
                    rate_limited = True
                    delay.on_rate_limit()
                    break
                except Exception as exc:
                    if "rate_limited" in str(exc).lower():
                        last_error = ContentRateLimited(str(exc))
                        rate_limited = True
                        delay.on_rate_limit()
                        break
                    last_error = exc
                    if attempt < attempts_left - 1:
                        backoff = min(60.0, (2**attempt) + random.random())
                        cooperative_sleep(backoff, checkpoint, slice_seconds=1.0)
            return {
                "article_id": article_id,
                "url": url,
                "account_id": account_id,
                "account_name": account_name,
                "publish_ts": publish_ts,
                "retry_count": int(row["retry_count"]),
                "attempts_left": attempts_left,
                "rate_limited": rate_limited,
                "parsed": parsed,
                "error": last_error,
            }

        if concurrency == 1:
            outcomes = [handle_one(row) for row in rows]
        else:
            outcomes = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(handle_one, row) for row in rows]
                for fut in as_completed(futures):
                    outcomes.append(fut.result())
            outcomes.sort(key=lambda x: (x["account_id"], x["article_id"]))

        hit_rate_limit = False
        batch_rate_limits = 0
        for outcome in outcomes:
            if checkpoint:
                checkpoint()
            article_id = outcome["article_id"]
            account_id = outcome["account_id"]
            account_name = outcome["account_name"]
            publish_ts = outcome["publish_ts"]
            parsed = outcome["parsed"]
            last_error = outcome["error"]
            url = outcome["url"]

            if outcome["rate_limited"]:
                hit_rate_limit = True
                batch_rate_limits += 1
                consecutive_rate_limits += 1
                retry_count = int(outcome["retry_count"]) + 1
                db.execute(
                    """
                    UPDATE articles
                    SET status='retry_wait', content_error='rate_limited',
                        retry_count=?,
                        next_retry_at=datetime('now', '+' || ? || ' seconds', 'localtime'),
                        last_fetched_at=datetime('now','localtime'),
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (retry_count, rate_limit_cooldown, article_id),
                )
                failed += 1
                processed += 1
                account_done[account_id] = account_done.get(account_id, 0) + 1
                if progress:
                    progress(
                        processed,
                        target_total,
                        phase="正文",
                        account=account_name,
                        account_done=account_done[account_id],
                        account_total=account_targets.get(account_id),
                        ok=ok,
                        failed=failed,
                        deleted=deleted,
                        note=(
                            f"触发限流 ({consecutive_rate_limits}/{breaker_threshold})，"
                            f"将冷却 {rate_limit_cooldown}s"
                        ),
                    )
                if consecutive_rate_limits >= breaker_threshold:
                    circuit_open = True
                    break
                continue

            try:
                if parsed is None:
                    raise last_error or RuntimeError("article fetch failed")
                status = parsed["status"]
                if status == "deleted":
                    deleted += 1
                else:
                    pts = parsed.get("publish_ts") or publish_ts
                    if pts is not None and (pts < start_ts or pts > end_ts):
                        status = "out_of_range"
                        skipped += 1
                    else:
                        ok += 1

                parsed_url = parsed.get("url") or url
                sn = parsed.get("sn") or article_sn(parsed_url)
                db.execute(
                    """
                    UPDATE articles
                    SET sn=COALESCE(?, sn), mid=COALESCE(?, mid),
                        idx=COALESCE(?, idx), biz=COALESCE(?, biz),
                        title=COALESCE(?, title), author=COALESCE(?, author),
                        digest=COALESCE(?, digest), cover_url=COALESCE(?, cover_url),
                        publish_ts=COALESCE(?, publish_ts),
                        publish_time=COALESCE(?, publish_time),
                        url=COALESCE(?, url),
                        normalized_url=COALESCE(?, normalized_url),
                        content_html=?, content_text=?, status=?,
                        content_error=?, retry_count=0, next_retry_at=NULL,
                        raw_list_json=NULL,
                        last_fetched_at=datetime('now','localtime'),
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (
                        sn,
                        parsed.get("mid"),
                        parsed.get("idx"),
                        parsed.get("biz"),
                        parsed.get("title"),
                        parsed.get("author"),
                        parsed.get("digest"),
                        parsed.get("cover_url"),
                        parsed.get("publish_ts"),
                        parsed.get("publish_time"),
                        parsed.get("url"),
                        normalize_article_url(parsed_url),
                        parsed.get("content_html"),
                        parsed.get("content_text"),
                        status,
                        parsed.get("error"),
                        article_id,
                    ),
                )
            except Exception as e:
                retry_count = int(outcome["retry_count"]) + int(outcome["attempts_left"])
                retryable = retry_count < max_retries
                db.execute(
                    """
                    UPDATE articles
                    SET status=?, content_error=?, retry_count=?,
                        next_retry_at=CASE WHEN ? THEN datetime(
                            'now', '+' || MIN(3600, 30 * (1 << MIN(6, ?))) || ' seconds',
                            'localtime'
                        ) ELSE NULL END,
                        last_fetched_at=datetime('now','localtime'),
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (
                        "retry_wait" if retryable else "failed",
                        str(e)[:500],
                        retry_count,
                        int(retryable),
                        retry_count,
                        article_id,
                    ),
                )
                failed += 1

            processed += 1
            account_done[account_id] = account_done.get(account_id, 0) + 1
            if progress:
                progress(
                    processed,
                    target_total,
                    phase="正文",
                    account=account_name,
                    account_done=account_done[account_id],
                    account_total=account_targets.get(account_id),
                    ok=ok,
                    failed=failed,
                    deleted=deleted,
                )

        if batch_rate_limits == 0:
            consecutive_rate_limits = 0
        if circuit_open:
            note = (
                f"正文连续限流 {consecutive_rate_limits} 次，已熔断暂停。"
                f"建议稍后重跑 content"
            )
            if progress:
                progress(
                    processed,
                    target_total,
                    phase="正文",
                    note=note,
                    ok=ok,
                    failed=failed,
                    deleted=deleted,
                )
            cooperative_sleep(rate_limit_cooldown, checkpoint, slice_seconds=5.0)
            break
        if hit_rate_limit and processed < target_total:
            if progress:
                progress(
                    processed,
                    target_total,
                    phase="正文",
                    note=f"限流冷却 {rate_limit_cooldown}s",
                    ok=ok,
                    failed=failed,
                    deleted=deleted,
                )
            cooperative_sleep(rate_limit_cooldown, checkpoint, slice_seconds=5.0)

    return {
        "ok": ok,
        "deleted": deleted,
        "failed": failed,
        "out_of_range": skipped,
        "total": processed + skipped,
        "concurrency": concurrency,
        "content_sleep": [sleep_min, sleep_max],
        "circuit_open": circuit_open,
    }


def _account_pending_counts(
    db: Database, start_ts: int, end_ts: int
) -> dict[int, int]:
    rows = db.fetchall(
        """
        SELECT account_id, COUNT(*) AS count
        FROM articles
        WHERE status IN ('listed', 'retry_wait')
          AND (next_retry_at IS NULL OR next_retry_at <= datetime('now','localtime'))
          AND url IS NOT NULL AND url != ''
          AND (publish_ts IS NULL OR publish_ts BETWEEN ? AND ?)
        GROUP BY account_id
        """,
        (start_ts, end_ts),
    )
    return {int(r["account_id"]): int(r["count"]) for r in rows}
