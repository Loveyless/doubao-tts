from typing import Literal

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator
except ImportError:  # pragma: no cover - Pydantic v1 fallback
    field_validator = None
    from pydantic import validator as legacy_validator
else:  # pragma: no cover - Pydantic v2 path
    legacy_validator = None


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    speaker: str | None = Field(default=None, description="Speaker alias or full speaker ID")
    format: Literal["aac", "mp3"] | None = Field(default=None, description="Output audio format")
    speed: float | None = Field(default=None, ge=-1.0, le=1.0, description="Speech rate")
    pitch: float | None = Field(default=None, ge=-1.0, le=1.0, description="Pitch")

    if field_validator is not None:

        @field_validator("text")
        @classmethod
        def validate_text(cls, value: str) -> str:
            if not value or not value.strip():
                raise ValueError("text must not be blank")
            return value

    else:  # pragma: no cover - Pydantic v1 fallback

        @legacy_validator("text")
        def validate_text(cls, value: str) -> str:
            if not value or not value.strip():
                raise ValueError("text must not be blank")
            return value


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"] = "ok"
    ready: bool = True
    setup_completed: bool = False
    enabled_api_keys: int = 0
    total_accounts: int = 0
    healthy_accounts: int = 0
    detail: str | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str


class AdminSetupRequest(BaseModel):
    bootstrap_password: str
    new_password: str


class AdminLoginRequest(BaseModel):
    password: str


class AdminActionResponse(BaseModel):
    status: str
    detail: str
    redirect_to: str | None = None


class AdminServiceSettingsRequest(BaseModel):
    default_speaker: str = Field(..., min_length=1)
    default_format: Literal["aac", "mp3"]
    request_timeout_seconds: float = Field(..., gt=0)
    max_concurrency: int = Field(..., gt=0)
    retry_on_block: bool = False
    retry_max_retries: int = Field(default=0, ge=0)
    retry_backoff_seconds: float = Field(default=1.0, gt=0)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0)
    retry_backoff_jitter_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_streaming: bool = True
    allow_request_override: bool = True
    report_retention_days: int = Field(default=30, ge=1, le=3650)


class AdminApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class AdminApiKeyCreateResponse(BaseModel):
    status: str
    detail: str
    key_id: int
    name: str
    raw_key: str


class AdminApiKeyStatusResponse(BaseModel):
    status: str
    detail: str
    key_id: int
    enabled: bool


class AdminAccountWriteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sessionid: str = Field(..., min_length=1)
    sid_guard: str = Field(..., min_length=1)
    uid_tt: str = Field(..., min_length=1)


class AdminAccountTestRequest(BaseModel):
    text: str = Field(default="后台凭据测试")
    speaker: str | None = Field(default=None)
    format: Literal["aac", "mp3"] | None = Field(default=None)
    speed: float | None = Field(default=None, ge=-1.0, le=1.0)
    pitch: float | None = Field(default=None, ge=-1.0, le=1.0)


class AdminTestTTSRequest(BaseModel):
    text: str = Field(default="后台测试一下当前配置")
    account_id: int | None = Field(default=None, ge=1)
    speaker: str | None = Field(default=None)
    format: Literal["aac", "mp3"] | None = Field(default=None)
    speed: float | None = Field(default=None, ge=-1.0, le=1.0)
    pitch: float | None = Field(default=None, ge=-1.0, le=1.0)


class AdminTestTTSResponse(BaseModel):
    status: str
    detail: str
    account_id: int | None = None
    account_name: str | None = None
    speaker: str
    format: str
    audio_bytes: int
    attempt_count: int
