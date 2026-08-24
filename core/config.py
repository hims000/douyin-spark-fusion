from __future__ import annotations

import json
import os
import secrets
import threading as _threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"
VERSION = "1.0.9"


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _env_or(key: str, default: str) -> str:
    return os.environ.get(key) or _read_env().get(key) or default


def _parse_bool(value: str, label: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{label} 必须是 true 或 false")


class Settings:
    def __init__(self) -> None:
        self.port = int(_env_or("PORT", "8000"))
        self.host = _env_or("HOST", "0.0.0.0")
        self.auth_token = _env_or("AUTH_TOKEN", "")
        self.secret_key = _env_or("SECRET_KEY", secrets.token_hex(32))
        self.headless = _parse_bool(_env_or("HEADLESS", "true"), "HEADLESS")
        self.browser_path = _env_or("BROWSER_PATH", "")
        self.data_dir = DATA_DIR
        self.dingtalk_webhook = _env_or("DINGTALK_WEBHOOK", "")
        self.dingtalk_secret = _env_or("DINGTALK_SECRET", "")
        self.email_smtp_host = _env_or("EMAIL_SMTP_HOST", "")
        self.email_smtp_port = int(_env_or("EMAIL_SMTP_PORT", "465"))
        self.email_user = _env_or("EMAIL_USER", "")
        self.email_pass = _env_or("EMAIL_PASS", "")
        self.email_to = _env_or("EMAIL_TO", "")
        self.allow_registration = _parse_bool(
            _env_or("ALLOW_REGISTRATION", "true"), "ALLOW_REGISTRATION"
        )
        self.rate_limit_cooldown_minutes = int(
            _env_or("RATE_LIMIT_COOLDOWN_MINUTES", "45")
        )
        self.invite_only = _parse_bool(
            _env_or("INVITE_ONLY", "false"), "INVITE_ONLY"
        )


settings = Settings()

TZ = "Asia/Shanghai"
DEFAULT_CONFIG = {
    "schedule_time": "21:00",
    "jitter_minutes": 30,
    "send_gap_min": 6,
    "send_gap_max": 12,
    "max_friends_per_run": 20,
    "daily_limit": 50,
    "rate_limit_cooldown_minutes": 45,
    "retry_delay_minutes": 45,
}


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)

    for key in (
        "jitter_minutes",
        "send_gap_min",
        "send_gap_max",
        "max_friends_per_run",
        "daily_limit",
        "rate_limit_cooldown_minutes",
        "retry_delay_minutes",
    ):
        try:
            merged[key] = max(0, int(merged.get(key, DEFAULT_CONFIG[key])))
        except (TypeError, ValueError):
            merged[key] = DEFAULT_CONFIG[key]

    merged["send_gap_max"] = max(merged["send_gap_max"], merged["send_gap_min"])

    schedule = str(merged.get("schedule_time", "21:00"))
    try:
        hh, mm = schedule.split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError
        merged["schedule_time"] = f"{int(hh):02d}:{int(mm):02d}"
    except Exception:
        merged["schedule_time"] = "21:00"

    with _config_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return merged


_config_lock = _threading.Lock()
