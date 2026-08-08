"""运维诊断：错误聚合、下一步建议、doctor 报告。"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from wechat_archive.db import Database
from wechat_archive.platform_client import load_platform_credentials


def classify_error(message: str | None) -> str:
    text = (message or "").strip().lower()
    raw = message or ""
    if raw.startswith("rate_limited:") or "freq control" in text or "200013" in text:
        return "rate_limited"
    if raw.startswith("auth:") or any(
        k in text for k in ("login", "expired", "session", "登录", "过期", "失效")
    ):
        return "auth"
    if "interrupted" in text:
        return "interrupted"
    if raw.startswith("api:"):
        return "api"
    return "other"


def aggregate_errors(db: Database, limit: int = 20) -> dict[str, Any]:
    list_rows = db.fetchall(
        """
        SELECT list_error AS error, COUNT(*) AS count
        FROM accounts
        WHERE list_status IN ('failed', 'retry_wait', 'need_session')
          AND list_error IS NOT NULL AND list_error != ''
        GROUP BY list_error
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    )
    resolve_rows = db.fetchall(
        """
        SELECT resolve_error AS error, COUNT(*) AS count
        FROM accounts
        WHERE resolve_status='failed'
          AND resolve_error IS NOT NULL AND resolve_error != ''
        GROUP BY resolve_error
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    )
    content_rows = db.fetchall(
        """
        SELECT content_error AS error, COUNT(*) AS count
        FROM articles
        WHERE status IN ('failed', 'retry_wait')
          AND content_error IS NOT NULL AND content_error != ''
        GROUP BY content_error
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    )

    def bucket(rows: list[Any]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in rows:
            counts[classify_error(row["error"])] += int(row["count"])
        return dict(counts)

    return {
        "list_errors": [dict(r) for r in list_rows],
        "resolve_errors": [dict(r) for r in resolve_rows],
        "content_errors": [dict(r) for r in content_rows],
        "list_by_kind": bucket(list_rows),
        "resolve_by_kind": bucket(resolve_rows),
        "content_by_kind": bucket(content_rows),
    }


def suggest_next_actions(db: Database, cfg: dict[str, Any] | None = None) -> list[str]:
    actions: list[str] = []
    cfg = cfg or {}

    resolve_pending = db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts WHERE resolve_status='pending'"
    )
    resolve_failed = db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts WHERE resolve_status='failed'"
    )
    list_pending = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM accounts
        WHERE resolve_status='ok'
          AND list_status IN ('pending', 'failed', 'retry_wait', 'running', 'need_session')
        """
    )
    list_rate = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM accounts
        WHERE list_error LIKE 'rate_limited:%'
           OR list_error LIKE '%freq control%'
           OR list_error LIKE '%200013%'
        """
    )
    need_session = db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts WHERE list_status='need_session'"
    )
    listed = db.fetchone(
        "SELECT COUNT(*) AS c FROM articles WHERE status IN ('listed', 'retry_wait')"
    )
    content_failed = db.fetchone(
        "SELECT COUNT(*) AS c FROM articles WHERE status='failed'"
    )
    running = db.fetchone(
        "SELECT COUNT(*) AS c FROM accounts WHERE list_status='running'"
    )

    rp = int(resolve_pending["c"]) if resolve_pending else 0
    rf = int(resolve_failed["c"]) if resolve_failed else 0
    lp = int(list_pending["c"]) if list_pending else 0
    lr = int(list_rate["c"]) if list_rate else 0
    ns = int(need_session["c"]) if need_session else 0
    li = int(listed["c"]) if listed else 0
    cf = int(content_failed["c"]) if content_failed else 0
    rn = int(running["c"]) if running else 0

    creds = load_platform_credentials(cfg) if cfg else None
    if cfg and not creds and (cfg.get("platform") or {}).get("backend") != "download_api":
        actions.append(
            "公众平台凭证未配置 → python run.py import-platform-from-download-api"
        )
    elif creds and creds.expired:
        actions.append("凭证已过期 → 重新扫码并 import-platform-from-download-api")

    if ns:
        actions.append(
            f"{ns} 个账号 need_session → 更新凭证后重跑 python run.py history"
        )
    if lr >= 3:
        wait_m = int((cfg.get("crawl") or {}).get("history_global_cooldown", 3600)) // 60
        actions.append(
            f"检测到 {lr} 个历史限流错误 → 先停 {max(wait_m, 30)} 分钟，"
            "再用 --profile safe 运行 history"
        )
    if rn:
        actions.append(
            f"{rn} 个账号仍为 running（可能中断）→ 下次 history 会自动回收；"
            "或先 python run.py doctor"
        )
    if rp:
        actions.append(f"{rp} 个账号待解析 → python run.py resolve")
    if rf:
        actions.append(
            f"{rf} 个账号 resolve 失败 → python run.py retry-resolve 后再 resolve"
        )
    if lp and lr < 3:
        actions.append(
            f"{lp} 个账号待拉/可重试历史列表 → python run.py history"
            "（默认跳过 done；增量刷新加 --refresh）"
        )
    if li:
        actions.append(f"{li} 篇正文待抓 → python run.py content")
    if cf:
        actions.append(
            f"{cf} 篇正文 failed → python run.py retry-failed 后再 content"
        )
    if not actions:
        actions.append("暂无待办：可 python run.py status 或 export-jsonl / export-account-summary")
    return actions


def build_doctor_report(db: Database, cfg: dict[str, Any]) -> dict[str, Any]:
    account_rows = db.fetchall(
        """
        SELECT resolve_status, list_status, COUNT(*) AS count
        FROM accounts
        GROUP BY resolve_status, list_status
        ORDER BY resolve_status, list_status
        """
    )
    article_rows = db.fetchall(
        "SELECT status, COUNT(*) AS count FROM articles GROUP BY status ORDER BY status"
    )
    errors = aggregate_errors(db)
    actions = suggest_next_actions(db, cfg)
    recovered = {"accounts": 0, "jobs": 0}
    # dry hint: count stale running without mutating in doctor? Plan says recover on history/serve.
    # Doctor only reports; history recovers.
    stale_running = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM accounts
        WHERE list_status='running'
          AND updated_at < datetime('now', '-30 minutes', 'localtime')
        """
    )

    creds = load_platform_credentials(cfg)
    platform: dict[str, Any] = {
        "backend": (cfg.get("platform") or {}).get("backend", "platform"),
        "configured": bool(creds)
        or (cfg.get("platform") or {}).get("backend") == "download_api",
    }
    if creds:
        platform["source"] = creds.source
        platform["nickname"] = creds.nickname
        platform["expired"] = creds.expired
        if creds.expire_time_ms:
            platform["remain_hours"] = round(
                (creds.expire_time_ms / 1000 - time.time()) / 3600, 1
            )

    return {
        "database": str(db.path),
        "accounts": [dict(r) for r in account_rows],
        "articles": {r["status"]: r["count"] for r in article_rows},
        "errors": errors,
        "stale_running_accounts": int(stale_running["c"]) if stale_running else 0,
        "platform": platform,
        "next_actions": actions,
        "recovered_hint": recovered,
    }


def reset_failed_resolves(db: Database, limit: int | None = None) -> int:
    sql = """
        SELECT id FROM accounts
        WHERE resolve_status='failed'
        ORDER BY id
    """
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    ids = [row["id"] for row in db.fetchall(sql, params)]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    db.execute(
        f"""
        UPDATE accounts
        SET resolve_status='pending', resolve_error=NULL,
            updated_at=datetime('now','localtime')
        WHERE id IN ({placeholders})
        """,
        tuple(ids),
    )
    return len(ids)


def account_summary_rows(db: Database) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT
          a.id,
          a.nickname_input,
          a.account_name,
          a.resolve_status,
          a.list_status,
          COUNT(ar.id) AS articles_total,
          SUM(CASE WHEN ar.status='ok' THEN 1 ELSE 0 END) AS articles_ok,
          SUM(CASE WHEN ar.status='listed' THEN 1 ELSE 0 END) AS articles_listed,
          SUM(CASE WHEN ar.status='failed' THEN 1 ELSE 0 END) AS articles_failed,
          SUM(CASE WHEN ar.status='deleted' THEN 1 ELSE 0 END) AS articles_deleted,
          MIN(ar.publish_time) AS first_publish,
          MAX(ar.publish_time) AS last_publish
        FROM accounts a
        LEFT JOIN articles ar ON ar.account_id = a.id
        GROUP BY a.id
        ORDER BY a.id
        """
    )
    return [dict(r) for r in rows]
