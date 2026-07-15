from __future__ import annotations

import sqlite3
from typing import Any

from wechat_archive.db import Database


def article_page(
    db: Database,
    q: str | None = None,
    status: str | None = None,
    account_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    before_ts: int | None = None,
    before_id: int | None = None,
) -> dict[str, Any]:
    use_fts = bool(q and db.fts_available())
    try:
        return _query_page(
            db, q, status, account_id, limit, offset, before_ts, before_id, use_fts
        )
    except sqlite3.OperationalError:
        if not q or not use_fts:
            raise
        return _query_page(
            db, q, status, account_id, limit, offset, before_ts, before_id, False
        )


def _query_page(
    db: Database,
    q: str | None,
    status: str | None,
    account_id: int | None,
    limit: int,
    offset: int,
    before_ts: int | None,
    before_id: int | None,
    use_fts: bool,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("ar.status=?")
        params.append(status)
    if account_id:
        clauses.append("ar.account_id=?")
        params.append(account_id)
    if q:
        if use_fts:
            clauses.append("articles_fts MATCH ?")
            params.append(f'"{q.replace(chr(34), chr(34) * 2)}"')
        else:
            clauses.append(
                "(ar.title LIKE ? OR ar.author LIKE ? OR ar.digest LIKE ? "
                "OR ar.content_text LIKE ?)"
            )
            term = f"%{q}%"
            params.extend([term, term, term, term])
    join_fts = "JOIN articles_fts ON articles_fts.rowid=ar.id" if use_fts else ""
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = db.fetchone(
        f"SELECT COUNT(*) count FROM articles ar {join_fts} {where}", tuple(params)
    )

    page_clauses = list(clauses)
    page_params = list(params)
    if before_ts is not None and before_id is not None:
        page_clauses.append(
            "(COALESCE(ar.publish_ts,0) < ? OR "
            "(COALESCE(ar.publish_ts,0) = ? AND ar.id < ?))"
        )
        page_params.extend([before_ts, before_ts, before_id])
    page_where = f"WHERE {' AND '.join(page_clauses)}" if page_clauses else ""
    page_params.extend([limit, 0 if before_id is not None else offset])
    rows = db.fetchall(
        f"""
        SELECT ar.id, ar.account_id, ar.title, ar.author, ar.digest,
               ar.publish_time, ar.url, ar.cover_url, ar.status,
               ar.retry_count, ar.content_error, a.account_name,
               COALESCE(ar.publish_ts, 0) AS sort_ts
        FROM articles ar JOIN accounts a ON a.id=ar.account_id
        {join_fts}
        {page_where}
        ORDER BY COALESCE(ar.publish_ts, 0) DESC, ar.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(page_params),
    )
    items = [dict(row) for row in rows]
    next_cursor = None
    if len(items) == limit:
        last = items[-1]
        next_cursor = {"before_ts": last["sort_ts"], "before_id": last["id"]}
    return {
        "items": items,
        "total": total["count"] if total else 0,
        "next_cursor": next_cursor,
        "search_backend": "fts5" if use_fts else "like",
    }
