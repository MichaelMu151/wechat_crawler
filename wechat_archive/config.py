from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 相对路径统一相对项目根目录解析
    paths = cfg.setdefault("paths", {})
    for key in ("name_list", "database", "sessions_dir", "platform_credentials"):
        if key not in paths or not paths[key]:
            continue
        raw = Path(paths[key])
        if not raw.is_absolute():
            paths[key] = str((ROOT / raw).resolve())
    cfg.setdefault(
        "platform",
        {
            "backend": "platform",
            "download_api_base_url": "http://127.0.0.1:5000",
        },
    )
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    Path(cfg["paths"]["database"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["paths"]["sessions_dir"]).mkdir(parents=True, exist_ok=True)
    creds = cfg["paths"].get("platform_credentials")
    if creds:
        Path(creds).parent.mkdir(parents=True, exist_ok=True)
