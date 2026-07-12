from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HttpClient:
    def __init__(self, cfg: dict[str, Any]):
        crawl = cfg.get("crawl", {})
        http = cfg.get("http", {})
        self.timeout = http.get("timeout", 30)
        self.session = requests.Session()
        self.session.trust_env = bool(http.get("trust_env", False))
        retry = Retry(
            total=int(http.get("max_retries", 3)),
            connect=int(http.get("max_retries", 3)),
            read=int(http.get("max_retries", 3)),
            status=int(http.get("max_retries", 3)),
            backoff_factor=float(http.get("backoff_factor", 1.0)),
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=int(http.get("pool_connections", 4)),
            pool_maxsize=int(http.get("pool_maxsize", 8)),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": crawl.get(
                    "user_agent",
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                    "MicroMessenger/8.0.40 NetType/WIFI Language/zh_CN",
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        proxy = http.get("proxy")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        elif not self.session.trust_env:
            self.session.proxies = {"http": None, "https": None}

    def get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self.session.get(url, **kwargs)

    def get_text(self, url: str, **kwargs) -> tuple[str, str]:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.url, resp.text

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
