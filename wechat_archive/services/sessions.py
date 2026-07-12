from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_sessions(sessions_dir: str | Path) -> list[dict[str, Any]]:
    """加载 sessions/*.yaml（忽略 example_session.yaml）。"""
    d = Path(sessions_dir)
    if not d.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        if path.name.startswith("example"):
            continue
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not data.get("enabled", True):
            continue
        uin = str(data.get("uin") or "").strip()
        key = str(data.get("key") or "").strip()
        cookie = str(data.get("cookie") or "").strip()
        if not uin or not key or not cookie or "REPLACE_ME" in (uin, key, cookie):
            continue
        sessions.append(
            {
                "name": data.get("name") or path.stem,
                "uin": uin,
                "key": key,
                "cookie": cookie,
                "path": str(path),
            }
        )
    return sessions
