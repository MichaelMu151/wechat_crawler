from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import normalize_content_url
from wechat_archive.parsers.history_json import parse_history_payload
from wechat_archive.services.job_control import JobCancelled
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


def fetch_history_for_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit_accounts: int | None = None,
    checkpoint: Callable[[], None] | None = None,
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

    accounts = db.fetchall(
        """
        SELECT id, biz, account_name, nickname_input, list_status
        FROM accounts
        WHERE resolve_status='ok' AND biz IS NOT NULL
          AND list_status IN ('pending', 'running', 'done', 'failed', 'need_session')
        ORDER BY id
        """
    )
    if limit_accounts is not None:
        accounts = accounts[:limit_accounts]

    account_ok = account_fail = articles_added = 0

    for acc in accounts:
        if checkpoint:
            checkpoint()
        account_id = acc["id"]
        biz = acc["biz"]
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

        checkpoint = db.fetchone(
            "SELECT history_offset, newest_publish_ts FROM crawl_checkpoints "
            "WHERE account_id=?",
            (account_id,),
        )
        previous_watermark = checkpoint["newest_publish_ts"] if checkpoint else None
        offset = (
            checkpoint["history_offset"]
            if checkpoint and acc["list_status"] != "done"
            else 0
        )
        newest_seen = previous_watermark
        reached_old = False
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
                )
                rows, can_continue = parse_history_payload(payload)

                page_added = 0
                with db.connection() as conn:
                    for row in rows:
                        pts = row.get("publish_ts")
                        if pts is not None and pts < start_ts:
                            reached_old = True
                            continue
                        if (
                            previous_watermark is not None
                            and offset == 0
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
                        conn.execute(
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
                                updated_at=datetime('now','localtime')
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
                        page_added += 1
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
                time.sleep(random.uniform(sleep_min, sleep_max))

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
            account_ok += 1
        except JobCancelled:
            raise
        except Exception as e:
            msg = str(e)
            status = "need_session" if _is_session_error(msg) else "failed"
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET list_status=?,
                        list_error=?,
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (status, msg[:500], account_id),
                )
            account_fail += 1

        time.sleep(random.uniform(sleep_min, sleep_max))

    return {
        "accounts_ok": account_ok,
        "accounts_fail": account_fail,
        "articles_upserted": articles_added,
        "accounts_total": len(accounts),
    }


def _is_session_error(msg: str) -> bool:
    keys = ("invalid session", "失效", "过期", "频繁", "验证", "ret=")
    return any(k in msg.lower() or k in msg for k in keys)


def _request_history_with_failover(
    client: HttpClient,
    biz: str,
    offset: int,
    count: int,
    pool: SessionPool,
) -> dict[str, Any]:
    errors: list[str] = []
    for _ in range(len(pool.sessions)):
        session = pool.next()
        try:
            return _request_history(
                client=client,
                biz=biz,
                offset=offset,
                count=count,
                session=session,
            )
        except Exception as exc:
            msg = str(exc)
            if not _is_session_error(msg):
                raise
            errors.append(f"{session.get('name', 'session')}: {msg[:200]}")
    raise RuntimeError("all sessions failed for history page: " + " | ".join(errors))


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
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        # 有时返回 HTML
        text = resp.text
        raise RuntimeError(f"history non-json response: {text[:300]}")
