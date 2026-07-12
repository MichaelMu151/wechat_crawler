from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

DELETED_MARKERS = (
    "该内容已被发布者删除",
    "此内容因违规无法查看",
    "该公众号已迁移",
    "页面无法访问",
    "参数错误",
    "链接已过期",
)


def _first_match(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, flags=re.S)
        if m:
            val = m.group(1).strip()
            if val:
                return html.unescape(val)
    return None


def detect_deleted(html_text: str) -> bool:
    return any(marker in html_text for marker in DELETED_MARKERS)


def extract_from_url(url: str) -> dict[str, str | None]:
    """从长链接 query 中提取参数；短链则返回空字段。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    out = {
        "biz": qs.get("__biz", [None])[0],
        "mid": qs.get("mid", [None])[0],
        "idx": qs.get("idx", [None])[0],
        "sn": qs.get("sn", [None])[0],
    }
    return out


def parse_article_html(html_text: str, final_url: str = "") -> dict[str, Any]:
    """解析公众号文章页，返回结构化字段。"""
    result: dict[str, Any] = {
        "biz": None,
        "mid": None,
        "idx": None,
        "sn": None,
        "account_name": None,
        "username": None,
        "head_img": None,
        "title": None,
        "author": None,
        "digest": None,
        "cover_url": None,
        "publish_ts": None,
        "publish_time": None,
        "content_html": None,
        "content_text": None,
        "url": final_url or None,
        "status": "ok",
        "error": None,
    }

    if detect_deleted(html_text):
        result["status"] = "deleted"
        result["error"] = "article unavailable"
        # 仍尽量抽取标题等
    if "你的访问过于频繁" in html_text or "需要从微信打开验证身份" in html_text:
        result["status"] = "failed"
        result["error"] = "rate_limited"
        return result

    url_params = extract_from_url(final_url) if final_url else {}
    result["biz"] = (
        _first_match(
            [
                r'var biz = ""\s*\|\|\s*"([^"]+)"',
                r'var biz = "([^"]+)"\s*\|\|',
                r"biz:\s*'([^']+)'",
            ],
            html_text,
        )
        or url_params.get("biz")
    )
    result["mid"] = (
        _first_match(
            [r'var mid = ""\s*\|\|\s*"([^"]+)"', r'var mid = "([^"]+)"\s*\|\|'],
            html_text,
        )
        or url_params.get("mid")
    )
    result["sn"] = (
        _first_match(
            [r'var sn = ""\s*\|\|\s*"([^"]+)"', r'var sn = "([^"]+)"\s*\|\|'],
            html_text,
        )
        or url_params.get("sn")
    )
    idx_raw = (
        _first_match(
            [r'var idx = ""\s*\|\|\s*"([^"]+)"', r'var idx = "([^"]+)"\s*\|\|'],
            html_text,
        )
        or url_params.get("idx")
    )
    if idx_raw and str(idx_raw).isdigit():
        result["idx"] = int(idx_raw)

    result["account_name"] = _first_match(
        [
            r"nick_name:\s*'([^']*)'",
            r'id="js_name"[^>]*>\s*([^<]+?)\s*<',
            r'nickname = "([^"]*)"',
            r"nick_name = \"\" \|\| '([^']*)'",
        ],
        html_text,
    )
    result["username"] = _first_match([r"user_name:\s*'([^']*)'"], html_text)
    result["head_img"] = _first_match(
        [
            r"hd_head_img:\s*'([^']*)'",
            r'var hd_head_img = "([^"]*)"',
        ],
        html_text,
    )

    result["title"] = _first_match(
        [
            r"var msg_title = '([^']*)'\.html\(",
            r'property="og:title" content="([^"]*)"',
            r'id="activity-name"[^>]*>\s*([^<]+)',
            r"<h1[^>]*id=\"activity-name\"[^>]*>\s*([^<]+)",
        ],
        html_text,
    )
    if result["title"]:
        result["title"] = BeautifulSoup(result["title"], "lxml").get_text().strip()

    result["author"] = _first_match(
        [
            r'id="js_author_name"[^>]*>\s*([^<]+?)\s*<',
            r'var author = "([^"]*)"',
            r'<meta name="author" content="([^"]*)"',
        ],
        html_text,
    )

    result["digest"] = _first_match(
        [
            r"var msg_desc = '([^']*)'\.html\(",
            r'property="og:description" content="([^"]*)"',
            r'name="description" content="([^"]*)"',
        ],
        html_text,
    )
    if result["digest"]:
        result["digest"] = BeautifulSoup(result["digest"], "lxml").get_text().strip()

    result["cover_url"] = _first_match(
        [
            r'var msg_cdn_url = "([^"]*)"',
            r'property="og:image" content="([^"]*)"',
        ],
        html_text,
    )

    ct = _first_match([r'var ct = "(\d+)"', r"ct\s*=\s*'(\d+)'"], html_text)
    if ct and ct.isdigit():
        ts = int(ct)
        result["publish_ts"] = ts
        result["publish_time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    soup = BeautifulSoup(html_text, "lxml")
    content = soup.select_one("#js_content")
    if content is not None:
        # 图片只保留链接：把 data-src 写回 src，便于纯文本/后续分析看到地址
        for img in content.find_all("img"):
            src = img.get("data-src") or img.get("src")
            if src:
                img["src"] = src
            for attr in ("data-src", "data-type", "data-w"):
                if attr in img.attrs:
                    del img.attrs[attr]
        result["content_html"] = str(content)
        result["content_text"] = content.get_text("\n", strip=True)
    elif result["status"] == "ok":
        result["status"] = "failed"
        result["error"] = "js_content_not_found"

    # 规范化文章 URL
    msg_link = _first_match([r'var msg_link = "([^"]+)"'], html_text)
    if msg_link:
        result["url"] = msg_link.split("#")[0]
    elif final_url:
        result["url"] = final_url.split("#")[0]

    if result["status"] == "deleted":
        return result
    return result


def normalize_content_url(url: str) -> str:
    if not url:
        return url
    url = html.unescape(url)
    url = url.replace("\\/", "/")
    url = unquote(url)
    return url.split("#")[0].strip()
