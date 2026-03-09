import logging
import os
from dataclasses import dataclass, replace
from functools import lru_cache

from service.db import (
    DatabaseError,
    fetch_service_settings,
    initialize_database,
    save_initial_service_settings,
    seed_initial_doubao_account,
)

VALID_AUDIO_FORMATS = {"aac", "mp3"}
VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class ConfigError(ValueError):
    """Raised when service configuration is invalid."""


def _read_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Invalid environment variable {name}: {value}")


def _read_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid environment variable {name}: {value}") from exc


def _read_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid environment variable {name}: {value}") from exc


@dataclass(frozen=True)
class ServiceConfig:
    cookie: str
    default_speaker: str = "taozi"
    default_format: str = "aac"
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    retry_on_block: bool = False
    retry_max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    retry_backoff_multiplier: float = 2.0
    retry_backoff_jitter_ratio: float = 0.0
    request_timeout_seconds: float = 35.0
    max_concurrency: int = 4
    enable_streaming: bool = True
    allow_request_override: bool = True
    report_retention_days: int = 30
    auth_token: str = ""
    metrics_enabled: bool = True

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        config = cls(
            cookie=os.getenv("TTS_COOKIE", "").strip(),
            default_speaker=os.getenv("TTS_DEFAULT_SPEAKER", "taozi").strip() or "taozi",
            default_format=os.getenv("TTS_DEFAULT_FORMAT", "aac").strip() or "aac",
            host=os.getenv("TTS_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_read_env_int("TTS_PORT", 8080),
            log_level=(os.getenv("TTS_LOG_LEVEL", "INFO").strip() or "INFO").upper(),
            retry_on_block=_read_env_bool("TTS_RETRY_ON_BLOCK", False),
            retry_max_retries=_read_env_int("TTS_RETRY_MAX_RETRIES", 0),
            retry_backoff_seconds=_read_env_float("TTS_RETRY_BACKOFF_SECONDS", 1.0),
            retry_backoff_multiplier=_read_env_float("TTS_RETRY_BACKOFF_MULTIPLIER", 2.0),
            retry_backoff_jitter_ratio=_read_env_float("TTS_RETRY_BACKOFF_JITTER_RATIO", 0.0),
            request_timeout_seconds=_read_env_float("TTS_REQUEST_TIMEOUT_SECONDS", 35.0),
            max_concurrency=_read_env_int("TTS_MAX_CONCURRENCY", 4),
            auth_token=os.getenv("TTS_AUTH_TOKEN", "").strip(),
            metrics_enabled=_read_env_bool("TTS_ENABLE_METRICS", True),
        )
        config.validate()
        return config

    @classmethod
    def from_persisted_settings(
        cls,
        runtime_config: "ServiceConfig",
        stored_settings: dict[str, object],
    ) -> "ServiceConfig":
        if not stored_settings:
            return runtime_config

        resolved = replace(
            runtime_config,
            default_speaker=str(stored_settings.get("default_speaker", runtime_config.default_speaker)),
            default_format=str(stored_settings.get("default_format", runtime_config.default_format)),
            retry_on_block=bool(stored_settings.get("retry_on_block", runtime_config.retry_on_block)),
            retry_max_retries=int(stored_settings.get("retry_max_retries", runtime_config.retry_max_retries)),
            retry_backoff_seconds=float(
                stored_settings.get("retry_backoff_seconds", runtime_config.retry_backoff_seconds)
            ),
            retry_backoff_multiplier=float(
                stored_settings.get("retry_backoff_multiplier", runtime_config.retry_backoff_multiplier)
            ),
            retry_backoff_jitter_ratio=float(
                stored_settings.get(
                    "retry_backoff_jitter_ratio",
                    runtime_config.retry_backoff_jitter_ratio,
                )
            ),
            request_timeout_seconds=float(
                stored_settings.get("request_timeout_seconds", runtime_config.request_timeout_seconds)
            ),
            max_concurrency=int(stored_settings.get("max_concurrency", runtime_config.max_concurrency)),
            enable_streaming=bool(stored_settings.get("enable_streaming", runtime_config.enable_streaming)),
            allow_request_override=bool(
                stored_settings.get("allow_request_override", runtime_config.allow_request_override)
            ),
            report_retention_days=int(
                stored_settings.get("report_retention_days", runtime_config.report_retention_days)
            ),
        )
        resolved.validate()
        return resolved

    def validate(self) -> None:
        if self.default_format not in VALID_AUDIO_FORMATS:
            raise ConfigError(f"TTS_DEFAULT_FORMAT must be one of: {sorted(VALID_AUDIO_FORMATS)}")
        if not 1 <= self.port <= 65535:
            raise ConfigError("TTS_PORT must be between 1 and 65535")
        if self.log_level not in VALID_LOG_LEVELS:
            raise ConfigError(f"TTS_LOG_LEVEL must be one of: {sorted(VALID_LOG_LEVELS)}")
        if self.max_concurrency <= 0:
            raise ConfigError("TTS_MAX_CONCURRENCY must be greater than 0")
        if self.request_timeout_seconds <= 0:
            raise ConfigError("TTS_REQUEST_TIMEOUT_SECONDS must be greater than 0")
        if self.retry_max_retries < 0:
            raise ConfigError("TTS_RETRY_MAX_RETRIES cannot be negative")
        if self.retry_backoff_seconds <= 0:
            raise ConfigError("TTS_RETRY_BACKOFF_SECONDS must be greater than 0")
        if self.retry_backoff_multiplier < 1:
            raise ConfigError("TTS_RETRY_BACKOFF_MULTIPLIER must be >= 1")
        if not 0 <= self.retry_backoff_jitter_ratio <= 1:
            raise ConfigError("TTS_RETRY_BACKOFF_JITTER_RATIO must be between 0 and 1")
        if self.report_retention_days <= 0:
            raise ConfigError("report_retention_days must be greater than 0")

    def persistent_settings_payload(self) -> dict[str, object]:
        return {
            "default_speaker": self.default_speaker,
            "default_format": self.default_format,
            "retry_on_block": self.retry_on_block,
            "retry_max_retries": self.retry_max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "retry_backoff_multiplier": self.retry_backoff_multiplier,
            "retry_backoff_jitter_ratio": self.retry_backoff_jitter_ratio,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_concurrency": self.max_concurrency,
            "enable_streaming": self.enable_streaming,
            "allow_request_override": self.allow_request_override,
            "report_retention_days": self.report_retention_days,
        }


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")

    for logger_name in ("service", "doubao_tts"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False

        handler = next(
            (item for item in logger.handlers if getattr(item, "_tts_service_handler", False)),
            None,
        )
        if handler is None:
            handler = logging.StreamHandler()
            handler._tts_service_handler = True
            logger.addHandler(handler)

        handler.setLevel(level)
        handler.setFormatter(formatter)


@lru_cache(maxsize=1)
def get_service_config() -> ServiceConfig:
    runtime_config = ServiceConfig.from_env()
    try:
        initialize_database(seed_service_settings=runtime_config.persistent_settings_payload())
        seed_initial_doubao_account(runtime_config.cookie)
        stored_settings = fetch_service_settings()
        if not stored_settings:
            save_initial_service_settings(runtime_config.persistent_settings_payload())
            stored_settings = fetch_service_settings()
    except DatabaseError as exc:
        raise ConfigError(str(exc)) from exc

    config = ServiceConfig.from_persisted_settings(runtime_config, stored_settings)
    configure_logging(config.log_level)
    return config


def clear_service_config_cache() -> None:
    get_service_config.cache_clear()
