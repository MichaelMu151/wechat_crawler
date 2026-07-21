"""微信公众平台（mp.weixin.qq.com）接口客户端。

改版后个人微信 `profile_ext?action=getmsg` 已不可靠。
本模块改用公众号后台凭证调用：
- GET /cgi-bin/searchbiz      按昵称搜索，拿 fakeid
- GET /cgi-bin/appmsgpublish  分页拉历史发文列表

实现参考公开接口约定，以及社区项目 wechat-download-api
（https://github.com/tmwgsicp/wechat-download-api）的用法说明；
本仓库为独立实现，不复制其 AGPL 源码。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from wechat_archive.parsers.article_html import normalize_content_url


class PlatformAuthError(RuntimeError):
    """公众平台登录态失效或未配置。"""


class PlatformRateLimited(RuntimeError):
    """公众平台限流。"""


class PlatformAPIError(RuntimeError):
    """公众平台业务错误。"""


@dataclass
class PlatformCredentials:
    token: str
    cookie: str
    nickname: str = ""
    fakeid: str = ""
    expire_time_ms: int = 0
    source: str = "manual"

    @property
    def expired(self) -> bool:
        if not self.expire_time_ms:
            return False
        return int(time.time() * 1000) > int(self.expire_time_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "cookie": self.cookie,
            "nickname": self.nickname,
            "fakeid": self.fakeid,
            "expire_time": self.expire_time_ms,
            "source": self.source,
        }


def platform_creds_path(cfg: dict[str, Any]) -> Path:
    raw = cfg.get("paths", {}).get("platform_credentials")
    if raw:
        return Path(raw)
    sessions_dir = Path(cfg["paths"]["sessions_dir"])
    return sessions_dir / "platform_credentials.yaml"


def load_platform_credentials(cfg: dict[str, Any]) -> PlatformCredentials | None:
    path = platform_creds_path(cfg)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    token = str(data.get("token") or "").strip()
    cookie = str(data.get("cookie") or "").strip()
    if not token or not cookie or "REPLACE" in token or "REPLACE" in cookie:
        return None
    return PlatformCredentials(
        token=token,
        cookie=cookie,
        nickname=str(data.get("nickname") or ""),
        fakeid=str(data.get("fakeid") or ""),
        expire_time_ms=int(data.get("expire_time") or 0),
        source=str(data.get("source") or "manual"),
    )


def save_platform_credentials(
    cfg: dict[str, Any],
    creds: PlatformCredentials,
) -> Path:
    path = platform_creds_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = creds.as_dict()
    # 默认约 4 天有效（与公众平台常见会话一致）
    if not payload.get("expire_time"):
        payload["expire_time"] = int((time.time() + 4 * 24 * 3600) * 1000)
        creds.expire_time_ms = payload["expire_time"]
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return path


def import_credentials_from_download_api_env(env_path: Path) -> PlatformCredentials:
    """从 wechat-download-api 的 .env / credentials 导入。"""
    token = cookie = nickname = fakeid = ""
    expire_time = 0

    # JSON 凭证（Docker 常见）
    json_path = env_path.parent / "data" / ".credentials.json"
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        token = str(data.get("token") or "")
        cookie = str(data.get("cookie") or "")
        nickname = str(data.get("nickname") or "")
        fakeid = str(data.get("fakeid") or "")
        expire_time = int(data.get("expire_time") or 0)

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key == "WECHAT_TOKEN" and value:
                token = value
            elif key == "WECHAT_COOKIE" and value:
                cookie = value
            elif key == "WECHAT_NICKNAME" and value:
                nickname = value
            elif key == "WECHAT_FAKEID" and value:
                fakeid = value
            elif key == "WECHAT_EXPIRE_TIME" and value:
                expire_time = int(value)

    if not token or not cookie:
        raise PlatformAuthError(f"未能从 {env_path} 读到 WECHAT_TOKEN / WECHAT_COOKIE")
    return PlatformCredentials(
        token=token,
        cookie=cookie,
        nickname=nickname,
        fakeid=fakeid,
        expire_time_ms=expire_time,
        source="wechat-download-api",
    )


class PlatformClient:
    """直接调用微信公众平台后台接口。"""

    SEARCH_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
    LIST_URL = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"

    def __init__(
        self,
        creds: PlatformCredentials,
        timeout: int = 30,
        user_agent: str | None = None,
        proxies: dict[str, str | None] | None = None,
    ):
        if not creds.token or not creds.cookie:
            raise PlatformAuthError("缺少公众平台 token/cookie")
        if creds.expired:
            raise PlatformAuthError("公众平台凭证已过期，请重新登录并更新凭证")
        self.creds = creds
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = proxies or {"http": None, "https": None}
        self.session.headers.update(
            {
                "User-Agent": user_agent
                or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://mp.weixin.qq.com/",
                "Cookie": creds.cookie,
            }
        )

    def _raise_for_base_resp(self, result: dict[str, Any], context: str) -> None:
        base = result.get("base_resp") or {}
        ret = base.get("ret")
        err = base.get("err_msg") or ""
        if ret in (None, 0, "0"):
            return
        message = f"{context} failed: ret={ret} msg={err}"
        if ret in (200003, 200013, -3) or any(
            k in err for k in ("invalid session", "登录", "过期", "失效")
        ):
            raise PlatformAuthError(message)
        if ret in (200013,) or any(k in err for k in ("频繁", "freq", "limit")):
            raise PlatformRateLimited(message)
        if ret == 200002 or "invalid args" in err:
            raise PlatformAPIError(
                f"{context}: fakeid 无效或公众号不可访问 (ret={ret}, msg={err})"
            )
        raise PlatformAPIError(message)

    def search_accounts(self, query: str, begin: int = 0, count: int = 5) -> list[dict]:
        resp = self.session.get(
            self.SEARCH_URL,
            params={
                "action": "search_biz",
                "token": self.creds.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
                "random": time.time(),
                "query": query,
                "begin": begin,
                "count": count,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            result = resp.json()
        except Exception as exc:
            raise PlatformAuthError(
                f"searchbiz 返回非 JSON，登录态可能失效: {resp.text[:200]}"
            ) from exc
        self._raise_for_base_resp(result, "searchbiz")
        out = []
        for acc in result.get("list") or []:
            out.append(
                {
                    "fakeid": acc.get("fakeid") or "",
                    "nickname": acc.get("nickname") or "",
                    "alias": acc.get("alias") or "",
                    "round_head_img": acc.get("round_head_img") or "",
                    "service_type": acc.get("service_type", 0),
                }
            )
        return out

    def resolve_fakeid(self, nickname: str) -> dict[str, Any]:
        """按昵称精确优先匹配 fakeid。"""
        accounts = self.search_accounts(nickname)
        if not accounts:
            raise PlatformAPIError(f"未搜索到公众号: {nickname}")
        exact = [a for a in accounts if a["nickname"] == nickname]
        chosen = exact[0] if exact else accounts[0]
        if not chosen.get("fakeid"):
            raise PlatformAPIError(f"搜索结果缺少 fakeid: {nickname}")
        return chosen

    def list_articles(
        self,
        fakeid: str,
        begin: int = 0,
        count: int = 20,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        is_searching = bool(keyword)
        params = {
            "sub": "search" if is_searching else "list",
            "search_field": "7" if is_searching else "null",
            "begin": begin,
            "count": min(max(count, 1), 100),
            "query": keyword or "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": self.creds.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        resp = self.session.get(self.LIST_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        try:
            result = resp.json()
        except Exception as exc:
            raise PlatformAuthError(
                f"appmsgpublish 返回非 JSON，登录态可能失效: {resp.text[:200]}"
            ) from exc
        self._raise_for_base_resp(result, "appmsgpublish")
        return parse_publish_page(result, begin=begin)


class DownloadApiClient:
    """可选：把已部署的 wechat-download-api 当后端。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

    def _unwrap(self, payload: dict[str, Any], context: str) -> dict[str, Any]:
        if not payload.get("success"):
            err = payload.get("error") or "unknown error"
            if any(k in str(err) for k in ("登录", "过期", "失效", "未登录")):
                raise PlatformAuthError(f"{context}: {err}")
            raise PlatformAPIError(f"{context}: {err}")
        return payload.get("data") or {}

    def search_accounts(self, query: str) -> list[dict]:
        resp = self.session.get(
            f"{self.base_url}/api/public/searchbiz",
            params={"query": query},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = self._unwrap(resp.json(), "searchbiz")
        return data.get("list") or []

    def resolve_fakeid(self, nickname: str) -> dict[str, Any]:
        accounts = self.search_accounts(nickname)
        if not accounts:
            raise PlatformAPIError(f"未搜索到公众号: {nickname}")
        exact = [a for a in accounts if a.get("nickname") == nickname]
        chosen = exact[0] if exact else accounts[0]
        return chosen

    def list_articles(
        self,
        fakeid: str,
        begin: int = 0,
        count: int = 20,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "fakeid": fakeid,
            "begin": begin,
            "count": min(max(count, 1), 100),
        }
        if keyword:
            params["keyword"] = keyword
        resp = self.session.get(
            f"{self.base_url}/api/public/articles",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = self._unwrap(resp.json(), "articles")
        articles = []
        for item in data.get("articles") or []:
            articles.append(_normalize_list_item(item))
        total = int(data.get("total") or 0)
        begin_v = int(data.get("begin") or begin)
        count_v = len(articles)
        can_continue = (begin_v + count_v) < total and count_v > 0
        return {
            "articles": articles,
            "total": total,
            "begin": begin_v,
            "count": count_v,
            "can_continue": can_continue,
        }


def parse_publish_page(result: dict[str, Any], begin: int = 0) -> dict[str, Any]:
    publish_page = result.get("publish_page") or {}
    if isinstance(publish_page, str):
        publish_page = json.loads(publish_page)
    if not isinstance(publish_page, dict):
        raise PlatformAPIError("publish_page 格式错误")

    articles: list[dict[str, Any]] = []
    for item in publish_page.get("publish_list") or []:
        publish_info = item.get("publish_info") or {}
        if isinstance(publish_info, str):
            try:
                publish_info = json.loads(publish_info)
            except json.JSONDecodeError:
                continue
        if not isinstance(publish_info, dict):
            continue
        for article in publish_info.get("appmsgex") or []:
            articles.append(_normalize_list_item(article))

    total = int(publish_page.get("total_count") or 0)
    count_v = len(articles)
    can_continue = (begin + count_v) < total and count_v > 0
    return {
        "articles": articles,
        "total": total,
        "begin": begin,
        "count": count_v,
        "can_continue": can_continue,
    }


def _normalize_list_item(article: dict[str, Any]) -> dict[str, Any]:
    url = normalize_content_url(article.get("link") or article.get("content_url") or "")
    create_time = article.get("create_time") or article.get("update_time") or 0
    try:
        publish_ts = int(create_time) if create_time else None
    except (TypeError, ValueError):
        publish_ts = None
    publish_time = None
    if publish_ts:
        publish_time = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d %H:%M:%S")
    aid = str(article.get("aid") or "")
    idx = None
    mid = None
    if "_" in aid:
        left, right = aid.rsplit("_", 1)
        mid = left
        if right.isdigit():
            idx = int(right)
    return {
        "aid": aid,
        "title": (article.get("title") or "").strip() or None,
        "author": (article.get("author") or "").strip() or None,
        "digest": (article.get("digest") or "").strip() or None,
        "cover_url": normalize_content_url(article.get("cover") or "") or None,
        "url": url or None,
        "publish_ts": publish_ts,
        "publish_time": publish_time,
        "mid": mid,
        "idx": idx,
        "raw_list_json": json.dumps(article, ensure_ascii=False),
    }


def build_history_client(cfg: dict[str, Any]):
    """根据 config 构建历史列表客户端。"""
    backend = (cfg.get("platform") or {}).get("backend", "platform")
    timeout = int(cfg.get("http", {}).get("timeout", 30))
    if backend == "download_api":
        base = (cfg.get("platform") or {}).get("download_api_base_url", "").strip()
        if not base:
            raise PlatformAuthError(
                "platform.backend=download_api 但未配置 download_api_base_url"
            )
        return DownloadApiClient(base, timeout=timeout)

    creds = load_platform_credentials(cfg)
    if not creds:
        raise PlatformAuthError(
            "未配置公众平台凭证。请扫码登录 wechat-download-api 后导入，"
            "或运行 python run.py set-platform-creds"
        )
    ua = (cfg.get("crawl") or {}).get("platform_user_agent")
    return PlatformClient(creds, timeout=timeout, user_agent=ua)
