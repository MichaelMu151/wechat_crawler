from wechat_archive.url_utils import article_sn, normalize_article_url


def test_normalize_article_url_removes_tracking_and_fragment() -> None:
    url = (
        "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=2&sn=stable"
        "&scene=27&from=timeline#wechat_redirect"
    )
    assert normalize_article_url(url) == (
        "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=2&sn=stable"
    )
    assert article_sn(url) == "stable"


def test_short_url_gets_temporary_sn() -> None:
    assert article_sn("https://mp.weixin.qq.com/s/AbCdEf") == "short:AbCdEf"
