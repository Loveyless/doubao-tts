from __future__ import annotations

import asyncio

from doubao_tts import TTSResult
from service.config import ServiceConfig
from service.dependencies import build_tts_client
from service.errors import UpstreamTimeoutError, map_tts_result_error
from service.models import TTSRequest


async def synthesize_once(
    tts_request: TTSRequest,
    config: ServiceConfig,
    *,
    cookie_override: str | None = None,
    on_audio_chunk=None,
) -> tuple[TTSResult, object]:
    client = build_tts_client(
        tts_request,
        config,
        cookie_override=cookie_override,
    )
    try:
        result = await asyncio.wait_for(
            client.synthesize(tts_request.text, on_audio_chunk=on_audio_chunk),
            timeout=config.request_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise UpstreamTimeoutError("Service request timed out") from exc

    if not result.success:
        raise map_tts_result_error(result)

    return result, client
