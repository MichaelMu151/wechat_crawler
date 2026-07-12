import json
import os
from pathlib import Path

import pytest
import yaml

from wechat_archive.services.session_import import (
    SessionImportError,
    import_session_from_har,
)


def _entry(
    *,
    captured_at: str,
    uin: str,
    key: str,
    cookie: str | None = None,
    action: str = "getmsg",
) -> dict:
    headers = [{"name": "Cookie", "value": cookie}] if cookie else []
    cookies = [] if cookie else [{"name": "wxuin", "value": uin}]
    return {
        "startedDateTime": captured_at,
        "request": {
            "url": (
                "https://mp.weixin.qq.com/mp/profile_ext"
                f"?action={action}&__biz=biz123&uin={uin}&key={key}"
            ),
            "queryString": [
                {"name": "action", "value": action},
                {"name": "__biz", "value": "biz123"},
                {"name": "uin", "value": uin},
                {"name": "key", "value": key},
            ],
            "headers": headers,
            "cookies": cookies,
        },
    }


def _write_har(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"log": {"version": "1.2", "entries": entries}}),
        encoding="utf-8",
    )


def test_import_uses_newest_complete_request_and_hides_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.har"
    sessions = tmp_path / "sessions"
    _write_har(
        source,
        [
            _entry(
                captured_at="2026-07-12T10:00:00Z",
                uin="old-uin",
                key="old-key",
                cookie="old=cookie",
            ),
            _entry(
                captured_at="2026-07-12T10:02:00Z",
                uin="new-uin",
                key="new-key",
                cookie="wxuin=new-uin; pass_ticket=secret",
            ),
        ],
    )

    result = import_session_from_har(source, sessions, name="session_1")
    saved = yaml.safe_load((sessions / "session_1.yaml").read_text(encoding="utf-8"))

    assert saved["uin"] == "new-uin"
    assert saved["key"] == "new-key"
    assert saved["cookie"] == "wxuin=new-uin; pass_ticket=secret"
    assert result["candidates_found"] == 2
    assert "cookie" not in result and "key" not in result and "uin" not in result
    if os.name != "nt":
        assert (sessions / "session_1.yaml").stat().st_mode & 0o777 == 0o600


def test_import_reconstructs_cookie_array(tmp_path: Path) -> None:
    source = tmp_path / "capture.har"
    _write_har(
        source,
        [
            _entry(
                captured_at="2026-07-12T10:00:00Z",
                uin="123",
                key="abc",
            )
        ],
    )

    import_session_from_har(source, tmp_path / "sessions")
    saved = yaml.safe_load(
        (tmp_path / "sessions" / "session_1.yaml").read_text(encoding="utf-8")
    )
    assert saved["cookie"] == "wxuin=123"


def test_import_rejects_home_request_and_existing_session(tmp_path: Path) -> None:
    source = tmp_path / "capture.har"
    _write_har(
        source,
        [
            _entry(
                captured_at="2026-07-12T10:00:00Z",
                uin="123",
                key="abc",
                cookie="wxuin=123",
                action="home",
            )
        ],
    )
    with pytest.raises(SessionImportError, match="未找到"):
        import_session_from_har(source, tmp_path / "sessions")

    _write_har(
        source,
        [
            _entry(
                captured_at="2026-07-12T10:01:00Z",
                uin="123",
                key="abc",
                cookie="wxuin=123",
            )
        ],
    )
    import_session_from_har(source, tmp_path / "sessions")
    with pytest.raises(SessionImportError, match="已存在"):
        import_session_from_har(source, tmp_path / "sessions")
