import json
from pathlib import Path

from wechat_archive.db import Database
from wechat_archive.services.import_schinza_export import import_schinza_export


def test_offline_schinza_import_dry_run_and_merge(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    db.execute("INSERT INTO accounts (nickname_input) VALUES ('测试医院')")
    url = (
        "https://mp.weixin.qq.com/s?__biz=MzTest=="
        "&mid=123&idx=1&sn=abc&scene=27#wechat_redirect"
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "account": "测试医院",
                "articles": [
                    {
                        "title": "测试文章",
                        "link": url,
                        "publish_ts": 1704067200,
                        "publish_at": "2024-01-01 00:00",
                        "mid": "123",
                        "idx": "1",
                        "sn": "abc",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "01_测试文章.md").write_text(
        f"# 测试文章\n\n来源：{url}\n发布时间：2024-01-01 00:00\n\n正文内容\n",
        encoding="utf-8",
    )

    preview = import_schinza_export(db, manifest, dry_run=True)
    assert preview["inserted"] == 1
    assert preview["with_body"] == 1
    assert db.fetchone("SELECT COUNT(*) AS c FROM articles")["c"] == 0

    result = import_schinza_export(db, manifest)
    assert result["inserted"] == 1
    row = db.fetchone("SELECT title, status, content_text, biz FROM articles")
    assert row["title"] == "测试文章"
    assert row["status"] == "ok"
    assert "正文内容" in row["content_text"]
    assert row["biz"] == "MzTest=="

    repeated = import_schinza_export(db, manifest)
    assert repeated["existing"] == 1
    assert repeated["inserted"] == 0


def test_offline_import_reads_streaming_manifest_jsonl(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.db")
    db.execute("INSERT INTO accounts (nickname_input) VALUES ('大型账号')")
    url = "https://mp.weixin.qq.com/s?__biz=Large&mid=9&idx=1&sn=z"
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps({"title": "流式文章", "link": url}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "account": "大型账号",
                "count": 1,
                "articles_file": "manifest.jsonl",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = import_schinza_export(db, manifest, dry_run=True)
    assert result["manifest_articles"] == 1
    assert result["inserted"] == 1
