from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import normalize_content_url
from wechat_archive.parsers.history_json import parse_history_payload
from wechat_archive.services.job_control import JobCancelled, cooperative_sleep
from wechat_archive.services.sessions import load_sessions
from wechat_archive.url_utils import article_sn, normalize_article_url


class SessionPool:
    def __init__(self, sessions: list[dict[str, Any]]):
        if not sessions:
            raise RuntimeError(
                "未找到可用微信会话。请复制 sessions/example_session.yaml "
                "为 session_1.yaml 并填入 uin/key/cookie。"
            )
        self.sessions = sessions
        self._i = 0

    def next(self) -> dict[str, Any]:
        s = self.sessions[self._i % len(self.sessions)]
        self._i += 1
        return s


class HistorySessionError(RuntimeError):
    """The current WeChat session is no longer usable."""


class HistoryRateLimited(RuntimeError):
    """WeChat asked the crawler to slow down."""


def fetch_history_for_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit_accounts: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """对已解析 biz 的账号拉取历史列表（需会话）。"""
    sessions = load_sessions(cfg["paths"]["sessions_dir"])
    pool = SessionPool(sessions)

    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]
    page_size = cfg["crawl"].get("history_page_size", 10)
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
                client=client,
                cfg=cfg,
                pool=pool,
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
    client: HttpClient,
    cfg: dict[str, Any],
    pool: SessionPool,
    acc: Any,
    start_ts: int,
    end_ts: int,
    page_size: int,
    checkpoint: Callable[[], None] | None,
) -> dict[str, int]:
    account_id = acc["id"]
    biz = acc["biz"]
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
    try:
        while True:
            if checkpoint:
                checkpoint()
            payload = _request_history_with_failover(
                client=client,
                biz=biz,
                offset=offset,
                count=page_size,
                pool=pool,
                rate_limit_retries=int(
                    cfg["crawl"].get("history_rate_limit_retries", 2)
                ),
                rate_limit_cooldown=float(
                    cfg["crawl"].get("history_rate_limit_cooldown", 300)
                ),
                checkpoint=checkpoint,
            )
            rows, can_continue = parse_history_payload(payload)

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
                    sn = article_sn(content_url)
                    query = parse_qs(urlparse(url).query)
                    mid = query.get("mid", [None])[0]
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
                            biz,
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


def _is_session_error(msg: str) -> bool:
    keys = ("invalid session", "失效", "过期", "登录", "session expired")
    return any(k in msg.lower() or k in msg for k in keys)


def _is_rate_limit_error(msg: str) -> bool:
    keys = ("频繁", "rate limit", "too many", "验证身份", "ret=-3", "ret=200013")
    lowered = msg.lower()
    return any(k in lowered or k in msg for k in keys)


def _request_history_with_failover(
    client: HttpClient,
    biz: str,
    offset: int,
    count: int,
    pool: SessionPool,
    rate_limit_retries: int = 2,
    rate_limit_cooldown: float = 300,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    for _ in range(len(pool.sessions)):
        session = pool.next()
        for rate_attempt in range(rate_limit_retries + 1):
            try:
                return _request_history(
                    client=client,
                    biz=biz,
                    offset=offset,
                    count=count,
                    session=session,
                )
            except HistoryRateLimited:
                if rate_attempt >= rate_limit_retries:
                    raise
                cooperative_sleep(rate_limit_cooldown, checkpoint)
            except HistorySessionError as exc:
                errors.append(f"{session.get('name', 'session')}: {str(exc)[:200]}")
                break
            except Exception as exc:
                message = str(exc)
                if _is_rate_limit_error(message):
                    if rate_attempt >= rate_limit_retries:
                        raise HistoryRateLimited(message) from exc
                    cooperative_sleep(rate_limit_cooldown, checkpoint)
                    continue
                if _is_session_error(message):
                    errors.append(
                        f"{session.get('name', 'session')}: {message[:200]}"
                    )
                    break
                raise
    raise HistorySessionError(
        "all sessions failed for history page: " + " | ".join(errors)
    )


def _request_history(
    client: HttpClient,
    biz: str,
    offset: int,
    count: int,
    session: dict[str, Any],
) -> dict[str, Any]:
    params = {
        "action": "getmsg",
        "__biz": biz,
        "f": "json",
        "offset": str(offset),
        "count": str(count),
        "uin": session["uin"],
        "key": session["key"],
    }
    headers = {
        "Cookie": session["cookie"],
        "Referer": f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={biz}&scene=124#wechat_redirect",
    }
    resp = client.get(
        "https://mp.weixin.qq.com/mp/profile_ext",
        params=params,
        headers=headers,
    )
    if getattr(resp, "status_code", None) == 429:
        raise HistoryRateLimited("history endpoint returned HTTP 429")
    resp.raise_for_status()
    try:
        payload = resp.json()
    except Exception:
        # 有时返回 HTML
        text = resp.text
        if _is_rate_limit_error(text):
            raise HistoryRateLimited(f"history rate limited: {text[:300]}")
        if _is_session_error(text):
            raise HistorySessionError(f"history session invalid: {text[:300]}")
        raise RuntimeError(f"history non-json response: {text[:300]}")
    err = (payload.get("base_resp") or {}).get("err_msg") or payload.get("errmsg")
    ret = (payload.get("base_resp") or {}).get("ret")
    if ret not in (None, 0, "0"):
        message = f"history api error ret={ret} msg={err}"
        if _is_rate_limit_error(message):
            raise HistoryRateLimited(message)
        if _is_session_error(message):
            raise HistorySessionError(message)
        raise RuntimeError(message)
    return payload
