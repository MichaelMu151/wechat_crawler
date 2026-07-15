from __future__ import annotations

import random
from typing import Any, Callable

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.services.job_control import cooperative_sleep


def resolve_pending_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """用样例文章链接解析 __biz 与公众号名。"""
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]

    count_row = db.fetchone(
        """
        SELECT COUNT(*) AS count FROM accounts
        WHERE resolve_status='pending' AND sample_url IS NOT NULL AND sample_url != ''
        """
    )
    total = int(count_row["count"]) if count_row else 0
    if limit is not None:
        total = min(total, limit)
    batch_size = max(1, int(cfg["crawl"].get("account_batch_size", 200)))
    if progress:
        progress(0, total)

    ok = fail = processed = last_id = 0
    while processed < total:
        rows = db.fetchall(
            """
            SELECT id, nickname_input, sample_url
            FROM accounts
            WHERE resolve_status='pending' AND sample_url IS NOT NULL
              AND sample_url != '' AND id > ?
            ORDER BY id LIMIT ?
            """,
            (last_id, min(batch_size, total - processed)),
        )
        if not rows:
            break
        for row in rows:
            if checkpoint:
                checkpoint()
            account_id = row["id"]
            last_id = account_id
            url = row["sample_url"]
            try:
                final_url, html_text = client.get_text(url)
                parsed = parse_article_html(html_text, final_url)
                if not parsed.get("biz"):
                    raise RuntimeError(
                        f"未能解析 biz: status={parsed.get('status')} "
                        f"err={parsed.get('error')}"
                    )

                with db.connection() as conn:
                    other = conn.execute(
                        "SELECT id, nickname_input FROM accounts WHERE biz=? AND id!=?",
                        (parsed["biz"], account_id),
                    ).fetchone()
                    if other:
                        conn.execute(
                            """
                            UPDATE accounts SET resolve_status='failed',
                                resolve_error=?, updated_at=datetime('now','localtime')
                            WHERE id=?
                            """,
                            (
                                f"biz {parsed['biz']} 已被账号#{other['id']} "
                                f"({other['nickname_input']}) 占用",
                                account_id,
                            ),
                        )
                        fail += 1
                    else:
                        conn.execute(
                            """
                            UPDATE accounts SET biz=?, account_name=?, username=?,
                                head_img=?, resolve_status='ok', resolve_error=NULL,
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
                db.execute(
                    """
                    UPDATE accounts SET resolve_status='failed', resolve_error=?,
                        updated_at=datetime('now','localtime') WHERE id=?
                    """,
                    (str(e)[:500], account_id),
                )
                fail += 1
            processed += 1
            if progress:
                progress(processed, total)
            if processed < total:
                cooperative_sleep(random.uniform(sleep_min, sleep_max), checkpoint)

    return {"ok": ok, "fail": fail, "total": processed}
