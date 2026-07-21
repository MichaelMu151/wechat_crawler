import json
from pathlib import Path

from wechat_archive.platform_client import (
    PlatformCredentials,
    parse_publish_page,
    save_platform_credentials,
    load_platform_credentials,
)


def test_parse_publish_page_extracts_articles() -> None:
    payload = {
        "publish_page": json.dumps(
            {
                "total_count": 10,
                "publish_list": [
                    {
                        "publish_info": json.dumps(
                            {
                                "appmsgex": [
                                    {
                                        "aid": "111_1",
                                        "title": "标题A",
                                        "link": "https://mp.weixin.qq.com/s/abc",
                                        "create_time": 1704067200,
                                        "digest": "摘要",
                                        "cover": "https://mmbiz.qpic.cn/x",
                                        "author": "作者",
                                    },
                                    {
                                        "aid": "111_2",
                                        "title": "标题B",
                                        "link": "https://mp.weixin.qq.com/s/def",
                                        "create_time": 1704067200,
                                    },
                                ]
                            }
                        )
                    }
                ],
            }
        )
    }
    parsed = parse_publish_page(payload, begin=0)
    assert parsed["total"] == 10
    assert parsed["publish_fetched"] == 1
    assert parsed["next_begin"] == 1
    assert parsed["can_continue"] is True
    assert len(parsed["articles"]) == 2
    assert parsed["articles"][0]["title"] == "标题A"
    assert parsed["articles"][0]["url"].startswith("https://mp.weixin.qq.com/s/")


def test_resolve_fakeid_requires_exact_nickname() -> None:
    from wechat_archive.platform_client import PlatformAPIError, PlatformClient

    class Stub(PlatformClient):
        def __init__(self):
            pass

        def search_accounts(self, query, begin=0, count=5):
            return [
                {"fakeid": "A", "nickname": query + "附属"},
                {"fakeid": "B", "nickname": "其他"},
            ]

    try:
        Stub().resolve_fakeid("测试医院")
        assert False, "expected PlatformAPIError"
    except PlatformAPIError as exc:
        assert "精确匹配" in str(exc)


def test_save_and_load_platform_credentials(tmp_path: Path) -> None:
    cfg = {
        "paths": {
            "sessions_dir": str(tmp_path),
            "platform_credentials": str(tmp_path / "platform_credentials.yaml"),
        }
    }
    creds = PlatformCredentials(
        token="tok",
        cookie="a=1; b=2",
        nickname="测试号",
        fakeid="MzTest==",
        expire_time_ms=9999999999999,
        source="manual",
    )
    path = save_platform_credentials(cfg, creds)
    assert path.exists()
    loaded = load_platform_credentials(cfg)
    assert loaded is not None
    assert loaded.token == "tok"
    assert loaded.cookie == "a=1; b=2"
    assert loaded.nickname == "测试号"
    assert not loaded.expired
