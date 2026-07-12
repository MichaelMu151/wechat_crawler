from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from wechat_archive.config import ensure_dirs, load_config
from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.services.fetch_content import fetch_pending_contents
from wechat_archive.services.fetch_history import fetch_history_for_accounts
from wechat_archive.services.import_accounts import import_name_list
from wechat_archive.services.resolve_accounts import resolve_pending_accounts
from wechat_archive.services.session_import import (
    SessionImportError,
    import_session_from_har,
)
from wechat_archive.services.sessions import load_sessions
from wechat_archive.url_utils import article_sn, normalize_article_url

console = Console()


def _db(cfg) -> Database:
    ensure_dirs(cfg)
    db = Database(cfg["paths"]["database"])
    db.recover_stale_work()
    return db


@click.group()
@click.option("--config", "config_path", default=None, help="配置文件路径")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """微信公众号学术存档爬虫（试点版）"""
    cfg = load_config(config_path)
    ensure_dirs(cfg)
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = cfg


@cli.command("init-db")
@click.pass_context
def init_db(ctx: click.Context) -> None:
    """初始化 SQLite 数据库。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    console.print(f"[green]数据库已就绪:[/green] {db.path}")


@cli.command("import-list")
@click.option("--file", "xlsx", default=None, help="名单 xlsx，默认读 config")
@click.pass_context
def import_list_cmd(ctx: click.Context, xlsx: str | None) -> None:
    """导入 name_list.xlsx。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    path = xlsx or cfg["paths"]["name_list"]
    stats = import_name_list(db, path)
    console.print(f"导入完成: {stats}")


@cli.command("resolve")
@click.option("--limit", default=None, type=int, help="最多处理多少个账号")
@click.pass_context
def resolve_cmd(ctx: click.Context, limit: int | None) -> None:
    """根据样例链接解析 __biz。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)
    console.print("开始解析账号…")
    stats = resolve_pending_accounts(db, client, cfg, limit=limit)
    console.print(f"解析完成: {stats}")
    _seed_sample_articles(db)
    console.print("已将样例文章写入 articles（便于先试正文抓取）。")


@cli.command("import-session-har")
@click.option(
    "--file",
    "har_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Charles 导出的 HTTP Archive (.har)",
)
@click.option(
    "--name",
    default="session_1",
    show_default=True,
    help="生成的会话名称（不含 .yaml）",
)
@click.option("--overwrite", is_flag=True, help="覆盖已有同名会话")
@click.option(
    "--delete-source",
    is_flag=True,
    help="成功导入后删除含敏感 Cookie 的 HAR",
)
@click.pass_context
def import_session_har_cmd(
    ctx: click.Context,
    har_file: Path,
    name: str,
    overwrite: bool,
    delete_source: bool,
) -> None:
    """从 Charles HAR 自动提取微信历史会话。"""
    cfg = ctx.obj["cfg"]
    try:
        result = import_session_from_har(
            har_file,
            cfg["paths"]["sessions_dir"],
            name=name,
            overwrite=overwrite,
        )
    except SessionImportError as exc:
        raise click.ClickException(str(exc)) from exc

    table = Table(title="微信会话导入成功")
    table.add_column("字段")
    table.add_column("结果")
    table.add_row("名称", result["name"])
    table.add_row("文件", result["path"])
    table.add_row("公众号 biz", result["biz"] or "HAR 中未提供")
    table.add_row("捕获时间", result["captured_at"] or "HAR 中未提供")
    table.add_row("候选请求", str(result["candidates_found"]))
    console.print(table)
    console.print("[green]已安全写入会话；不会在终端显示 uin、key 或 Cookie。[/green]")

    if delete_source:
        try:
            har_file.unlink()
            console.print(f"[green]已删除敏感 HAR: {har_file}[/green]")
        except OSError as exc:
            console.print(
                f"[yellow]会话已导入，但无法删除 HAR，请手动删除: {exc}[/yellow]"
            )
    else:
        console.print(
            "[yellow]HAR 含登录凭据。确认导入成功后请将其安全删除，"
            "切勿上传或提交 Git。[/yellow]"
        )


@cli.command("history")
@click.option("--limit", default=None, type=int, help="最多处理多少个账号")
@click.pass_context
def history_cmd(ctx: click.Context, limit: int | None) -> None:
    """拉取历史发文列表（需要 sessions/*.yaml）。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    sessions = load_sessions(cfg["paths"]["sessions_dir"])
    if not sessions:
        console.print(
            "[yellow]还没有可用会话。[/yellow]\n"
            "推荐：从 Charles 导出 HAR，然后运行\n"
            "python run.py import-session-har --file capture.har --delete-source\n"
            "也可以手工复制 sessions/example_session.yaml 并填入参数。\n"
            "详见 README。"
        )
        return
    console.print(f"已加载 {len(sessions)} 个会话: {[s['name'] for s in sessions]}")
    client = HttpClient(cfg)
    stats = fetch_history_for_accounts(db, client, cfg, limit_accounts=limit)
    console.print(f"历史列表完成: {stats}")


@cli.command("content")
@click.option("--limit", default=None, type=int, help="最多抓取多少篇")
@click.pass_context
def content_cmd(ctx: click.Context, limit: int | None) -> None:
    """抓取待处理文章正文。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)
    console.print("开始抓取正文…")
    stats = fetch_pending_contents(db, client, cfg, limit=limit)
    console.print(f"正文抓取完成: {stats}")


@cli.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """查看进度概览。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)

    table = Table(title="账号状态")
    table.add_column("resolve")
    table.add_column("list")
    table.add_column("count", justify="right")
    for row in db.fetchall(
        """
        SELECT resolve_status, list_status, COUNT(*) AS c
        FROM accounts
        GROUP BY resolve_status, list_status
        ORDER BY resolve_status, list_status
        """
    ):
        table.add_row(row["resolve_status"], row["list_status"], str(row["c"]))
    console.print(table)

    table2 = Table(title="文章状态")
    table2.add_column("status")
    table2.add_column("count", justify="right")
    for row in db.fetchall(
        "SELECT status, COUNT(*) AS c FROM articles GROUP BY status ORDER BY status"
    ):
        table2.add_row(row["status"], str(row["c"]))
    console.print(table2)

    sessions = load_sessions(cfg["paths"]["sessions_dir"])
    console.print(f"可用会话数: {len(sessions)}")
    console.print(f"数据库: {cfg['paths']['database']}")


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, type=int, show_default=True)
def serve_cmd(host: str, port: int) -> None:
    """启动本地 API、任务执行器和已构建的管理台。"""
    import uvicorn

    uvicorn.run("wechat_archive.api.app:app", host=host, port=port)


@cli.command("retry-failed")
@click.option("--limit", default=None, type=int, help="最多重置多少篇")
@click.pass_context
def retry_failed_cmd(ctx: click.Context, limit: int | None) -> None:
    """将已耗尽重试的正文任务重新放回队列。"""
    db = _db(ctx.obj["cfg"])
    sql = "SELECT id FROM articles WHERE status='failed' ORDER BY id"
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    ids = [row["id"] for row in db.fetchall(sql, params)]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        db.execute(
            f"""
            UPDATE articles SET status='listed', retry_count=0,
                next_retry_at=NULL, content_error=NULL
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
    console.print(f"已重置 {len(ids)} 篇失败文章")


@cli.command("export-jsonl")
@click.option("--out", default="export/articles.jsonl", help="输出路径")
@click.option("--status-filter", default="ok", help="导出的文章 status，默认 ok")
@click.pass_context
def export_jsonl(ctx: click.Context, out: str, status_filter: str) -> None:
    """导出结构化 JSONL，便于 Python 分析。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parents[1] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = db.iter_rows(
        """
        SELECT a.nickname_input, a.account_name, a.biz,
               ar.title, ar.author, ar.digest, ar.publish_time, ar.publish_ts,
               ar.url, ar.cover_url, ar.content_text, ar.content_html, ar.status, ar.sn
        FROM articles ar
        JOIN accounts a ON a.id = ar.account_id
        WHERE ar.status = ?
        ORDER BY (ar.publish_ts IS NULL), ar.publish_ts, ar.id
        """,
        (status_filter,),
    )

    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            count += 1
    console.print(f"[green]已导出 {count} 条 → {out_path}[/green]")


@cli.command("pilot")
@click.option("--limit", default=None, type=int, help="限制账号数")
@click.pass_context
def pilot_cmd(ctx: click.Context, limit: int | None) -> None:
    """一键试点：导入 → 解析 →（若有会话则拉历史）→ 抓正文。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)

    console.print("[bold]1/4 导入名单[/bold]")
    stats = import_name_list(db, cfg["paths"]["name_list"])
    console.print(stats)

    console.print("[bold]2/4 解析 __biz[/bold]")
    stats = resolve_pending_accounts(db, client, cfg, limit=limit)
    console.print(stats)
    _seed_sample_articles(db)

    sessions = load_sessions(cfg["paths"]["sessions_dir"])
    if sessions:
        console.print(f"[bold]3/4 拉取历史列表[/bold]（{len(sessions)} 个会话）")
        stats = fetch_history_for_accounts(db, client, cfg, limit_accounts=limit)
        console.print(stats)
    else:
        console.print(
            "[bold]3/4 跳过历史列表[/bold]（尚未配置 sessions；仅抓取样例文章正文）"
        )

    console.print("[bold]4/4 抓取正文[/bold]")
    stats = fetch_pending_contents(db, client, cfg, limit=limit)
    console.print(stats)
    ctx.invoke(status_cmd)


def _seed_sample_articles(db: Database) -> None:
    """把已解析账号的样例链接写入 articles，方便无会话时先验证正文链路。"""
    rows = db.fetchall(
        """
        SELECT id, biz, sample_url
        FROM accounts
        WHERE resolve_status='ok' AND sample_url IS NOT NULL AND sample_url != ''
        """
    )
    with db.connection() as conn:
        for row in rows:
            url = row["sample_url"].split("#")[0].strip()
            exists = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (url,)
            ).fetchone()
            if exists:
                continue
            sn = article_sn(url)
            conn.execute(
                """
                INSERT INTO articles (
                    account_id, biz, sn, url, normalized_url, status
                ) VALUES (?, ?, ?, ?, ?, 'listed')
                """,
                (row["id"], row["biz"], sn, url, normalize_article_url(url)),
            )


if __name__ == "__main__":
    cli()
