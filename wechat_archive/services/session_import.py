from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

MAX_HAR_BYTES = 100 * 1024 * 1024
SAFE_SESSION_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class SessionImportError(ValueError):
    """Raised when a HAR does not contain a usable WeChat history request."""


def import_session_from_har(
    har_path: str | Path,
    sessions_dir: str | Path,
    name: str = "session_1",
    *,
    overwrite: bool = False,
    max_bytes: int = MAX_HAR_BYTES,
) -> dict[str, Any]:
    """Extract the newest usable getmsg request and atomically save its session."""
    source = Path(har_path).expanduser().resolve()
    if not source.is_file():
        raise SessionImportError(f"HAR 文件不存在: {source}")
    size = source.stat().st_size
    if size == 0:
        raise SessionImportError("HAR 文件为空")
    if size > max_bytes:
        raise SessionImportError(
            f"HAR 文件过大（{size / 1024 / 1024:.1f} MB），上限为 "
            f"{max_bytes / 1024 / 1024:.0f} MB"
        )

    session_name = _validate_session_name(name)
    destination_dir = Path(sessions_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{session_name}.yaml"
    if destination.exists() and not overwrite:
        raise SessionImportError(
            f"会话文件已存在: {destination}；确认更新时添加 --overwrite"
        )

    try:
        with source.open("r", encoding="utf-8-sig") as handle:
            har = json.load(handle)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionImportError(
            "无法解析 HAR；请在 Charles 中导出 HTTP Archive (.har)，"
            "不要使用 Charles Session (.chls)"
        ) from exc

    entries = ((har.get("log") or {}).get("entries")) if isinstance(har, dict) else None
    if not isinstance(entries, list):
        raise SessionImportError("HAR 缺少 log.entries，文件格式不正确")

    candidates = [
        candidate
        for index, entry in enumerate(entries)
        if (candidate := _extract_candidate(entry, index)) is not None
    ]
    if not candidates:
        raise SessionImportError(
            "未找到可用的微信历史请求。请确认 HAR 包含 "
            "mp.weixin.qq.com/mp/profile_ext?action=getmsg，且请求中有 "
            "uin、key 和 Cookie"
        )

    selected = max(candidates, key=lambda item: item["sort_key"])
    payload = {
        "name": session_name,
        "enabled": True,
        "uin": selected["uin"],
        "key": selected["key"],
        "cookie": selected["cookie"],
        "note": (
            f"自动导入自 {source.name}; captured_at="
            f"{selected['captured_at'] or 'unknown'}"
        ),
    }
    _atomic_write_yaml(destination, payload)

    return {
        "name": session_name,
        "path": str(destination),
        "biz": selected["biz"],
        "captured_at": selected["captured_at"],
        "candidates_found": len(candidates),
        "source_size_bytes": size,
    }


def _extract_candidate(entry: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    request = entry.get("request")
    if not isinstance(request, dict):
        return None
    url = str(request.get("url") or "")
    parsed = urlparse(url)
    if parsed.hostname != "mp.weixin.qq.com" or parsed.path != "/mp/profile_ext":
        return None

    query = _query_values(request, parsed.query)
    if query.get("action") != "getmsg":
        return None
    uin = (query.get("uin") or "").strip()
    key = (query.get("key") or "").strip()
    cookie = _request_cookie(request)
    if not uin or not key or not cookie:
        return None

    captured_at = str(entry.get("startedDateTime") or "").strip() or None
    return {
        "uin": uin,
        "key": key,
        "cookie": cookie,
        "biz": (query.get("__biz") or "").strip() or None,
        "captured_at": captured_at,
        "sort_key": (_parse_timestamp(captured_at), index),
    }


def _query_values(request: dict[str, Any], raw_query: str) -> dict[str, str]:
    values: dict[str, str] = {}
    query_string = request.get("queryString")
    if isinstance(query_string, list):
        for item in query_string:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if name and name not in values:
                values[name] = str(item.get("value") or "")
    for name, items in parse_qs(raw_query, keep_blank_values=True).items():
        if name not in values and items:
            values[name] = items[0]
    return values


def _request_cookie(request: dict[str, Any]) -> str:
    headers = request.get("headers")
    if isinstance(headers, list):
        for header in headers:
            if (
                isinstance(header, dict)
                and str(header.get("name") or "").lower() == "cookie"
            ):
                value = str(header.get("value") or "").strip()
                if value:
                    return value

    cookies = request.get("cookies")
    if isinstance(cookies, list):
        parts = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "").strip()
            if name:
                parts.append(f"{name}={value}")
        return "; ".join(parts)
    return ""


def _parse_timestamp(value: str | None) -> float:
    if not value:
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def _validate_session_name(name: str) -> str:
    cleaned = name[:-5] if name.lower().endswith(".yaml") else name
    if not SAFE_SESSION_NAME.fullmatch(cleaned):
        raise SessionImportError(
            "会话名称只能包含英文字母、数字、下划线和连字符"
        )
    if cleaned.startswith("example"):
        raise SessionImportError("会话名称不能以 example 开头，该名称会被加载器忽略")
    return cleaned


def _atomic_write_yaml(destination: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                payload,
                handle,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
