import json

from wechat_archive.parsers.article_html import parse_article_html
from wechat_archive.parsers.history_json import parse_history_payload


def test_parse_article_extracts_content_and_lazy_image() -> None:
    html = """
    <html><head><meta property="og:title" content="研究样本"></head>
    <body>
      <div id="js_name">示例公众号</div>
      <div id="js_content"><p>正文内容</p>
        <img data-src="https://mmbiz.qpic.cn/example.jpg">
      </div>
      <script>var ct = "1704067200";</script>
    </body></html>
    """
    parsed = parse_article_html(
        html, "https://mp.weixin.qq.com/s?__biz=biz&mid=1&idx=1&sn=sn"
    )
    assert parsed["status"] == "ok"
    assert parsed["title"] == "研究样本"
    assert parsed["content_text"] == "正文内容"
    assert 'src="https://mmbiz.qpic.cn/example.jpg"' in parsed["content_html"]


def test_parse_history_expands_multi_article_message() -> None:
    general = {
        "list": [
            {
                "comm_msg_info": {"datetime": 1704067200},
                "app_msg_ext_info": {
                    "title": "主文",
                    "content_url": "https://mp.weixin.qq.com/s/a",
                    "multi_app_msg_item_list": [
                        {
                            "title": "副文",
                            "content_url": "https://mp.weixin.qq.com/s/b",
                        }
                    ],
                },
            }
        ]
    }
    rows, can_continue = parse_history_payload(
        {"general_msg_list": json.dumps(general), "can_msg_continue": 1}
    )
    assert [row["title"] for row in rows] == ["主文", "副文"]
    assert [row["idx"] for row in rows] == [1, 2]
    assert can_continue is True
