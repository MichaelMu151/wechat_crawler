from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from wechat_archive.config import PROFILES, ensure_dirs, load_config
from wechat_archive.db import Database
from wechat_archive.http_client import HttpClient
from wechat_archive.platform_client import (
    PlatformAuthError,
    PlatformCredentials,
    build_history_client,
    import_credentials_from_download_api_env,
    load_platform_credentials,
    platform_creds_path,
    save_platform_credentials,
)
from wechat_archive.progress_ui import CrawlProgress
from wechat_archive.services.fetch_content import fetch_pending_contents
from wechat_archive.services.fetch_history import fetch_history_for_accounts
from wechat_archive.services.import_accounts import import_name_list
from wechat_archive.services.import_schinza import import_schinza_credentials
from wechat_archive.services.import_schinza_export import import_schinza_export
from wechat_archive.services.ops import (
    account_summary_rows,
    aggregate_errors,
    build_doctor_report,
    reset_failed_resolves,
)
from wechat_archive.services.resolve_accounts import resolve_pending_accounts
from wechat_archive.services.session_import import (
    SessionImportError,
    import_session_from_har,
)
from wechat_archive.url_utils import article_sn, normalize_article_url
from wechat_archive.schinza_client import summarize_schinza_accounts

console = Console()


def _db(cfg) -> Database:
    ensure_dirs(cfg)
    return Database(cfg["paths"]["database"], cfg.get("database"))


def _progress_log_path(cfg: dict, name: str) -> Path:
    db_path = Path(cfg["paths"]["database"])
    return db_path.parent / "logs" / f"{name}_progress.jsonl"


@click.group()
@click.option("--config", "config_path", default=None, help="配置文件路径")
@click.option(
    "--profile",
    type=click.Choice(sorted(PROFILES.keys())),
    default=None,
    help="节奏档位：safe / balanced / fast（仅 --force-legacy 在线命令使用）",
)
@click.option(
    "--force-legacy",
    is_flag=True,
    default=False,
    help="启用已弃用的在线 history/content/resolve/pilot/serve",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str | None,
    profile: str | None,
    force_legacy: bool,
) -> None:
    """将 Schinza 离线归档合并进 SQLite（兼容原 wechat_archive.db）。"""
    try:
        cfg = load_config(config_path, profile=profile)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    ensure_dirs(cfg)
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = cfg
    ctx.obj["force_legacy"] = force_legacy
    if profile:
        console.print(f"[dim]profile={profile}[/dim]")


LEGACY_ONLINE_HINT = (
    "在线抓取已停用。请在 Schinza 拉列表与正文，再运行：\n"
    "  python run.py import-list\n"
    "  python run.py import-schinza-export --manifest <manifest.json> "
    "--articles-dir <articles/>\n"
    "若确需旧命令：python run.py --force-legacy history"
)


def _require_legacy(ctx: click.Context) -> None:
    if not ctx.obj.get("force_legacy"):
        raise click.ClickException(LEGACY_ONLINE_HINT)


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
    """[已弃用] 解析公众号 fakeid/__biz。请改用 Schinza + import-schinza-export。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)
    with CrawlProgress(
        title="解析账号",
        log_path=_progress_log_path(cfg, "resolve"),
        console=console,
    ) as prog:
        stats = resolve_pending_accounts(
            db, client, cfg, limit=limit, progress=prog.callback
        )
    console.print(f"解析完成: {stats}")
    _seed_sample_articles(db)
    console.print("已将样例文章写入 articles（便于先试正文抓取）。")


@cli.command("set-platform-creds")
@click.option("--token", prompt=True, help="公众平台 token")
@click.option("--cookie", prompt=True, help="公众平台 Cookie 整段")
@click.option("--nickname", default="", help="登录的公众号昵称（可选）")
@click.option("--fakeid", default="", help="登录公众号 fakeid（可选）")
@click.option(
    "--expire-days",
    default=4,
    show_default=True,
    type=click.IntRange(1, 14),
    help="本地记录的预计有效天数",
)
@click.pass_context
def set_platform_creds_cmd(
    ctx: click.Context,
    token: str,
    cookie: str,
    nickname: str,
    fakeid: str,
    expire_days: int,
) -> None:
    """[已弃用] 手动写入公众平台 token/cookie。"""
    _require_legacy(ctx)
    import time

    cfg = ctx.obj["cfg"]
    creds = PlatformCredentials(
        token=token.strip(),
        cookie=cookie.strip(),
        nickname=nickname.strip(),
        fakeid=fakeid.strip(),
        expire_time_ms=int((time.time() + expire_days * 24 * 3600) * 1000),
        source="manual",
    )
    path = save_platform_credentials(cfg, creds)
    console.print(f"[green]已保存公众平台凭证:[/green] {path}")


@cli.command("import-platform-from-download-api")
@click.option(
    "--env",
    "env_path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="wechat-download-api 的 .env；默认读 config.yaml 的 platform.download_api_env",
)
@click.pass_context
def import_platform_from_download_api_cmd(
    ctx: click.Context, env_path: Path | None
) -> None:
    """[已弃用] 从 wechat-download-api 导入凭证。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    resolved = env_path
    if resolved is None:
        raw = (cfg.get("platform") or {}).get("download_api_env")
        if not raw:
            raise click.ClickException(
                "未指定 --env，且 config.yaml 中没有 platform.download_api_env。"
                "同级布局默认应为 ../wechat-download-api/.env"
            )
        resolved = Path(raw)
    if not resolved.exists():
        raise click.ClickException(
            f"找不到凭证文件: {resolved}\n"
            "请确认已 clone wechat-download-api 到同级目录，并完成扫码登录。\n"
            "期望布局: wechat-work/{wechat_crawler, wechat-download-api}/"
        )
    try:
        creds = import_credentials_from_download_api_env(resolved)
    except PlatformAuthError as exc:
        raise click.ClickException(str(exc)) from exc
    path = save_platform_credentials(cfg, creds)
    console.print(f"[green]已导入凭证:[/green] {path}")
    console.print(f"来源文件: {resolved}")
    if creds.nickname:
        console.print(f"登录公众号: {creds.nickname}")


def _schinza_accounts_path(cfg: dict) -> Path:
    raw = (cfg.get("platform") or {}).get("schinza_accounts_path")
    if not raw:
        raise click.ClickException(
            "未配置 platform.schinza_accounts_path；"
            "默认同级布局应指向 ../schinza-wechat-certificate-main/data/accounts.json"
        )
    return Path(raw)


@cli.command("import-schinza-credentials")
@click.option(
    "--file",
    "accounts_file",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Schinza data/accounts.json；默认读 config.yaml",
)
@click.pass_context
def import_schinza_credentials_cmd(
    ctx: click.Context, accounts_file: Path | None
) -> None:
    """[已弃用] 按名称映射 Schinza 账号以供在线 history。请改用 import-schinza-export。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    source = accounts_file or _schinza_accounts_path(cfg)
    try:
        result = import_schinza_credentials(_db(cfg), source)
    except PlatformAuthError as exc:
        raise click.ClickException(str(exc)) from exc
    safe_result = {key: value for key, value in result.items() if key != "details"}
    console.print_json(data=safe_result)
    if result["duplicates"]:
        console.print("[yellow]存在同名 Schinza 账号，已跳过以避免绑错号。[/yellow]")
    if result["unmatched_crawler"]:
        console.print(
            f"[yellow]{result['unmatched_crawler']} 个名单账号未匹配；"
            "请确保 Schinza 名称与 name_list.xlsx 完全一致。[/yellow]"
        )


@cli.command("import-schinza-export")
@click.option(
    "--manifest",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Schinza 导出的列表 JSON 或后台归档 manifest.json",
)
@click.option(
    "--articles-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Markdown 正文目录；默认从 manifest 所在目录递归查找",
)
@click.option("--dry-run", is_flag=True, help="只报告合并结果，不写入 SQLite")
@click.pass_context
def import_schinza_export_cmd(
    ctx: click.Context,
    manifest: Path,
    articles_dir: Path | None,
    dry_run: bool,
) -> None:
    """离线合并 Schinza 列表和 Markdown；不会访问微信。"""
    try:
        result = import_schinza_export(
            _db(ctx.obj["cfg"]),
            manifest,
            articles_dir=articles_dir,
            dry_run=dry_run,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    console.print_json(data=result)
    if not result.get("matched_account"):
        console.print(
            "[yellow]未匹配 crawler 账号；请先 import-list，并确保公众号名称完全一致。[/yellow]"
        )
    elif dry_run:
        console.print("[green]dry-run 完成，数据库未修改。[/green]")
    else:
        console.print("[green]Schinza 导出已离线合并到 SQLite。[/green]")


@cli.command("schinza-status")
@click.option(
    "--file",
    "accounts_file",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
)
@click.pass_context
def schinza_status_cmd(ctx: click.Context, accounts_file: Path | None) -> None:
    """检查 Schinza 凭证文件，仅输出计数和同名冲突，不显示密钥。"""
    cfg = ctx.obj["cfg"]
    source = accounts_file or _schinza_accounts_path(cfg)
    try:
        summary = summarize_schinza_accounts(source)
    except PlatformAuthError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print_json(data=summary)
    mapped = _db(cfg).fetchone(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN list_status='need_session' THEN 1 ELSE 0 END) AS expired
        FROM accounts
        WHERE history_backend='schinza_getmsg' AND wechat_biz IS NOT NULL
        """
    )
    console.print(
        f"SQLite 已映射: {int(mapped['total'] or 0)}，"
        f"need_session: {int(mapped['expired'] or 0)}"
    )


@cli.command("platform-status")
@click.pass_context
def platform_status_cmd(ctx: click.Context) -> None:
    """检查当前历史后端及其凭证状态。"""
    import time

    cfg = ctx.obj["cfg"]
    backend = (cfg.get("platform") or {}).get("backend", "platform")
    console.print(f"历史后端: {backend}")
    if backend == "schinza_getmsg":
        ctx.invoke(schinza_status_cmd)
        return
    if backend == "download_api":
        base = (cfg.get("platform") or {}).get("download_api_base_url")
        console.print(f"download_api: {base}")
        try:
            client = build_history_client(cfg)
            # 轻量探测：空搜索不应抛未登录以外的错误
            client.search_accounts("微信")
            console.print("[green]download_api 可访问且已登录[/green]")
        except Exception as exc:
            console.print(f"[yellow]download_api 探测失败:[/yellow] {exc}")
        return

    creds = load_platform_credentials(cfg)
    path = platform_creds_path(cfg)
    if not creds:
        console.print(f"[yellow]未配置凭证:[/yellow] {path}")
        console.print(
            "请先在同级目录启动 wechat-download-api 并扫码，然后执行:\n"
            "  python run.py import-platform-from-download-api\n"
            "（默认读取 ../wechat-download-api/.env）"
        )
        return
    console.print(f"凭证文件: {path}")
    console.print(f"来源: {creds.source}")
    console.print(f"昵称: {creds.nickname or '(未填)'}")
    if creds.expire_time_ms:
        remain_h = (creds.expire_time_ms / 1000 - time.time()) / 3600
        console.print(f"预计剩余: {remain_h:.1f} 小时")
        if creds.expired:
            console.print("[red]已过期，请重新登录并更新凭证[/red]")
    try:
        client = build_history_client(cfg)
        hits = client.search_accounts(creds.nickname or "卫生健康")
        console.print(f"[green]凭证可用[/green]（searchbiz 返回 {len(hits)} 条）")
    except Exception as exc:
        console.print(f"[red]凭证探测失败:[/red] {exc}")


@cli.command("history")
@click.option("--limit", default=None, type=int, help="最多处理多少个账号")
@click.option(
    "--refresh",
    is_flag=True,
    help="同时增量刷新已完成(done)账号；默认跳过 done 以节省配额",
)
@click.pass_context
def history_cmd(ctx: click.Context, limit: int | None, refresh: bool) -> None:
    """[已弃用] 在线拉取历史列表。请在 Schinza 拉列表后 import-schinza-export。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    try:
        build_history_client(cfg)
    except PlatformAuthError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        if (cfg.get("platform") or {}).get("backend") == "schinza_getmsg":
            console.print(
                "推荐流程：\n"
                "1. 在 Schinza 中为目标公众号刷新凭证\n"
                "2. python run.py import-schinza-credentials\n"
                "3. python run.py schinza-status\n"
                "详见 README。"
            )
        else:
            console.print(
                "Legacy 流程：重新登录公众平台并导入凭证；详见 README。"
            )
        return
    client = HttpClient(cfg)
    with CrawlProgress(
        title="历史列表",
        log_path=_progress_log_path(cfg, "history"),
        console=console,
    ) as prog:
        stats = fetch_history_for_accounts(
            db,
            client,
            cfg,
            limit_accounts=limit,
            progress=prog.callback,
            refresh=refresh,
        )
    console.print(f"历史列表完成: {stats}")
    if stats.get("circuit_open"):
        console.print(
            f"[yellow]{stats.get('circuit_message') or '已因频控熔断暂停'}[/yellow]"
        )


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
    """[已弃用] 旧 HAR 导入。"""
    _require_legacy(ctx)
    console.print(
        "[yellow]请优先用 Schinza 刷新凭证并运行 "
        "python run.py import-schinza-credentials。旧 HAR 命令仅兼容保留。[/yellow]"
    )
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

    table = Table(title="旧版微信会话导入成功（仅兼容保留）")
    table.add_column("字段")
    table.add_column("结果")
    table.add_row("名称", result["name"])
    table.add_row("文件", result["path"])
    table.add_row("公众号 biz", result["biz"] or "HAR 中未提供")
    table.add_row("捕获时间", result["captured_at"] or "HAR 中未提供")
    table.add_row("候选请求", str(result["candidates_found"]))
    console.print(table)

    if delete_source:
        try:
            har_file.unlink()
            console.print(f"[green]已删除敏感 HAR: {har_file}[/green]")
        except OSError as exc:
            console.print(
                f"[yellow]会话已导入，但无法删除 HAR，请手动删除: {exc}[/yellow]"
            )


@cli.command("content")
@click.option("--limit", default=None, type=int, help="最多抓取多少篇")
@click.pass_context
def content_cmd(ctx: click.Context, limit: int | None) -> None:
    """[已弃用] 在线抓取正文。请在 Schinza 归档后 import-schinza-export。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)
    with CrawlProgress(
        title="抓取正文",
        log_path=_progress_log_path(cfg, "content"),
        console=console,
    ) as prog:
        stats = fetch_pending_contents(
            db, client, cfg, limit=limit, progress=prog.callback
        )
    console.print(f"正文抓取完成: {stats}")


@cli.command("status")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON（便于周报/脚本）")
@click.pass_context
def status_cmd(ctx: click.Context, as_json: bool = False) -> None:
    """查看进度概览（账号漏斗 + 文章漏斗 + 建议动作）。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    report = build_doctor_report(db, cfg)
    if as_json:
        console.print_json(data=report)
        return

    table = Table(title="账号状态")
    table.add_column("resolve")
    table.add_column("list")
    table.add_column("count", justify="right")
    for row in report["accounts"]:
        table.add_row(row["resolve_status"], row["list_status"], str(row["count"]))
    console.print(table)

    table2 = Table(title="文章状态")
    table2.add_column("status")
    table2.add_column("count", justify="right")
    for status, count in sorted(report["articles"].items()):
        table2.add_row(status, str(count))
    console.print(table2)

    kinds = report["errors"].get("list_by_kind") or {}
    if kinds:
        console.print(
            "历史错误分类: "
            + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
        )

    platform = report["platform"]
    if platform.get("backend") == "schinza_getmsg":
        schinza = platform.get("schinza") or {}
        console.print(
            "Schinza: "
            f"active={schinza.get('active', 0)} expired={schinza.get('expired', 0)} "
            f"mapped={platform.get('mapped_accounts', 0)} "
            f"need_session={platform.get('mapped_need_session', 0)}"
        )
    elif platform.get("backend") == "download_api":
        console.print(
            f"历史后端: download_api "
            f"({(cfg.get('platform') or {}).get('download_api_base_url')})"
        )
    elif platform.get("configured"):
        remain = ""
        if platform.get("remain_hours") is not None:
            remain = f"，预计剩余 {platform['remain_hours']:.1f}h"
        console.print(
            f"公众平台凭证: 已配置（来源 {platform.get('source')}{remain}）"
        )
    else:
        console.print("公众平台凭证: [yellow]未配置[/yellow]")
    console.print(f"数据库: {report['database']}")
    if report.get("stale_running_accounts"):
        console.print(
            f"[yellow]可疑 running 账号: {report['stale_running_accounts']} "
            "（超过 30 分钟未更新）[/yellow]"
        )
    console.print("[bold]建议下一步[/bold]")
    for action in report["next_actions"]:
        console.print(f"  • {action}")


@cli.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_context
def doctor_cmd(ctx: click.Context, as_json: bool) -> None:
    """运维体检：凭证、错误 TopN、卡住任务、建议动作。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    report = build_doctor_report(db, cfg)
    if as_json:
        console.print_json(data=report)
        return
    console.print(f"[bold]Doctor[/bold] · db={report['database']}")
    platform = report["platform"]
    console.print(
        f"平台: backend={platform.get('backend')} configured={platform.get('configured')} "
        f"expired={platform.get('expired')} remain_h={platform.get('remain_hours')}"
    )
    if report.get("stale_running_accounts"):
        console.print(
            f"[yellow]stale running accounts: {report['stale_running_accounts']}[/yellow]"
        )
    err = report["errors"]
    table = Table(title="历史 list_error Top")
    table.add_column("error")
    table.add_column("count", justify="right")
    for row in err.get("list_errors") or []:
        table.add_row(str(row["error"])[:120], str(row["count"]))
    if err.get("list_errors"):
        console.print(table)
    else:
        console.print("历史 list_error: （无）")
    table_r = Table(title="resolve_error Top")
    table_r.add_column("error")
    table_r.add_column("count", justify="right")
    for row in err.get("resolve_errors") or []:
        table_r.add_row(str(row["error"])[:120], str(row["count"]))
    if err.get("resolve_errors"):
        console.print(table_r)
    console.print(
        "分类: list="
        + str(err.get("list_by_kind"))
        + " resolve="
        + str(err.get("resolve_by_kind"))
        + " content="
        + str(err.get("content_by_kind"))
    )
    console.print("[bold]建议下一步[/bold]")
    for action in report["next_actions"]:
        console.print(f"  • {action}")


@cli.command("errors")
@click.option("--limit", default=20, type=int, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def errors_cmd(ctx: click.Context, limit: int, as_json: bool) -> None:
    """按 list/resolve/content 错误聚合。"""
    db = _db(ctx.obj["cfg"])
    data = aggregate_errors(db, limit=limit)
    if as_json:
        console.print_json(data=data)
        return
    for title, key in (
        ("历史列表错误", "list_errors"),
        ("解析错误", "resolve_errors"),
        ("正文错误", "content_errors"),
    ):
        table = Table(title=title)
        table.add_column("error")
        table.add_column("count", justify="right")
        rows = data.get(key) or []
        for row in rows:
            table.add_row(str(row["error"])[:160], str(row["count"]))
        if rows:
            console.print(table)
        else:
            console.print(f"{title}: （无）")
    console.print(f"list_by_kind: {data.get('list_by_kind')}")
    console.print(f"resolve_by_kind: {data.get('resolve_by_kind')}")
    console.print(f"content_by_kind: {data.get('content_by_kind')}")


@cli.command("retry-resolve")
@click.option("--limit", default=None, type=int, help="最多重置多少个账号")
@click.pass_context
def retry_resolve_cmd(ctx: click.Context, limit: int | None) -> None:
    """[已弃用] 将 resolve=failed 的账号重新放回 pending。"""
    _require_legacy(ctx)
    db = _db(ctx.obj["cfg"])
    n = reset_failed_resolves(db, limit=limit)
    console.print(f"已重置 {n} 个解析失败账号为 pending；请再运行 python run.py resolve")


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, type=int, show_default=True)
@click.pass_context
def serve_cmd(ctx: click.Context, host: str, port: int) -> None:
    """[已弃用] 启动本地 Web 管理台。"""
    _require_legacy(ctx)
    import uvicorn

    uvicorn.run("wechat_archive.api.app:app", host=host, port=port)


@cli.command("retry-failed")
@click.option("--limit", default=None, type=int, help="最多重置多少篇")
@click.pass_context
def retry_failed_cmd(ctx: click.Context, limit: int | None) -> None:
    """[已弃用] 将已耗尽重试的正文任务重新放回队列。"""
    _require_legacy(ctx)
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


@cli.command("export-account-summary")
@click.option("--out", default="export/account_summary.jsonl", help="输出路径")
@click.pass_context
def export_account_summary_cmd(ctx: click.Context, out: str) -> None:
    """按账号导出篇数、时间跨度与状态汇总（论文附录友好）。"""
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parents[1] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = account_summary_rows(db)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    console.print(f"[green]已导出 {len(rows)} 个账号汇总 → {out_path}[/green]")


@cli.command("backup-db")
@click.option("--out", default=None, help="备份文件路径；默认写入 data/backups")
@click.pass_context
def backup_db_cmd(ctx: click.Context, out: str | None) -> None:
    """使用 SQLite online backup API 创建一致性备份。"""
    cfg = ctx.obj["cfg"]
    db = Database(cfg["paths"]["database"], cfg.get("database"))
    if out:
        destination = Path(out)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = Path(cfg["paths"]["database"]).parent / "backups" / (
            f"wechat_archive-{stamp}.db"
        )
    if not destination.is_absolute():
        destination = Path(__file__).resolve().parents[1] / destination
    result = db.backup(destination)
    console.print(f"[green]一致性备份完成:[/green] {result}")


@cli.command("maintain-db")
@click.option("--event-retention-days", default=30, type=click.IntRange(min=1))
@click.pass_context
def maintain_db_cmd(ctx: click.Context, event_retention_days: int) -> None:
    """检查数据库、更新查询统计、清理事件并截断 WAL。"""
    cfg = ctx.obj["cfg"]
    db = Database(cfg["paths"]["database"], cfg.get("database"))
    result = db.maintain(event_retention_days)
    console.print(f"数据库维护完成: {result}")


@cli.command("pilot")
@click.option("--limit", default=None, type=int, help="限制账号数")
@click.pass_context
def pilot_cmd(ctx: click.Context, limit: int | None) -> None:
    """[已弃用] 一键在线试点。请改用 Schinza + import-schinza-export。"""
    _require_legacy(ctx)
    cfg = ctx.obj["cfg"]
    db = _db(cfg)
    client = HttpClient(cfg)

    console.print("[bold]1/4 导入名单[/bold]")
    stats = import_name_list(db, cfg["paths"]["name_list"])
    console.print(stats)

    backend = (cfg.get("platform") or {}).get("backend", "platform")
    if backend == "schinza_getmsg":
        console.print("[bold]2/4 导入 Schinza 账号映射[/bold]")
        stats = import_schinza_credentials(db, _schinza_accounts_path(cfg))
        console.print({key: value for key, value in stats.items() if key != "details"})
    else:
        console.print("[bold]2/4 解析 fakeid/__biz[/bold]")
        with CrawlProgress(
            title="解析账号",
            log_path=_progress_log_path(cfg, "resolve"),
            console=console,
        ) as prog:
            stats = resolve_pending_accounts(
                db, client, cfg, limit=limit, progress=prog.callback
            )
        console.print(stats)
        _seed_sample_articles(db)

    platform_ok = False
    try:
        build_history_client(cfg)
        platform_ok = True
    except PlatformAuthError:
        platform_ok = False

    if platform_ok:
        console.print(f"[bold]3/4 拉取历史列表[/bold]（{backend}）")
        with CrawlProgress(
            title="历史列表",
            log_path=_progress_log_path(cfg, "history"),
            console=console,
        ) as prog:
            stats = fetch_history_for_accounts(
                db,
                client,
                cfg,
                limit_accounts=limit,
                progress=prog.callback,
                refresh=False,
            )
        console.print(stats)
    else:
        console.print(
            "[bold]3/4 跳过历史列表[/bold]（当前后端凭证不可用）"
        )

    console.print("[bold]4/4 抓取正文[/bold]")
    with CrawlProgress(
        title="抓取正文",
        log_path=_progress_log_path(cfg, "content"),
        console=console,
    ) as prog:
        stats = fetch_pending_contents(
            db, client, cfg, limit=limit, progress=prog.callback
        )
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
