from __future__ import annotations

import random
import time
from typing import Any

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html


def resolve_pending_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
) -> dict[str, int]:
    """用样例文章链接解析 __biz 与公众号名。"""
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]

    sql = """
        SELECT id, nickname_input, sample_url
        FROM accounts
        WHERE resolve_status = 'pending' AND sample_url IS NOT NULL AND sample_url != ''
        ORDER BY id
    """
    rows = db.fetchall(sql)
    if limit is not None:
        rows = rows[:limit]

    ok = fail = 0
    for i, row in enumerate(rows):
        account_id = row["id"]
        url = row["sample_url"]
        try:
            final_url, html_text = client.get_text(url)
            parsed = parse_article_html(html_text, final_url)
            if not parsed.get("biz"):
                raise RuntimeError(f"未能解析 biz: status={parsed.get('status')} err={parsed.get('error')}")

            with db.connection() as conn:
                # biz 唯一：若已被其他行占用，合并提示
                other = conn.execute(
                    "SELECT id, nickname_input FROM accounts WHERE biz = ? AND id != ?",
                    (parsed["biz"], account_id),
                ).fetchone()
                if other:
                    conn.execute(
                        """
                        UPDATE accounts
                        SET resolve_status='failed',
                            resolve_error=?,
                            updated_at=datetime('now','localtime')
                        WHERE id=?
                        """,
                        (
                            f"biz {parsed['biz']} 已被账号#{other['id']} ({other['nickname_input']}) 占用",
                            account_id,
                        ),
                    )
                    fail += 1
                else:
                    conn.execute(
                        """
                        UPDATE accounts
                        SET biz=?,
                            account_name=?,
                            username=?,
                            head_img=?,
                            resolve_status='ok',
                            resolve_error=NULL,
                            updated_at=datetime('now','localtime')
                        WHERE id=?
                        """,
                        (
                            parsed["biz"],
                            parsed.get("account_name") or row["nickname_input"],
                            parsed.get("username"),
                            parsed.get("head_img"),
                            account_id,
                        ),
                    )
                    ok += 1
        except Exception as e:
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE accounts
                    SET resolve_status='failed',
                        resolve_error=?,
                        updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (str(e)[:500], account_id),
                )
            fail += 1

        if i < len(rows) - 1:
            time.sleep(random.uniform(sleep_min, sleep_max))

    return {"ok": ok, "fail": fail, "total": len(rows)}
