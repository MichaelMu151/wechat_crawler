from pathlib import Path

from wechat_archive.db import Database
from wechat_archive.services.article_queries import article_page


def test_article_page_uses_cursor_and_fts(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    with db.connection() as conn:
        account_id = conn.execute(
            "INSERT INTO accounts (nickname_input) VALUES ('sample')"
        ).lastrowid
        for index in range(3):
            conn.execute(
                """
                INSERT INTO articles (
                    account_id, title, content_text, publish_ts, url, status
                ) VALUES (?, ?, ?, ?, ?, 'ok')
                """,
                (
                    account_id,
                    f"article-{index}",
                    f"needle content {index}",
                    1704067200 - index,
                    f"https://mp.weixin.qq.com/s/{index}",
                ),
            )

    first = article_page(db, q="needle", limit=2)
    cursor = first["next_cursor"]
    assert first["search_backend"] == "fts5"
    assert len(first["items"]) == 2
    assert cursor is not None

    second = article_page(
        db,
        q="needle",
        limit=2,
        before_ts=cursor["before_ts"],
        before_id=cursor["before_id"],
    )
    assert len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )
