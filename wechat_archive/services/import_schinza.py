"""Map Schinza account metadata to crawler accounts without copying secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wechat_archive.db import Database
from wechat_archive.schinza_client import credential_status, load_schinza_accounts


def import_schinza_credentials(db: Database, path: str | Path) -> dict[str, Any]:
    rows = load_schinza_accounts(path)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if name:
            by_name.setdefault(name, []).append(row)

    matched = active = expired = invalid = duplicates = 0
    matched_names: set[str] = set()
    details: list[dict[str, str]] = []
    accounts = db.fetchall(
        "SELECT id, nickname_input, account_name, list_status FROM accounts ORDER BY id"
    )
    with db.connection() as conn:
        for account in accounts:
            candidates: list[dict[str, Any]] = []
            for name in {
                str(account["nickname_input"] or "").strip(),
                str(account["account_name"] or "").strip(),
            }:
                if name:
                    candidates.extend(by_name.get(name, []))
            unique = {str(row.get("id") or id(row)): row for row in candidates}
            candidates = list(unique.values())
            if not candidates:
                continue
            if len(candidates) > 1:
                duplicates += 1
                details.append(
                    {
                        "name": str(account["nickname_input"]),
                        "status": "duplicate",
                        "reason": "Schinza 中存在多个同名账号",
                    }
                )
                continue

            row = candidates[0]
            credentials = row.get("credentials") or {}
            biz = str(credentials.get("__biz") or row.get("biz") or "").strip()
            ok, reason = credential_status(row)
            if ok:
                active += 1
                next_status = (
                    "pending" if account["list_status"] == "need_session" else account["list_status"]
                )
            elif "过期" in reason:
                expired += 1
                next_status = "need_session"
            else:
                invalid += 1
                next_status = "need_session"
            conn.execute(
                """
                UPDATE accounts
                SET wechat_biz=?, schinza_account_id=?,
                    history_backend='schinza_getmsg',
                    account_name=COALESCE(NULLIF(account_name, ''), ?),
                    resolve_status=CASE WHEN ? != '' THEN 'ok' ELSE resolve_status END,
                    resolve_error=CASE WHEN ? != '' THEN NULL ELSE resolve_error END,
                    list_status=?,
                    list_error=CASE
                        WHEN ? THEN NULL
                        ELSE 'auth: Schinza ' || ?
                    END,
                    updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (
                    biz or None,
                    str(row.get("id") or "") or None,
                    str(row.get("name") or "").strip() or None,
                    biz,
                    biz,
                    next_status,
                    ok,
                    reason,
                    account["id"],
                ),
            )
            matched += 1
            matched_names.add(str(row.get("name") or "").strip())
            details.append(
                {
                    "name": str(account["nickname_input"]),
                    "status": "active" if ok else "unavailable",
                    "reason": "" if ok else reason,
                }
            )

    unmatched_crawler = len(accounts) - matched - duplicates
    unmatched_schinza = sum(
        1 for row in rows if str(row.get("name") or "").strip() not in matched_names
    )
    return {
        "source": str(Path(path)),
        "crawler_accounts": len(accounts),
        "schinza_accounts": len(rows),
        "matched": matched,
        "active": active,
        "expired": expired,
        "invalid": invalid,
        "duplicates": duplicates,
        "unmatched_crawler": unmatched_crawler,
        "unmatched_schinza": unmatched_schinza,
        "details": details,
    }
