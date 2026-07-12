from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, Callable

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.url_utils import article_sn, normalize_article_url


def fetch_pending_contents(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
    checkpoint: Callable[[], None] | None = None,
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
    sql = """
        SELECT id, url, publish_ts, retry_count
        FROM articles
        WHERE status IN ('listed', 'retry_wait')
          AND (next_retry_at IS NULL OR next_retry_at <= datetime('now','localtime'))
          AND url IS NOT NULL AND url != ''
        ORDER BY id
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    rows = db.fetchall(sql, params)

    ok = deleted = failed = skipped = 0
    for i, row in enumerate(rows):
        if checkpoint:
            checkpoint()
        article_id = row["id"]
        url = row["url"]
        publish_ts = row["publish_ts"]

        # 时间窗过滤（若列表阶段已有时间）
        if publish_ts is not None and (publish_ts < start_ts or publish_ts > end_ts):
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE articles
                    SET status='out_of_range',
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (article_id,),
                )
            skipped += 1
            continue

        parsed: dict[str, Any] | None = None
        last_error: Exception | None = None
        attempts_left = max(1, max_retries - int(row["retry_count"]))
        for attempt in range(attempts_left):
            try:
                final_url, html_text = client.get_text(url)
                parsed = parse_article_html(html_text, final_url)
                if parsed["status"] == "failed":
                    raise RuntimeError(parsed.get("error") or "article parse failed")
                break
            except Exception as exc:
                last_error = exc
                if attempt < attempts_left - 1:
                    delay = min(60.0, (2**attempt) + random.random())
                    time.sleep(delay)

        try:
            if parsed is None:
                raise last_error or RuntimeError("article fetch failed")
            status = parsed["status"]
            if status == "deleted":
                deleted += 1
            elif status == "failed":
                failed += 1
            else:
                # 正文页时间优先
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
                    SET sn=COALESCE(?, sn),
                        mid=COALESCE(?, mid),
                        idx=COALESCE(?, idx),
                        biz=COALESCE(?, biz),
                        title=COALESCE(?, title),
                        author=COALESCE(?, author),
                        digest=COALESCE(?, digest),
                        cover_url=COALESCE(?, cover_url),
                        publish_ts=COALESCE(?, publish_ts),
                        publish_time=COALESCE(?, publish_time),
                        url=COALESCE(?, url),
                        normalized_url=COALESCE(?, normalized_url),
                        content_html=?,
                        content_text=?,
                        status=?,
                        content_error=?,
                        retry_count=0,
                        next_retry_at=NULL,
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
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE articles
                    SET status=?,
                        content_error=?,
                        retry_count=?,
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

        if i < len(rows) - 1:
            if checkpoint:
                checkpoint()
            time.sleep(random.uniform(sleep_min, sleep_max))

    return {
        "ok": ok,
        "deleted": deleted,
        "failed": failed,
        "out_of_range": skipped,
        "total": len(rows),
    }
