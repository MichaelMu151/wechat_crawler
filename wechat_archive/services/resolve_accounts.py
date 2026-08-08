from __future__ import annotations

import random
from typing import Any, Callable

from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.platform_client import (
    PlatformAuthError,
    PlatformAPIError,
    PlatformRateLimited,
    build_history_client,
    load_platform_credentials,
)
from wechat_archive.services.job_control import JobCancelled, cooperative_sleep


class ResolveRateLimited(RuntimeError):
    """公众平台 searchbiz 限流。"""


def resolve_pending_accounts(
    db: Database,
    client: HttpClient,
    cfg: dict[str, Any],
    limit: int | None = None,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """解析账号标识。

    优先使用公众平台 searchbiz（昵称精确匹配 → fakeid）；
    若无平台凭证或搜索失败，再回退到样例文章链接解析 __biz。
    """
    sleep_min = cfg["crawl"]["sleep_min"]
    sleep_max = cfg["crawl"]["sleep_max"]
    breaker_threshold = max(
        1, int(cfg["crawl"].get("history_circuit_breaker_threshold", 3))
    )
    global_cooldown = max(0, int(cfg["crawl"].get("history_global_cooldown", 3600)))

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

    ok = fail = rate_limited = 0
    total = len(rows)
    consecutive_rate_limits = 0
    circuit_open = False
    if progress:
        progress(0, total, phase="解析账号")

    for i, row in enumerate(rows):
        if checkpoint:
            checkpoint()
        account_id = row["id"]
        nickname = row["nickname_input"]
        sample_url = row["sample_url"]
        if progress:
            progress(
                i,
                total,
                phase="解析账号",
                account=nickname,
                account_done=0,
                account_total=1,
                ok=ok,
                failed=fail,
            )
        try:
            resolved = None
            resolve_notes: list[str] = []
            if platform is not None:
                try:
                    resolved = _resolve_via_platform(platform, nickname)
                    consecutive_rate_limits = 0
                except PlatformRateLimited as exc:
                    # 限流：保持 pending，稍后重试，不永久 failed
                    rate_limited += 1
                    consecutive_rate_limits += 1
                    with db.connection() as conn:
                        conn.execute(
                            """
                            UPDATE accounts
                            SET resolve_error=?,
                                updated_at=datetime('now','localtime')
                            WHERE id=?
                            """,
                            (f"rate_limited: {exc}"[:500], account_id),
                        )
                    if progress:
                        progress(
                            i,
                            total,
                            phase="解析账号",
                            account=nickname,
                            note=f"限流，保持 pending · 连续 {consecutive_rate_limits}",
                            ok=ok,
                            failed=fail,
                        )
                    if consecutive_rate_limits >= breaker_threshold:
                        circuit_open = True
                        if global_cooldown > 0:
                            cooperative_sleep(float(global_cooldown), checkpoint)
                        break
                    cooperative_sleep(
                        float(cfg["crawl"].get("history_rate_limit_cooldown", 900)),
                        checkpoint,
                    )
                    continue
                except PlatformAuthError as exc:
                    platform = None
                    resolve_notes.append(f"platform auth failed: {exc}")
                except PlatformAPIError as exc:
                    resolve_notes.append(str(exc))
                    resolved = None
            if resolved is None:
                if not sample_url:
                    detail = "；".join(resolve_notes) or "无平台命中"
                    raise RuntimeError(
                        f"无法解析 fakeid/__biz（{detail}），且缺少 sample_url"
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
                            f"api: biz/fakeid {resolved['biz']} 已被账号#{other['id']} "
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
        except JobCancelled:
            raise
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

        if progress:
            progress(
                i + 1,
                total,
                phase="解析账号",
                account=nickname,
                account_done=1,
                account_total=1,
                ok=ok,
                failed=fail,
            )
        if i < total - 1:
            cooperative_sleep(random.uniform(sleep_min, sleep_max), checkpoint)

    return {
        "ok": ok,
        "fail": fail,
        "total": total,
        "rate_limited": rate_limited,
        "circuit_open": circuit_open,
        "processed": ok + fail + rate_limited,
    }


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
