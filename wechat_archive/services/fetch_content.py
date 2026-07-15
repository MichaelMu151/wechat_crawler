from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Callable

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.services.job_control import cooperative_sleep
from wechat_archive.url_utils import article_sn, normalize_article_url


class ContentRateLimited(RuntimeError):
    """The public article endpoint asked the crawler to slow down."""


def fetch_pending_contents(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Fetch listed/retryable content with bounded, observable retries."""
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]
    start = datetime.strptime(cfg["crawl"]["start_date"], "%Y-%m-%d")
    end = datetime.strptime(cfg["crawl"]["end_date"], "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    max_retries = int(cfg["crawl"].get("content_max_retries", 3))
    batch_size = max(1, int(cfg["crawl"].get("content_batch_size", 500)))
    rate_limit_cooldown = max(
        1, int(cfg["crawl"].get("content_rate_limit_cooldown", 900))
    )

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
        progress(0, target_total)

    ok = deleted = failed = processed = 0
    while processed < target_total:
        current_batch_size = min(batch_size, target_total - processed)
        rows = db.fetchall(
            """
            SELECT id, url, publish_ts, retry_count
            FROM articles
            WHERE status IN ('listed', 'retry_wait')
              AND (next_retry_at IS NULL OR next_retry_at <= datetime('now','localtime'))
              AND url IS NOT NULL AND url != ''
              AND (publish_ts IS NULL OR publish_ts BETWEEN ? AND ?)
            ORDER BY id
            LIMIT ?
            """,
            (start_ts, end_ts, current_batch_size),
        )
        if not rows:
            break

        for row in rows:
            if checkpoint:
                checkpoint()
            article_id = row["id"]
            url = row["url"]
            publish_ts = row["publish_ts"]
            parsed: dict[str, Any] | None = None
            last_error: Exception | None = None
            rate_limited = False
            attempts_left = max(1, max_retries - int(row["retry_count"]))
            for attempt in range(attempts_left):
                if checkpoint:
                    checkpoint()
                try:
                    final_url, html_text = client.get_text(url)
                    parsed = parse_article_html(html_text, final_url)
                    if parsed["status"] == "failed":
                        error = parsed.get("error") or "article parse failed"
                        if error == "rate_limited":
                            raise ContentRateLimited(error)
                        raise RuntimeError(error)
                    break
                except ContentRateLimited as exc:
                    last_error = exc
                    rate_limited = True
                    break
                except Exception as exc:
                    if "rate_limited" in str(exc).lower():
                        last_error = ContentRateLimited(str(exc))
                        rate_limited = True
                        break
                    last_error = exc
                    if attempt < attempts_left - 1:
                        delay = min(60.0, (2**attempt) + random.random())
                        cooperative_sleep(delay, checkpoint)

            if rate_limited:
                retry_count = int(row["retry_count"]) + 1
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
                if progress:
                    progress(processed, target_total)
                cooperative_sleep(rate_limit_cooldown, checkpoint)
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
                with db.connection() as conn:
                    conn.execute(
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
                retry_count = int(row["retry_count"]) + attempts_left
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
            if progress:
                progress(processed, target_total)
            if processed < target_total:
                cooperative_sleep(random.uniform(sleep_min, sleep_max), checkpoint)

    return {
        "ok": ok,
        "deleted": deleted,
        "failed": failed,
        "out_of_range": skipped,
        "total": processed + skipped,
    }
