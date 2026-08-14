from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"

PROFILES: dict[str, dict[str, Any]] = {
    "safe": {
        "crawl": {
            "sleep_min": 15,
            "sleep_max": 30,
            "history_page_size": 10,
            "history_page_size_min": 5,
            "history_page_size_max": 20,
            "history_max_pages_per_account": 50,
            "history_rate_limit_retries": 3,
            "history_rate_limit_cooldown": 900,
            "history_circuit_breaker_threshold": 2,
            "history_global_cooldown": 7200,
            "content_sleep_min": 2.5,
            "content_sleep_max": 5.0,
            "content_concurrency": 1,
            "content_rate_limit_cooldown": 900,
            "content_circuit_breaker_threshold": 3,
        }
    },
    "balanced": {
        "crawl": {
            "sleep_min": 10,
            "sleep_max": 20,
            "history_page_size": 15,
            "history_page_size_min": 5,
            "history_page_size_max": 40,
            "history_max_pages_per_account": 100,
            "history_rate_limit_retries": 2,
            "history_rate_limit_cooldown": 900,
            "history_circuit_breaker_threshold": 3,
            "history_global_cooldown": 3600,
            "content_sleep_min": 1.5,
            "content_sleep_max": 3.5,
            "content_concurrency": 2,
            "content_rate_limit_cooldown": 600,
            "content_circuit_breaker_threshold": 5,
        }
    },
    "fast": {
        "crawl": {
            # History stays conservative; only content is more aggressive.
            "sleep_min": 10,
            "sleep_max": 20,
            "history_page_size": 15,
            "history_page_size_min": 5,
            "history_page_size_max": 40,
            "history_max_pages_per_account": 100,
            "history_rate_limit_retries": 2,
            "history_rate_limit_cooldown": 900,
            "history_circuit_breaker_threshold": 3,
            "history_global_cooldown": 3600,
            "content_sleep_min": 1.0,
            "content_sleep_max": 2.5,
            "content_concurrency": 3,
            "content_rate_limit_cooldown": 480,
            "content_circuit_breaker_threshold": 5,
        }
    },
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def apply_profile(cfg: dict[str, Any], profile: str | None) -> dict[str, Any]:
    if not profile:
        return cfg
    key = profile.strip().lower()
    if key not in PROFILES:
        raise ValueError(
            f"未知 profile: {profile}；可选: {', '.join(sorted(PROFILES))}"
        )
    return _deep_merge(cfg, PROFILES[key])


def load_config(
    path: Path | str | None = None, profile: str | None = None
) -> dict[str, Any]:
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
            "download_api_env": "../wechat-download-api/.env",
        },
    )
    # 解析 download_api_env 相对路径
    platform = cfg["platform"]
    for key in ("download_api_env", "schinza_accounts_path"):
        configured_path = platform.get(key)
        if configured_path and not Path(configured_path).is_absolute():
            platform[key] = str((ROOT / configured_path).resolve())

    if profile:
        cfg = apply_profile(cfg, profile)
    cfg["_profile"] = profile or "default"
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    Path(cfg["paths"]["database"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["paths"]["sessions_dir"]).mkdir(parents=True, exist_ok=True)
    creds = cfg["paths"].get("platform_credentials")
    if creds:
        Path(creds).parent.mkdir(parents=True, exist_ok=True)
