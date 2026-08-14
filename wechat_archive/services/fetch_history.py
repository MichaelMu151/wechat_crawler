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

# Default queue: unfinished work only. Use refresh=True to also probe done accounts.
_ACTIVE_LIST_STATUSES = ("pending", "running", "failed", "need_session", "retry_wait")
_REFRESH_LIST_STATUSES = _ACTIVE_LIST_STATUSES + ("done",)


class HistorySessionError(RuntimeError):
    """公众平台登录态不可用。"""


class HistoryRateLimited(RuntimeError):
    """公众平台限流。"""


class HistoryCircuitOpen(RuntimeError):
    """连续频控触发全局熔断，停止继续遍历账号。"""


def _list_status_clause(refresh: bool) -> str:
    statuses = _REFRESH_LIST_STATUSES if refresh else _ACTIVE_LIST_STATUSES
    return ", ".join(f"'{s}'" for s in statuses)


def _normalize_list_error(kind: str, message: str) -> str:
    prefix = {
        "rate_limited": "rate_limited:",
        "auth": "auth:",
        "api": "api:",
        "interrupted": "interrupted:",
    }.get(kind, "error:")
    body = message.strip()
    if body.startswith(prefix):
        return body[:500]
    return f"{prefix} {body}"[:500]


def fetch_history_for_accounts(
    db: Database,
    client: HttpClient | None,
    cfg: dict[str, Any],
    limit_accounts: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[..., None] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """对已解析 fakeid/biz 的账号拉取历史列表（公众平台 appmsgpublish）。"""
    _ = client
    # CLI/history start: reclaim running left by killed processes (stale_minutes=0).
    # serve/job path uses timed recovery separately.
    reclaim_all = bool(cfg["crawl"].get("history_reclaim_running_on_start", True))
    recovered = db.recover_stale_work(
        stale_minutes=0
        if reclaim_all
        else int(cfg["crawl"].get("history_stale_running_minutes", 30))
    )

    platform = build_history_client(cfg)
    backend = (cfg.get("platform") or {}).get("backend", "platform")
    schinza_backend = backend == "schinza_getmsg"

    sleep_min = float(cfg["crawl"]["sleep_min"])
    sleep_max = float(cfg["crawl"]["sleep_max"])
    base_page_size = min(max(int(cfg["crawl"].get("history_page_size", 20)), 1), 100)
    page_size = base_page_size
    page_size_min = min(max(int(cfg["crawl"].get("history_page_size_min", 5)), 1), page_size)
    page_size_max = min(
        max(int(cfg["crawl"].get("history_page_size_max", base_page_size)), page_size),
        100,
    )
    start = datetime.strptime(cfg["crawl"]["start_date"], "%Y-%m-%d")
    end = datetime.strptime(cfg["crawl"]["end_date"], "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    breaker_threshold = max(
        1, int(cfg["crawl"].get("history_circuit_breaker_threshold", 3))
    )
    global_cooldown = max(
        0, int(cfg["crawl"].get("history_global_cooldown", 3600))
    )
    status_sql = _list_status_clause(refresh)

    account_batch_size = max(1, int(cfg["crawl"].get("account_batch_size", 200)))
    identity_filter = (
        "history_backend='schinza_getmsg' AND wechat_biz IS NOT NULL"
        if schinza_backend
        else "resolve_status='ok' AND biz IS NOT NULL"
    )
    total_row = db.fetchone(
        f"""
        SELECT COUNT(*) AS count
        FROM accounts
        WHERE {identity_filter}
          AND list_status IN ({status_sql})
        """
    )
    accounts_total = int(total_row["count"]) if total_row else 0
    if limit_accounts is not None:
        accounts_total = min(accounts_total, limit_accounts)

    account_ok = account_fail = articles_added = 0
    last_account_id = 0
    processed_accounts = 0
    consecutive_rate_limits = 0
    success_streak = 0
    circuit_open = False
    circuit_message = ""

    if progress:
        mode = "含 done 增量刷新" if refresh else "跳过已完成"
        progress(
            0,
            accounts_total,
            phase="历史列表",
            note=(
                f"{mode} · 每页 {page_size} · 间隔 {sleep_min}-{sleep_max}s"
                + (f" · 已回收 running {recovered.get('accounts', 0)}" if recovered.get("accounts") else "")
            ),
        )

    try:
        while processed_accounts < accounts_total:
            remaining = accounts_total - processed_accounts
            accounts = db.fetchall(
                f"""
                SELECT id,
                       {'wechat_biz' if schinza_backend else 'biz'} AS biz,
                       account_name, nickname_input, list_status
                FROM accounts
                WHERE {identity_filter}
                  AND list_status IN ({status_sql})
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
                account_name = (
                    acc["account_name"] or acc["nickname_input"] or f"#{account_id}"
                )
                if progress:
                    progress(
                        processed_accounts,
                        accounts_total,
                        phase="历史列表",
                        account=account_name,
                        account_done=0,
                        account_total=None,
                        ok=account_ok,
                        failed=account_fail,
                        note=f"累计入库 {articles_added} · 页大小 {page_size}",
                    )
                result = _fetch_history_for_account(
                    db=db,
                    platform=platform,
                    cfg=cfg,
                    acc=acc,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    page_size=page_size,
                    checkpoint=checkpoint,
                    progress=progress,
                    overall_current=processed_accounts,
                    overall_total=accounts_total,
                    overall_ok=account_ok,
                    overall_fail=account_fail,
                    overall_articles=articles_added,
                )
                processed_accounts += 1
                account_ok += result["accounts_ok"]
                account_fail += result["accounts_fail"]
                articles_added += result["articles_upserted"]

                if result.get("rate_limited"):
                    consecutive_rate_limits += 1
                    success_streak = 0
                    page_size = page_size_min
                elif result["accounts_ok"]:
                    consecutive_rate_limits = 0
                    success_streak += 1
                    if success_streak >= 5 and page_size < page_size_max:
                        page_size = min(page_size_max, page_size + 5)

                if progress:
                    note = f"累计入库 {articles_added}"
                    if consecutive_rate_limits:
                        note += f" · 连续频控 {consecutive_rate_limits}/{breaker_threshold}"
                    progress(
                        processed_accounts,
                        accounts_total,
                        phase="历史列表",
                        account=account_name,
                        account_done=result["articles_upserted"],
                        account_total=result["articles_upserted"] or None,
                        ok=account_ok,
                        failed=account_fail,
                        note=note,
                    )

                if consecutive_rate_limits >= breaker_threshold:
                    mins = max(1, global_cooldown // 60)
                    circuit_message = (
                        f"连续频控 {consecutive_rate_limits} 次，已熔断。"
                        f"建议等待约 {mins} 分钟后再运行 history"
                    )
                    circuit_open = True
                    if progress:
                        progress(
                            processed_accounts,
                            accounts_total,
                            phase="历史列表",
                            note=circuit_message,
                            ok=account_ok,
                            failed=account_fail,
                        )
                    if global_cooldown > 0:
                        cooperative_sleep(float(global_cooldown), checkpoint)
                    raise HistoryCircuitOpen(circuit_message)

                if result.get("rate_limited"):
                    cooperative_sleep(
                        float(cfg["crawl"].get("history_rate_limit_cooldown", 900)),
                        checkpoint,
                    )
                elif processed_accounts < accounts_total:
                    cooperative_sleep(
                        random.uniform(sleep_min, sleep_max), checkpoint
                    )
    except HistoryCircuitOpen:
        pass

    out: dict[str, Any] = {
        "accounts_ok": account_ok,
        "accounts_fail": account_fail,
        "articles_upserted": articles_added,
        "accounts_total": processed_accounts,
        "refresh": refresh,
        "circuit_open": circuit_open,
        "recovered_running": int(recovered.get("accounts") or 0),
        "page_size_final": page_size,
    }
    if circuit_open:
        out["paused_rate_limit"] = True
        out["circuit_message"] = circuit_message
        out["suggested_wait_seconds"] = global_cooldown
    return out


def _fetch_history_for_account(
    db: Database,
    platform: Any,
    cfg: dict[str, Any],
    acc: Any,
    start_ts: int,
    end_ts: int,
    page_size: int,
    checkpoint: Callable[[], None] | None,
    progress: Callable[..., None] | None = None,
    overall_current: int = 0,
    overall_total: int | None = None,
    overall_ok: int = 0,
    overall_fail: int = 0,
    overall_articles: int = 0,
) -> dict[str, Any]:
    account_id = acc["id"]
    fakeid = acc["biz"]
    account_name = acc["account_name"] or acc["nickname_input"] or f"#{account_id}"
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
        if cp_row and acc["list_status"] not in ("done",)
        else 0
    )
    newest_seen = previous_watermark
    reached_old = False
    articles_added = 0
    pages_done = 0
    backend = (cfg.get("platform") or {}).get("backend", "platform")
    rate_limit_retries = (
        0
        if backend == "schinza_getmsg"
        else int(cfg["crawl"].get("history_rate_limit_retries", 2))
    )
    rate_limit_cooldown = float(cfg["crawl"].get("history_rate_limit_cooldown", 900))
    max_pages = max(1, int(cfg["crawl"].get("history_max_pages_per_account", 100)))
    hit_page_cap = False

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
            pages_done += 1

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
                    (
                        account_id,
                        int(page.get("next_begin") or (offset + page_size)),
                        newest_seen,
                    ),
                )

            if progress:
                progress(
                    overall_current,
                    overall_total,
                    phase="历史列表",
                    account=account_name,
                    account_done=articles_added,
                    account_total=None,
                    ok=overall_ok,
                    failed=overall_fail,
                    note=(
                        f"第 {pages_done} 页 · 本号入库 {articles_added} · "
                        f"累计 {overall_articles + articles_added}"
                    ),
                )

            no_pushes = not rows and int(page.get("publish_fetched") or 0) <= 0
            if reached_old or not can_continue or no_pushes:
                break
            if pages_done >= max_pages:
                hit_page_cap = True
                break
            offset = int(page.get("next_begin") or (offset + page_size))
            cooperative_sleep(random.uniform(sleep_min, sleep_max), checkpoint)

        if hit_page_cap:
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET list_status='retry_wait',
                        list_error='api: reached configured page cap; safe to resume',
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (account_id,),
                )
            return {
                "accounts_ok": 0,
                "accounts_fail": 0,
                "articles_upserted": articles_added,
                "partial": True,
            }

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
            UPDATE accounts SET list_status='retry_wait', list_error=?,
                updated_at=datetime('now','localtime') WHERE id=?
            """,
            (_normalize_list_error("rate_limited", str(e)), account_id),
        )
        return {
            "accounts_ok": 0,
            "accounts_fail": 1,
            "articles_upserted": articles_added,
            "rate_limited": True,
        }
    except HistorySessionError as e:
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE accounts SET list_status='need_session', list_error=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (_normalize_list_error("auth", str(e)), account_id),
            )
        return {
            "accounts_ok": 0,
            "accounts_fail": 1,
            "articles_upserted": articles_added,
        }
    except Exception as e:
        msg = str(e)
        if _is_session_error(msg):
            status = "need_session"
            err = _normalize_list_error("auth", msg)
        else:
            status = "failed"
            err = _normalize_list_error("api", msg)
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE accounts SET list_status=?, list_error=?,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (status, err, account_id),
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
