from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from wechat_archive.parsers.article_html import normalize_content_url


def _item_to_articles(item: dict[str, Any]) -> list[dict[str, Any]]:
    """把 history API 的一条消息展开成一篇或多篇（多图文）。"""
    if "app_msg_ext_info" not in item:
        return []
    comm = item.get("comm_msg_info") or {}
    publish_ts = comm.get("datetime")
    publish_time = None
    if publish_ts:
        publish_time = datetime.fromtimestamp(int(publish_ts)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    articles: list[dict[str, Any]] = []
    primary = item["app_msg_ext_info"]
    articles.append(_ext_to_row(primary, publish_ts, publish_time, idx=1, raw=item))

    for i, sub in enumerate(primary.get("multi_app_msg_item_list") or [], start=2):
        articles.append(_ext_to_row(sub, publish_ts, publish_time, idx=i, raw=sub))
    return articles


def _ext_to_row(
    ext: dict[str, Any],
    publish_ts: int | None,
    publish_time: str | None,
    idx: int,
    raw: Any,
) -> dict[str, Any]:
    url = normalize_content_url(ext.get("content_url") or "")
    return {
        "title": html.unescape(ext.get("title") or "").strip() or None,
        "author": (ext.get("author") or "").strip() or None,
        "digest": html.unescape(ext.get("digest") or "").strip() or None,
        "cover_url": normalize_content_url(ext.get("cover") or "") or None,
        "url": url or None,
        "publish_ts": int(publish_ts) if publish_ts else None,
        "publish_time": publish_time,
        "idx": idx,
        "raw_list_json": json.dumps(raw, ensure_ascii=False),
    }


def parse_history_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """
    解析 profile_ext getmsg 返回。
    Returns: (articles, can_continue)
    """
    if not payload:
        return [], False

    # 常见错误
    err = (payload.get("base_resp") or {}).get("err_msg") or payload.get("errmsg")
    ret = (payload.get("base_resp") or {}).get("ret")
    if ret not in (None, 0, "0"):
        raise RuntimeError(f"history api error ret={ret} msg={err} raw={payload}")

    general = payload.get("general_msg_list")
    if general is None:
        # 有时直接失败文案
        raise RuntimeError(f"missing general_msg_list: {payload}")

    if isinstance(general, str):
        data = json.loads(general)
    else:
        data = general

    items = data.get("list") or []
    articles: list[dict[str, Any]] = []
    for item in items:
        articles.extend(_item_to_articles(item))

    can_continue = str(payload.get("can_msg_continue", "0")) in ("1", "true", "True")
    return articles, can_continue
