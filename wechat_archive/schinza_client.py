"""Schinza credential store and WeChat ``getmsg`` history adapter.

The response parsing follows the MIT-licensed Schinza project:
https://github.com/Alexxxxxxxxxxxxy/schinza-wechat-certificate

Only the small, headless history boundary is implemented here.  The crawler
does not import Schinza's GUI/MITM dependencies and never persists its live
``uin``/``key``/``pass_ticket`` credentials in SQLite or logs.
"""

from __future__ import annotations

import html
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from wechat_archive.parsers.article_html import normalize_content_url
from wechat_archive.platform_client import (
    PlatformAPIError,
    PlatformAuthError,
    PlatformRateLimited,
)

GETMSG_URL = "https://mp.weixin.qq.com/mp/profile_ext"
REQUIRED_CREDENTIALS = ("__biz", "uin", "key")


def _parse_expiry(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def credential_status(row: dict[str, Any], now: datetime | None = None) -> tuple[bool, str]:
    credentials = row.get("credentials")
    if not isinstance(credentials, dict):
        return False, "缺少 credentials"
    missing = [key for key in REQUIRED_CREDENTIALS if not str(credentials.get(key) or "").strip()]
    if missing:
        return False, f"缺少字段: {', '.join(missing)}"
    expires = _parse_expiry(row.get("expires_at"))
    if row.get("status") == "expired" or (expires is not None and expires <= (now or datetime.now())):
        return False, "凭证已过期"
    return True, ""


def load_schinza_accounts(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        raise PlatformAuthError(f"找不到 Schinza 凭证文件: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlatformAuthError(f"无法读取 Schinza 凭证文件: {source}") from exc
    rows = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise PlatformAuthError("Schinza accounts.json 格式错误：accounts 必须是列表")
    return [dict(row) for row in rows if isinstance(row, dict)]


def summarize_schinza_accounts(path: str | Path) -> dict[str, Any]:
    rows = load_schinza_accounts(path)
    active = expired = invalid = 0
    names: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if name:
            if name in names:
                duplicates.add(name)
            names.add(name)
        ok, reason = credential_status(row)
        if ok:
            active += 1
        elif "过期" in reason:
            expired += 1
        else:
            invalid += 1
    return {
        "path": str(Path(path)),
        "total": len(rows),
        "active": active,
        "expired": expired,
        "invalid": invalid,
        "duplicate_names": sorted(duplicates),
    }


def _fully_unquote(value: Any) -> str:
    text = str(value or "").strip()
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def _clean_url(value: Any) -> str:
    text = html.unescape(str(value or "").strip())
    text = text.replace("\\/", "/").replace("\\u0026", "&").replace("\\x26", "&")
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("http://mp.weixin.qq.com"):
        text = "https://" + text[len("http://") :]
    return normalize_content_url(text)


def _url_ids(url: str) -> tuple[str | None, int | None, str]:
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None, None, ""
    mid = str((query.get("mid") or query.get("appmsgid") or [""])[0] or "") or None
    idx_raw = str((query.get("idx") or query.get("itemidx") or [""])[0] or "")
    idx = int(idx_raw) if idx_raw.isdigit() else None
    sn = str((query.get("sn") or [""])[0] or "")
    return mid, idx, sn


def _article_row(item: dict[str, Any], publish_ts: int, ordinal: int) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    url = ""
    for key in ("content_url", "content_url_encoded", "url", "link", "content_url_with_token"):
        if item.get(key):
            url = _clean_url(item[key])
            break
    if not title and not url:
        return None
    mid, idx, sn = _url_ids(url)
    publish_time = (
        datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d %H:%M:%S")
        if publish_ts
        else None
    )
    raw = dict(item)
    raw["_source"] = "schinza_getmsg"
    return {
        "aid": sn or (f"{mid}_{idx or ordinal}" if mid else ""),
        "title": title or "(无标题)",
        "author": str(item.get("author") or "").strip() or None,
        "digest": str(item.get("digest") or "").strip() or None,
        "cover_url": _clean_url(item.get("cover") or item.get("cdn_url") or "") or None,
        "url": url or None,
        "publish_ts": publish_ts or None,
        "publish_time": publish_time,
        "mid": mid,
        "idx": idx or ordinal,
        "raw_list_json": json.dumps(raw, ensure_ascii=False),
    }


def parse_getmsg_payload(payload: dict[str, Any], begin: int = 0) -> dict[str, Any]:
    ret = payload.get("ret")
    errmsg = str(payload.get("errmsg") or "")
    if ret not in (None, 0, "0") and errmsg.lower() != "ok":
        message = f"getmsg failed: ret={ret} msg={errmsg}"[:500]
        lowered = errmsg.lower()
        if ret in (-6, 200013, "-6", "200013") or any(
            key in lowered for key in ("unknownerror", "freq", "频繁", "too many")
        ):
            raise PlatformRateLimited(message)
        if ret in (-3, 200003, 200040, "-3", "200003", "200040") or any(
            key in lowered for key in ("session", "expired", "login", "过期", "失效", "登录")
        ):
            raise PlatformAuthError(message)
        raise PlatformAPIError(message)

    general = payload.get("general_msg_list") or {}
    if isinstance(general, str):
        try:
            general = json.loads(general) if general.strip() else {}
        except json.JSONDecodeError as exc:
            raise PlatformAPIError("getmsg general_msg_list 不是有效 JSON") from exc
    if not isinstance(general, dict):
        raise PlatformAPIError("getmsg general_msg_list 格式错误")

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    pushes = general.get("list") or []
    for message in pushes:
        if not isinstance(message, dict):
            continue
        common = message.get("comm_msg_info") or {}
        publish_ts = int(common.get("datetime") or 0) if isinstance(common, dict) else 0
        head = message.get("app_msg_ext_info")
        if not isinstance(head, dict) or not head:
            continue
        items = [head]
        multi = head.get("multi_app_msg_item_list")
        if isinstance(multi, list):
            items.extend(item for item in multi if isinstance(item, dict))
        root_multi = message.get("multi_app_msg_item_list")
        if isinstance(root_multi, list):
            items.extend(item for item in root_multi if isinstance(item, dict) and item not in items)
        for ordinal, item in enumerate(items, start=1):
            row = _article_row(item, publish_ts, ordinal)
            if row is None:
                continue
            identity = (
                f"{row.get('mid')}|{row.get('idx')}|{row.get('aid')}"
                if row.get("mid")
                else str(row.get("url") or f"{row.get('title')}|{publish_ts}|{ordinal}")
            )
            if identity in seen:
                continue
            seen.add(identity)
            articles.append(row)

    next_offset = payload.get("next_offset")
    try:
        next_begin = int(next_offset) if next_offset is not None else begin + len(pushes)
    except (TypeError, ValueError):
        next_begin = begin + len(pushes)
    can_raw = payload.get("can_msg_continue")
    can_continue = str(can_raw).isdigit() and bool(int(can_raw))
    if not str(can_raw).isdigit():
        can_continue = bool(can_raw)
    return {
        "articles": articles,
        "total": 0,
        "begin": begin,
        "count": len(articles),
        "publish_fetched": len(pushes),
        "next_begin": next_begin,
        "can_continue": can_continue and next_begin > begin,
    }


class SchinzaGetmsgClient:
    def __init__(
        self,
        accounts_path: str | Path,
        timeout: float = 30,
        retries: int = 2,
        backoff_factor: float = 1.0,
    ):
        self.accounts_path = Path(accounts_path)
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        # Validate early so CLI failures are actionable.
        load_schinza_accounts(self.accounts_path)

    def _credentials_for_biz(self, biz: str) -> dict[str, str]:
        matches = []
        for row in load_schinza_accounts(self.accounts_path):
            credentials = row.get("credentials")
            if not isinstance(credentials, dict):
                continue
            row_biz = str(credentials.get("__biz") or row.get("biz") or "").strip()
            if row_biz == biz:
                matches.append(row)
        if not matches:
            raise PlatformAuthError(f"Schinza 中没有公众号 {biz} 的凭证，请重新导入映射")
        row = matches[0]
        ok, reason = credential_status(row)
        if not ok:
            raise PlatformAuthError(f"Schinza 凭证不可用: {reason}")
        credentials = dict(row["credentials"])
        return {
            key: _fully_unquote(credentials.get(key))
            for key in (
                "__biz",
                "uin",
                "key",
                "pass_ticket",
                "appmsg_token",
                "wxtoken",
                "devicetype",
                "clientversion",
            )
            if credentials.get(key) is not None
        }

    def list_articles(
        self,
        fakeid: str,
        begin: int = 0,
        count: int = 10,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        del keyword
        credentials = self._credentials_for_biz(fakeid)
        params = {
            "action": "getmsg",
            "__biz": credentials["__biz"],
            "f": "json",
            "offset": str(max(0, int(begin))),
            "count": str(min(max(int(count), 1), 20)),
            "is_ok": "1",
            "scene": "124",
            "uin": credentials["uin"],
            "key": credentials["key"],
            "wxtoken": credentials.get("wxtoken", ""),
            "devicetype": credentials.get("devicetype", ""),
            "clientversion": credentials.get("clientversion", "0"),
            "x5": "0",
        }
        for key in ("pass_ticket", "appmsg_token"):
            if credentials.get(key):
                params[key] = credentials[key]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 "
                "MicroMessenger/7.0.20 WindowsWechat"
            ),
            "Referer": (
                "https://mp.weixin.qq.com/mp/profile_ext?action=home"
                f"&__biz={credentials['__biz']}&scene=124#wechat_redirect"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        cookies = {"wxuin": credentials["uin"]}
        if credentials.get("pass_ticket"):
            cookies["pass_ticket"] = credentials["pass_ticket"]

        session = requests.Session()
        session.trust_env = False
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = session.get(
                    GETMSG_URL,
                    params=params,
                    headers=headers,
                    cookies=cookies,
                    timeout=self.timeout,
                )
                if response.status_code == 429:
                    raise PlatformRateLimited("getmsg HTTP 429")
                if response.status_code >= 500:
                    raise requests.HTTPError(f"getmsg HTTP {response.status_code}")
                if response.status_code in (401, 403):
                    raise PlatformAuthError(f"getmsg HTTP {response.status_code}")
                response.raise_for_status()
                try:
                    payload = response.json()
                except (ValueError, json.JSONDecodeError) as exc:
                    body = response.text[:300].lower()
                    if any(
                        marker in body
                        for marker in ("访问过于频繁", "操作频繁", "too many requests")
                    ):
                        raise PlatformRateLimited("getmsg 返回微信频控页面") from exc
                    raise PlatformAuthError(
                        f"getmsg 返回非 JSON（HTTP {response.status_code}），会话可能失效"
                    ) from exc
                if not isinstance(payload, dict):
                    raise PlatformAPIError("getmsg 响应格式错误")
                return parse_getmsg_payload(payload, begin=begin)
            except PlatformRateLimited:
                raise
            except PlatformAuthError:
                raise
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.backoff_factor * (2**attempt))
        raise PlatformAPIError(f"getmsg 网络请求失败: {type(last_error).__name__}")
