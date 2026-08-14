"""Offline import of Schinza manifests and Markdown bodies into SQLite."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from wechat_archive.db import Database
from wechat_archive.url_utils import article_sn, normalize_article_url

SOURCE_RE = re.compile(r"^来源[：:]\s*(https?://\S+)\s*$", re.MULTILINE)


def _markdown_by_url(root: Path) -> dict[str, tuple[Path, str]]:
    bodies: dict[str, tuple[Path, str]] = {}
    if not root.exists():
        return bodies
    for path in root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = SOURCE_RE.search(text[:5000])
        if not match:
            continue
        normalized = normalize_article_url(match.group(1))
        if normalized:
            bodies.setdefault(normalized, (path, text))
    return bodies


def _wechat_biz(url: str) -> str | None:
    try:
        value = (parse_qs(urlparse(url).query).get("__biz") or [None])[0]
    except ValueError:
        return None
    return unquote(str(value)) if value else None


def import_schinza_export(
    db: Database,
    manifest_path: str | Path,
    *,
    articles_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge one Schinza account export without making any network requests."""

    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    account_name = str(payload.get("account") or "").strip()
    articles = payload.get("articles")
    if not isinstance(articles, list):
        articles = []
        articles_file = str(payload.get("articles_file") or "").strip()
        if articles_file:
            jsonl_path = manifest.parent / articles_file
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    articles.append(row)
    if not account_name or not isinstance(articles, list):
        raise ValueError("Schinza manifest 缺少 account 或 articles 列表")

    account = db.fetchone(
        """
        SELECT id, nickname_input, account_name
        FROM accounts
        WHERE nickname_input=? OR account_name=?
        ORDER BY CASE WHEN nickname_input=? THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (account_name, account_name, account_name),
    )
    if account is None:
        return {
            "account": account_name,
            "manifest_articles": len(articles),
            "matched_account": False,
            "inserted": 0,
            "updated": 0,
            "existing": 0,
            "with_body": 0,
            "missing_body": len(articles),
            "dry_run": dry_run,
        }

    body_root = Path(articles_dir) if articles_dir else manifest.parent
    bodies = _markdown_by_url(body_root)
    inserted = updated = existing_n = with_body = missing_body = invalid = 0

    def process(conn: Any | None) -> None:
        nonlocal inserted, updated, existing_n, with_body, missing_body, invalid
        for item in articles:
            if not isinstance(item, dict):
                invalid += 1
                continue
            url = str(item.get("link") or item.get("url") or "").strip()
            normalized = normalize_article_url(url)
            if not url or not normalized:
                invalid += 1
                continue
            body_entry = bodies.get(normalized)
            body_text = body_entry[1] if body_entry else None
            if body_text:
                with_body += 1
            else:
                missing_body += 1
            existing_sql = """
                SELECT id, status, content_text
                FROM articles
                WHERE url=? OR normalized_url=?
                ORDER BY id LIMIT 1
                """
            current = (
                conn.execute(existing_sql, (url, normalized)).fetchone()
                if conn is not None
                else db.fetchone(existing_sql, (url, normalized))
            )
            if current is None:
                inserted += 1
                if conn is not None:
                    conn.execute(
                        """
                        INSERT INTO articles (
                            account_id, biz, sn, mid, idx, title, author, digest,
                            cover_url, url, normalized_url, publish_ts, publish_time,
                            content_text, status, raw_list_json, last_fetched_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            CASE WHEN ? IS NOT NULL THEN datetime('now','localtime') END
                        )
                        """,
                        (
                            account["id"],
                            _wechat_biz(url),
                            str(item.get("sn") or article_sn(url) or "") or None,
                            str(item.get("mid") or "") or None,
                            int(item["idx"]) if str(item.get("idx") or "").isdigit() else None,
                            str(item.get("title") or "").strip() or None,
                            str(item.get("author") or "").strip() or None,
                            str(item.get("digest") or "").strip() or None,
                            str(item.get("cover") or item.get("cover_url") or "").strip() or None,
                            url,
                            normalized,
                            int(item["publish_ts"]) if item.get("publish_ts") else None,
                            str(item.get("publish_at") or item.get("publish_time") or "") or None,
                            body_text,
                            "ok" if body_text else "listed",
                            json.dumps(item, ensure_ascii=False),
                            body_text,
                        ),
                    )
                continue

            can_fill_body = bool(body_text and not str(current["content_text"] or "").strip())
            if can_fill_body:
                updated += 1
                if conn is not None:
                    conn.execute(
                        """
                        UPDATE articles
                        SET content_text=?, status='ok', content_error=NULL,
                            last_fetched_at=datetime('now','localtime'),
                            title=COALESCE(title, ?),
                            author=COALESCE(author, ?),
                            digest=COALESCE(digest, ?),
                            cover_url=COALESCE(cover_url, ?),
                            publish_ts=COALESCE(publish_ts, ?),
                            publish_time=COALESCE(publish_time, ?),
                            raw_list_json=COALESCE(raw_list_json, ?),
                            updated_at=datetime('now','localtime')
                        WHERE id=?
                        """,
                        (
                            body_text,
                            str(item.get("title") or "").strip() or None,
                            str(item.get("author") or "").strip() or None,
                            str(item.get("digest") or "").strip() or None,
                            str(item.get("cover") or item.get("cover_url") or "").strip() or None,
                            int(item["publish_ts"]) if item.get("publish_ts") else None,
                            str(item.get("publish_at") or item.get("publish_time") or "") or None,
                            json.dumps(item, ensure_ascii=False),
                            current["id"],
                        ),
                    )
            else:
                existing_n += 1

    if dry_run:
        process(None)
    else:
        with db.connection() as conn:
            process(conn)

    return {
        "account": account_name,
        "account_id": int(account["id"]),
        "matched_account": True,
        "manifest_articles": len(articles),
        "inserted": inserted,
        "updated": updated,
        "existing": existing_n,
        "with_body": with_body,
        "missing_body": missing_body,
        "invalid": invalid,
        "dry_run": dry_run,
        "articles_dir": str(body_root),
    }
