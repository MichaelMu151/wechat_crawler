from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "chksm",
    "clicktime",
    "enterid",
    "from",
    "isappinstalled",
    "scene",
    "sessionid",
    "subscene",
}


def normalize_article_url(url: str | None) -> str | None:
    """Return a stable WeChat article URL without fragments/tracking parameters."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS
    ]
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urlencode(query),
            "",
        )
    )


def article_sn(url: str | None) -> str | None:
    normalized = normalize_article_url(url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    query = dict(parse_qsl(parsed.query))
    if query.get("sn"):
        return query["sn"]
    tail = parsed.path.rstrip("/").split("/")[-1]
    if tail and tail != "s":
        return f"short:{tail}"
    return None
