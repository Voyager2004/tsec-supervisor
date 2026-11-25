from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    db_path: Path
    event_name: str
    admin_token: str
    ip_hash_salt: str
    public_base_url: str
    trusted_proxies: frozenset[str]
    store_raw_ip: bool
    retention_days: int
    cors_origin_regex: str
    scoring_start: str | None = None
    scoring_end: str | None = None
    scoring_set_name: str | None = None
    ip2region_path: str | None = None
    expected_province: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("TSEC_ENV", "development").strip().lower()
        admin_token = os.getenv("TSEC_ADMIN_TOKEN", "dev-admin-token-change-me")
        ip_hash_salt = os.getenv("TSEC_IP_HASH_SALT", "dev-ip-salt-change-me")
        if environment == "production":
            if len(admin_token) < 32:
                raise RuntimeError("TSEC_ADMIN_TOKEN must contain at least 32 characters")
            if len(ip_hash_salt) < 32:
                raise RuntimeError("TSEC_IP_HASH_SALT must contain at least 32 characters")

        trusted = frozenset(
            item.strip()
            for item in os.getenv("TSEC_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
            if item.strip()
        )
        retention_days = max(1, min(365, int(os.getenv("TSEC_RETENTION_DAYS", "30"))))

        def boundary(name: str) -> datetime | None:
            value = os.getenv(name, "").strip()
            if not value:
                return None
            try:
                result = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
            if result.tzinfo is None:
                raise ValueError(f"{name} must include a timezone")
            return result

        start = boundary("TSEC_SCORING_START")
        end = boundary("TSEC_SCORING_END")
        if start and end and end <= start:
            raise ValueError("TSEC_SCORING_END must be after TSEC_SCORING_START")
        return cls(
            environment=environment,
            db_path=Path(os.getenv("TSEC_DB_PATH", "./data/tsec-supervisor.sqlite3")),
            event_name=os.getenv("TSEC_EVENT_NAME", "").strip() or "TsecBench 挑战赛",
            admin_token=admin_token,
            ip_hash_salt=ip_hash_salt,
            public_base_url=os.getenv("TSEC_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            trusted_proxies=trusted,
            store_raw_ip=_as_bool(os.getenv("TSEC_STORE_RAW_IP"), False),
            retention_days=retention_days,
            cors_origin_regex=os.getenv(
                "TSEC_CORS_ORIGIN_REGEX",
                r"^(chrome-extension://[a-p]{32}|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
            ),
            scoring_start=start.isoformat() if start else None,
            scoring_end=end.isoformat() if end else None,
            scoring_set_name=os.getenv("TSEC_SCORING_SET_NAME", "").strip() or None,
            expected_province=os.getenv("TSEC_EXPECTED_PROVINCE", "").strip() or None,
            ip2region_path=os.getenv("TSEC_IP2REGION_PATH", "").strip() or None,
        )
