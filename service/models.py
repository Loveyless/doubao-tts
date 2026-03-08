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
    speed: float = Field(default=0.0, ge=-1.0, le=1.0, description="Speech rate")
    pitch: float = Field(default=0.0, ge=-1.0, le=1.0, description="Pitch")

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
    status: str = "ok"


class ErrorResponse(BaseModel):
    error: str
    detail: str

