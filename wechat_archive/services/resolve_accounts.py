from __future__ import annotations

import random
import time
from typing import Any

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.platform_client import (
    PlatformAuthError,
    PlatformAPIError,
    build_history_client,
    load_platform_credentials,
)


def resolve_pending_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
) -> dict[str, int]:
    """解析账号标识。

    优先使用公众平台 searchbiz（昵称 → fakeid）；
    若无平台凭证或搜索失败，再回退到样例文章链接解析 __biz。
    """
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]

    rows = db.fetchall(
        """
        SELECT id, nickname_input, sample_url
        FROM accounts
        WHERE resolve_status = 'pending'
        ORDER BY id
        """
    )
    if limit is not None:
        rows = rows[:limit]

    platform = None
    try:
        if load_platform_credentials(cfg) or (
            (cfg.get("platform") or {}).get("backend") == "download_api"
        ):
            platform = build_history_client(cfg)
    except PlatformAuthError:
        platform = None

    ok = fail = 0
    for i, row in enumerate(rows):
        account_id = row["id"]
        nickname = row["nickname_input"]
        sample_url = row["sample_url"]
        try:
            resolved = None
            if platform is not None:
                try:
                    resolved = _resolve_via_platform(platform, nickname)
                except (PlatformAPIError, PlatformAuthError):
                    resolved = None
            if resolved is None:
                if not sample_url:
                    raise RuntimeError(
                        "平台搜索失败且缺少 sample_url，无法解析 fakeid/__biz"
                    )
                resolved = _resolve_via_sample(client, sample_url, nickname)

            with db.connection() as conn:
                other = conn.execute(
                    "SELECT id, nickname_input FROM accounts WHERE biz=? AND id!=?",
                    (resolved["biz"], account_id),
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
                            f"biz/fakeid {resolved['biz']} 已被账号#{other['id']} "
                            f"({other['nickname_input']}) 占用",
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
                            resolved["biz"],
                            resolved.get("account_name") or nickname,
                            resolved.get("username"),
                            resolved.get("head_img"),
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


def _resolve_via_platform(platform: Any, nickname: str) -> dict[str, Any]:
    chosen = platform.resolve_fakeid(nickname)
    return {
        "biz": chosen["fakeid"],
        "account_name": chosen.get("nickname") or nickname,
        "username": chosen.get("alias") or None,
        "head_img": chosen.get("round_head_img") or None,
    }


def _resolve_via_sample(
    client: HttpClient, url: str, nickname: str
) -> dict[str, Any]:
    final_url, html_text = client.get_text(url)
    parsed = parse_article_html(html_text, final_url)
    if not parsed.get("biz"):
        raise RuntimeError(
            f"未能从样例链接解析 biz: status={parsed.get('status')} "
            f"err={parsed.get('error')}"
        )
    return {
        "biz": parsed["biz"],
        "account_name": parsed.get("account_name") or nickname,
        "username": parsed.get("username"),
        "head_img": parsed.get("head_img"),
    }
