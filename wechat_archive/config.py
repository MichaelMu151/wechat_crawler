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
    for key in ("name_list", "database", "sessions_dir"):
        raw = Path(paths[key])
        if not raw.is_absolute():
            paths[key] = str((ROOT / raw).resolve())
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    Path(cfg["paths"]["database"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["paths"]["sessions_dir"]).mkdir(parents=True, exist_ok=True)
