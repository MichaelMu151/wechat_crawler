from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wechat_archive.config import ensure_dirs, load_config
from wechat_archive.db import Database
from wechat_archive.services.jobs import JobRunner
from wechat_archive.services.sessions import load_sessions
from wechat_archive.url_utils import article_sn, normalize_article_url

cfg = load_config(os.environ.get("WECHAT_ARCHIVE_CONFIG"))
ensure_dirs(cfg)
db = Database(cfg["paths"]["database"])
runner = JobRunner(db, cfg, workers=int(cfg.get("jobs", {}).get("workers", 1)))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    stale_minutes = int(cfg.get("jobs", {}).get("stale_minutes", 30))
    db.recover_stale_work(stale_minutes=stale_minutes)
    runner.resume_pending()
    yield
    runner.executor.shutdown(wait=False, cancel_futures=False)


app = FastAPI(
    title="Wechat Archive API",
    version="0.2.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobCreate(BaseModel):
    stage: Literal["import", "resolve", "history", "content"]
    limit: int | None = Field(default=None, ge=1, le=100_000)


class PublicArticleCreate(BaseModel):
    url: str = Field(min_length=12, max_length=2048)
    account_name: str = Field(default="公开链接", max_length=200)


def row_dict(row: Any) -> dict[str, Any]:
    value = dict(row)
    for key in ("payload_json", "result_json", "data_json"):
        if value.get(key):
            try:
                value[key.removesuffix("_json")] = json.loads(value.pop(key))
            except json.JSONDecodeError:
                pass
    return value


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database": str(db.path),
        "sessions": len(load_sessions(cfg["paths"]["sessions_dir"])),
    }


@app.get("/api/v1/stats")
def stats() -> dict[str, Any]:
    account_rows = db.fetchall(
        "SELECT resolve_status, list_status, COUNT(*) AS count FROM accounts "
        "GROUP BY resolve_status, list_status"
    )
    article_rows = db.fetchall(
        "SELECT status, COUNT(*) AS count FROM articles GROUP BY status"
    )
    job_rows = db.fetchall(
        "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
    )
    return {
        "accounts": [dict(row) for row in account_rows],
        "articles": {row["status"]: row["count"] for row in article_rows},
        "jobs": {row["status"]: row["count"] for row in job_rows},
        "sessions": len(load_sessions(cfg["paths"]["sessions_dir"])),
    }


@app.get("/api/v1/accounts")
def accounts(
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where = ""
    params: list[Any] = []
    if q:
        where = "WHERE nickname_input LIKE ? OR account_name LIKE ? OR biz LIKE ?"
        term = f"%{q}%"
        params.extend([term, term, term])
    total = db.fetchone(f"SELECT COUNT(*) AS count FROM accounts {where}", tuple(params))
    params.extend([limit, offset])
    rows = db.fetchall(
        f"""
        SELECT a.*,
               (SELECT COUNT(*) FROM articles ar WHERE ar.account_id=a.id) article_count
        FROM accounts a {where}
        ORDER BY a.id DESC LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    return {"items": [dict(row) for row in rows], "total": total["count"] if total else 0}


@app.post("/api/v1/articles/public", status_code=201)
def add_public_article(body: PublicArticleCreate) -> dict[str, Any]:
    if "mp.weixin.qq.com/" not in body.url:
        raise HTTPException(400, "仅接受 mp.weixin.qq.com 公开文章链接")
    normalized = normalize_article_url(body.url)
    with db.connection() as conn:
        account = conn.execute(
            "SELECT id FROM accounts WHERE nickname_input=? AND biz IS NULL",
            (body.account_name,),
        ).fetchone()
        if account is None:
            cursor = conn.execute(
                """
                INSERT INTO accounts (
                    nickname_input, account_name, resolve_status, list_status
                ) VALUES (?, ?, 'pending', 'pending')
                """,
                (body.account_name, body.account_name),
            )
            account_id = int(cursor.lastrowid)
        else:
            account_id = int(account["id"])
        existing = conn.execute(
            "SELECT id FROM articles WHERE normalized_url=? OR url=?",
            (normalized, body.url),
        ).fetchone()
        if existing:
            return {"id": existing["id"], "created": False}
        cursor = conn.execute(
            """
            INSERT INTO articles (
                account_id, sn, url, normalized_url, status
            ) VALUES (?, ?, ?, ?, 'listed')
            """,
            (account_id, article_sn(body.url), body.url, normalized),
        )
        return {"id": int(cursor.lastrowid), "created": True}


@app.get("/api/v1/articles")
def articles(
    q: str | None = None,
    status: str | None = None,
    account_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
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
        clauses.append(
            "(ar.title LIKE ? OR ar.author LIKE ? OR ar.digest LIKE ? "
            "OR ar.content_text LIKE ?)"
        )
        term = f"%{q}%"
        params.extend([term, term, term, term])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = db.fetchone(
        f"SELECT COUNT(*) count FROM articles ar {where}", tuple(params)
    )
    params.extend([limit, offset])
    rows = db.fetchall(
        f"""
        SELECT ar.id, ar.account_id, ar.title, ar.author, ar.digest,
               ar.publish_time, ar.url, ar.cover_url, ar.status,
               ar.retry_count, ar.content_error, a.account_name
        FROM articles ar JOIN accounts a ON a.id=ar.account_id
        {where}
        ORDER BY COALESCE(ar.publish_ts, 0) DESC, ar.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    return {"items": [dict(row) for row in rows], "total": total["count"] if total else 0}


@app.get("/api/v1/articles/{article_id}")
def article(article_id: int) -> dict[str, Any]:
    row = db.fetchone(
        """
        SELECT ar.*, a.account_name FROM articles ar
        JOIN accounts a ON a.id=ar.account_id WHERE ar.id=?
        """,
        (article_id,),
    )
    if row is None:
        raise HTTPException(404, "文章不存在")
    return dict(row)


@app.get("/api/v1/articles/{article_id}/preview", response_class=HTMLResponse)
def article_preview(article_id: int) -> HTMLResponse:
    row = db.fetchone("SELECT content_html FROM articles WHERE id=?", (article_id,))
    if row is None:
        raise HTTPException(404, "文章不存在")
    return HTMLResponse(
        row["content_html"] or "<p>暂无正文</p>",
        headers={
            "Content-Security-Policy": "default-src 'none'; img-src data: https:; "
            "style-src 'unsafe-inline'; media-src https:; sandbox"
        },
    )


@app.get("/api/v1/jobs")
def jobs(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return [
        row_dict(row)
        for row in db.fetchall("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    ]


@app.post("/api/v1/jobs", status_code=202)
def create_job(body: JobCreate) -> dict[str, int]:
    return {"id": runner.create(body.stage, {"limit": body.limit})}


@app.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: int) -> dict[str, bool]:
    if db.fetchone("SELECT id FROM jobs WHERE id=?", (job_id,)) is None:
        raise HTTPException(404, "任务不存在")
    runner.cancel(job_id)
    return {"accepted": True}


@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: int) -> StreamingResponse:
    if db.fetchone("SELECT id FROM jobs WHERE id=?", (job_id,)) is None:
        raise HTTPException(404, "任务不存在")

    async def stream() -> AsyncIterator[str]:
        last_id = 0
        while True:
            events = db.fetchall(
                "SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id",
                (job_id, last_id),
            )
            for event in events:
                last_id = event["id"]
                yield f"data: {json.dumps(row_dict(event), ensure_ascii=False)}\n\n"
            job = db.fetchone("SELECT status FROM jobs WHERE id=?", (job_id,))
            if job and job["status"] in {"done", "failed", "cancelled"} and not events:
                break
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/sessions")
def sessions() -> list[dict[str, Any]]:
    # Deliberately expose metadata only; credentials never cross the API.
    return [
        {"name": session["name"], "configured": True}
        for session in load_sessions(cfg["paths"]["sessions_dir"])
    ]


web_dist = Path(__file__).resolve().parents[2] / "web_ui" / "dist"
if web_dist.exists():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
