from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import normalize_content_url
from wechat_archive.platform_client import (
    PlatformAPIError,
    PlatformAuthError,
    PlatformRateLimited,
    build_history_client,
)
from wechat_archive.services.job_control import JobCancelled, cooperative_sleep
from wechat_archive.url_utils import article_sn, normalize_article_url


class HistorySessionError(RuntimeError):
    """公众平台登录态不可用。"""


class HistoryRateLimited(RuntimeError):
    """公众平台限流。"""


def fetch_history_for_accounts(
    db: Database,
    client: HttpClient | None,
    cfg: dict[str, Any],
    limit_accounts: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """对已解析 fakeid/biz 的账号拉取历史列表（公众平台 appmsgpublish）。"""
    # client 参数保留兼容旧签名；平台模式使用独立 HTTP 会话
    _ = client
    platform = build_history_client(cfg)

    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]
    page_size = int(cfg["crawl"].get("history_page_size", 20))
    page_size = min(max(page_size, 1), 100)
    start = datetime.strptime(cfg["crawl"]["start_date"], "%Y-%m-%d")
    end = datetime.strptime(cfg["crawl"]["end_date"], "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    account_batch_size = max(1, int(cfg["crawl"].get("account_batch_size", 200)))
    total_row = db.fetchone(
        """
        SELECT COUNT(*) AS count
        FROM accounts
        WHERE resolve_status='ok' AND biz IS NOT NULL
          AND list_status IN ('pending', 'running', 'done', 'failed', 'need_session')
        """
    )
    accounts_total = int(total_row["count"]) if total_row else 0
    if limit_accounts is not None:
        accounts_total = min(accounts_total, limit_accounts)

    account_ok = account_fail = articles_added = 0
    last_account_id = 0
    processed_accounts = 0
    if progress:
        progress(0, accounts_total)

    while processed_accounts < accounts_total:
        remaining = accounts_total - processed_accounts
        accounts = db.fetchall(
            """
            SELECT id, biz, account_name, nickname_input, list_status
            FROM accounts
            WHERE resolve_status='ok' AND biz IS NOT NULL
              AND list_status IN ('pending', 'running', 'done', 'failed', 'need_session')
              AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (last_account_id, min(account_batch_size, remaining)),
        )
        if not accounts:
            break
        for acc in accounts:
            if checkpoint:
                checkpoint()
            account_id = acc["id"]
            last_account_id = account_id
            processed_accounts += 1
            result = _fetch_history_for_account(
                db=db,
                platform=platform,
                cfg=cfg,
                acc=acc,
                start_ts=start_ts,
                end_ts=end_ts,
                page_size=page_size,
                checkpoint=checkpoint,
            )
            account_ok += result["accounts_ok"]
            account_fail += result["accounts_fail"]
            articles_added += result["articles_upserted"]
            if progress:
                progress(processed_accounts, accounts_total)
            if processed_accounts < accounts_total:
                cooperative_sleep(
                    random.uniform(sleep_min, sleep_max), checkpoint
                )

    return {
        "accounts_ok": account_ok,
        "accounts_fail": account_fail,
        "articles_upserted": articles_added,
        "accounts_total": processed_accounts,
    }


def _fetch_history_for_account(
    db: Database,
    platform: Any,
    cfg: dict[str, Any],
    acc: Any,
    start_ts: int,
    end_ts: int,
    page_size: int,
    checkpoint: Callable[[], None] | None,
) -> dict[str, int]:
    account_id = acc["id"]
    fakeid = acc["biz"]
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET list_status='running', list_error=NULL,
                updated_at=datetime('now','localtime')
            WHERE id=?
            """,
            (account_id,),
        )

    cp_row = db.fetchone(
        "SELECT history_offset, newest_publish_ts FROM crawl_checkpoints "
        "WHERE account_id=?",
        (account_id,),
    )
    previous_watermark = cp_row["newest_publish_ts"] if cp_row else None
    offset = (
        cp_row["history_offset"]
        if cp_row and acc["list_status"] != "done"
        else 0
    )
    newest_seen = previous_watermark
    reached_old = False
    articles_added = 0
    rate_limit_retries = int(cfg["crawl"].get("history_rate_limit_retries", 2))
    rate_limit_cooldown = float(cfg["crawl"].get("history_rate_limit_cooldown", 300))

    try:
        while True:
            if checkpoint:
                checkpoint()
            page = _request_history_page(
                platform=platform,
                fakeid=fakeid,
                begin=offset,
                count=page_size,
                rate_limit_retries=rate_limit_retries,
                rate_limit_cooldown=rate_limit_cooldown,
                checkpoint=checkpoint,
            )
            rows = page.get("articles") or []
            can_continue = bool(page.get("can_continue"))

            with db.connection() as conn:
                for row in rows:
                    pts = row.get("publish_ts")
                    if pts is not None and pts < start_ts:
                        reached_old = True
                        continue
                    if (
                        previous_watermark is not None
                        and pts is not None
                        and pts <= previous_watermark
                    ):
                        reached_old = True
                        continue
                    if pts is not None:
                        newest_seen = max(newest_seen or pts, pts)
                    status = (
                        "out_of_range"
                        if pts is not None and pts > end_ts
                        else "listed"
                    )
                    url = row.get("url")
                    if not url:
                        continue
                    content_url = normalize_content_url(url)
                    normalized_url = normalize_article_url(content_url)
                    sn = article_sn(content_url) or row.get("aid")
                    query = parse_qs(urlparse(url).query)
                    mid = row.get("mid") or query.get("mid", [None])[0]
                    cursor = conn.execute(
                        """
                        INSERT INTO articles (
                            account_id, biz, sn, mid, idx,
                            title, author, digest, cover_url, url,
                            normalized_url, publish_ts, publish_time, status, raw_list_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET
                            title=COALESCE(excluded.title, articles.title),
                            author=COALESCE(excluded.author, articles.author),
                            digest=COALESCE(excluded.digest, articles.digest),
                            cover_url=COALESCE(excluded.cover_url, articles.cover_url),
                            normalized_url=COALESCE(excluded.normalized_url, articles.normalized_url),
                            publish_ts=COALESCE(excluded.publish_ts, articles.publish_ts),
                            publish_time=COALESCE(excluded.publish_time, articles.publish_time),
                            updated_at=CASE
                                WHEN excluded.title IS NOT articles.title
                                  OR excluded.author IS NOT articles.author
                                  OR excluded.digest IS NOT articles.digest
                                  OR excluded.cover_url IS NOT articles.cover_url
                                  OR excluded.publish_ts IS NOT articles.publish_ts
                                THEN datetime('now','localtime')
                                ELSE articles.updated_at
                            END
                        """,
                        (
                            account_id,
                            fakeid,
                            sn,
                            mid,
                            row.get("idx"),
                            row.get("title"),
                            row.get("author"),
                            row.get("digest"),
                            row.get("cover_url"),
                            content_url,
                            normalized_url,
                            pts,
                            row.get("publish_time"),
                            status,
                            row.get("raw_list_json"),
                        ),
                    )
                    if cursor.rowcount:
                        articles_added += 1
                conn.execute(
                    """
                    INSERT INTO crawl_checkpoints (
                        account_id, history_offset, newest_publish_ts, updated_at
                    ) VALUES (?, ?, ?, datetime('now','localtime'))
                    ON CONFLICT(account_id) DO UPDATE SET
                        history_offset=excluded.history_offset,
                        newest_publish_ts=MAX(
                            COALESCE(crawl_checkpoints.newest_publish_ts, 0),
                            COALESCE(excluded.newest_publish_ts, 0)
                        ),
                        updated_at=datetime('now','localtime')
                    """,
                    (account_id, offset + page_size, newest_seen),
                )

            if reached_old or not can_continue or not rows:
                break
            offset += page_size
            cooperative_sleep(random.uniform(sleep_min, sleep_max), checkpoint)

        with db.connection() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET list_status='done', list_error=NULL,
                    last_listed_at=datetime('now','localtime'),
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (account_id,),
            )
            conn.execute(
                "UPDATE crawl_checkpoints SET history_offset=0 WHERE account_id=?",
                (account_id,),
            )
        return {
            "accounts_ok": 1,
            "accounts_fail": 0,
            "articles_upserted": articles_added,
        }
    except JobCancelled:
        raise
    except HistoryRateLimited as e:
        db.execute(
            """
            UPDATE accounts SET list_status='failed', list_error=?,
                updated_at=datetime('now','localtime') WHERE id=?
            """,
            (str(e)[:500], account_id),
        )
        raise
    except HistorySessionError as e:
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE accounts SET list_status='need_session', list_error=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (str(e)[:500], account_id),
            )
        return {
            "accounts_ok": 0,
            "accounts_fail": 1,
            "articles_upserted": articles_added,
        }
    except Exception as e:
        msg = str(e)
        status = "need_session" if _is_session_error(msg) else "failed"
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE accounts SET list_status=?, list_error=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (status, msg[:500], account_id),
            )
        return {
            "accounts_ok": 0,
            "accounts_fail": 1,
            "articles_upserted": articles_added,
        }


def _request_history_page(
    platform: Any,
    fakeid: str,
    begin: int,
    count: int,
    rate_limit_retries: int,
    rate_limit_cooldown: float,
    checkpoint: Callable[[], None] | None,
) -> dict[str, Any]:
    for rate_attempt in range(rate_limit_retries + 1):
        try:
            return platform.list_articles(fakeid=fakeid, begin=begin, count=count)
        except PlatformRateLimited as exc:
            if rate_attempt >= rate_limit_retries:
                raise HistoryRateLimited(str(exc)) from exc
            cooperative_sleep(rate_limit_cooldown, checkpoint)
        except PlatformAuthError as exc:
            raise HistorySessionError(str(exc)) from exc
        except PlatformAPIError:
            raise
    raise HistoryRateLimited("history rate limited")


def _is_session_error(msg: str) -> bool:
    keys = (
        "invalid session",
        "失效",
        "过期",
        "登录",
        "login",
        "expired",
        "session expired",
        "未配置公众平台",
    )
    lowered = msg.lower()
    return any(k in lowered or k in msg for k in keys)
