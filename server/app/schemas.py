from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeviceInfo(StrictModel):
    platform: str | None = Field(default=None, max_length=64)
    browser: str | None = Field(default=None, max_length=96)
    chrome_major: int | None = Field(default=None, ge=1, le=999)
    screen_bucket: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=32)
    hardware_concurrency: int | None = Field(default=None, ge=1, le=256)
    device_memory_gb: float | None = Field(default=None, ge=0.25, le=256)


class ClientContext(StrictModel):
    installation_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    extension_version: str = Field(min_length=1, max_length=32)
    page_path: str = Field(min_length=1, max_length=500)
    page_visibility: Literal["visible", "hidden", "prerender", "unknown"] = "unknown"
    device: DeviceInfo

    @field_validator("page_path")
    @classmethod
    def validate_page_path(cls, value: str) -> str:
        if not value.startswith("/") or "\n" in value or "\r" in value:
            raise ValueError("page_path must be a relative path")
        return value


EventType = Literal[
    "active_run",
    "run_created",
    "run_status",
    "run_result",
    "score_timeline",
    "account_hint",
    "heartbeat",
]


class IngestEnvelope(StrictModel):
    schema_version: Literal[1]
    event_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: EventType
    observed_at: datetime
    context: ClientContext
    payload: dict[str, Any]


class ParticipantCreate(StrictModel):
    code: str | None = Field(default=None, min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    token: str | None = Field(
        default=None, min_length=6, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    )


class ParticipantBulkCreate(StrictModel):
    text: str = Field(min_length=1, max_length=8000)


class ParticipantUpdate(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None


class VerificationUpdate(StrictModel):
    verification_status: Literal["observed", "pending", "verified", "risk"]
