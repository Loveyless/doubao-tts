from doubao_tts import DoubaoTTS, TTSConfig
from service.config import ServiceConfig, get_service_config
from service.errors import InternalServiceError
from service.models import TTSRequest


def build_tts_client(
    request: TTSRequest,
    service_config: ServiceConfig | None = None,
    *,
    cookie_override: str | None = None,
) -> DoubaoTTS:
    service_config = service_config or get_service_config()
    resolved_cookie = (cookie_override or service_config.cookie).strip()
    if not resolved_cookie:
        raise InternalServiceError("TTS_COOKIE is not configured")

    resolved_speaker = request.speaker or service_config.default_speaker
    resolved_format = request.format or service_config.default_format

    config = TTSConfig(
        cookie=resolved_cookie,
        autoload_cookie=False,
        format=resolved_format,
        verbose=False,
        retry_on_block=service_config.retry_on_block,
        retry_max_retries=service_config.retry_max_retries,
        retry_backoff_seconds=service_config.retry_backoff_seconds,
        retry_backoff_multiplier=service_config.retry_backoff_multiplier,
        retry_backoff_jitter_ratio=service_config.retry_backoff_jitter_ratio,
    )

    client = DoubaoTTS(config)
    client.set_speaker(resolved_speaker)
    client.set_speed(0.0 if request.speed is None else request.speed)
    client.set_pitch(0.0 if request.pitch is None else request.pitch)
    return client
