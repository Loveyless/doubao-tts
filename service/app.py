import asyncio
import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from doubao_tts import SPEAKERS, TTSResult
from service.config import ConfigError, ServiceConfig, get_service_config
from service.dependencies import build_tts_client
from service.errors import (
    InternalServiceError,
    ServiceHTTPError,
    ServiceUnavailableError,
    UnauthorizedError,
    UpstreamTimeoutError,
    map_tts_result_error,
)
from service.models import ErrorResponse, HealthResponse, TTSRequest

LOGGER = logging.getLogger(__name__)


@dataclass
class ServiceMetrics:
    requests_total: int = 0
    request_success_total: int = 0
    request_failure_total: int = 0
    request_timeout_total: int = 0
    upstream_failure_total: int = 0
    unauthorized_total: int = 0
    stream_requests_total: int = 0
    inflight_requests: int = 0

    def render(self) -> str:
        return "\n".join(
            [
                "# HELP tts_requests_total Total TTS requests handled by the service",
                "# TYPE tts_requests_total counter",
                f"tts_requests_total {self.requests_total}",
                "# HELP tts_request_success_total Successful TTS requests",
                "# TYPE tts_request_success_total counter",
                f"tts_request_success_total {self.request_success_total}",
                "# HELP tts_request_failure_total Failed TTS requests",
                "# TYPE tts_request_failure_total counter",
                f"tts_request_failure_total {self.request_failure_total}",
                "# HELP tts_request_timeout_total Timed out TTS requests",
                "# TYPE tts_request_timeout_total counter",
                f"tts_request_timeout_total {self.request_timeout_total}",
                "# HELP tts_upstream_failure_total Upstream or protocol failures",
                "# TYPE tts_upstream_failure_total counter",
                f"tts_upstream_failure_total {self.upstream_failure_total}",
                "# HELP tts_unauthorized_total Unauthorized requests",
                "# TYPE tts_unauthorized_total counter",
                f"tts_unauthorized_total {self.unauthorized_total}",
                "# HELP tts_stream_requests_total Streaming requests",
                "# TYPE tts_stream_requests_total counter",
                f"tts_stream_requests_total {self.stream_requests_total}",
                "# HELP tts_inflight_requests Currently in-flight requests",
                "# TYPE tts_inflight_requests gauge",
                f"tts_inflight_requests {self.inflight_requests}",
            ]
        )


app = FastAPI(title="Doubao TTS Service")


def _model_dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _build_validation_detail(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = [str(item) for item in error.get("loc", []) if item != "body"]
        field_name = ".".join(location)
        message = error.get("msg", "invalid request")
        messages.append(f"{field_name}: {message}" if field_name else message)
    return "; ".join(messages) or "invalid request"


def _resolve_media_type(audio_format: str) -> str:
    return "audio/aac" if audio_format == "aac" else "audio/mpeg"


def _get_metrics(request: Request) -> ServiceMetrics:
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is None:
        metrics = ServiceMetrics()
        request.app.state.metrics = metrics
    return metrics


def _get_semaphore(request: Request, config: ServiceConfig) -> asyncio.Semaphore:
    semaphore = getattr(request.app.state, "concurrency_semaphore", None)
    current_limit = getattr(request.app.state, "concurrency_limit", None)
    if semaphore is None or current_limit != config.max_concurrency:
        semaphore = asyncio.Semaphore(config.max_concurrency)
        request.app.state.concurrency_semaphore = semaphore
        request.app.state.concurrency_limit = config.max_concurrency
    return semaphore


def _record_error(metrics: ServiceMetrics, error: ServiceHTTPError) -> None:
    metrics.request_failure_total += 1
    if error.status_code == 504:
        metrics.request_timeout_total += 1
    elif error.status_code == 502:
        metrics.upstream_failure_total += 1
    elif error.status_code == 401:
        metrics.unauthorized_total += 1


def _load_service_config() -> ServiceConfig:
    try:
        return get_service_config()
    except ConfigError as exc:
        raise InternalServiceError(str(exc)) from exc


def _require_auth(request: Request, config: ServiceConfig, metrics: ServiceMetrics) -> None:
    if not config.auth_token:
        return

    header_value = request.headers.get("Authorization", "")
    expected = f"Bearer {config.auth_token}"
    if header_value != expected:
        metrics.request_failure_total += 1
        metrics.unauthorized_total += 1
        raise UnauthorizedError()


async def _run_synthesis(
    tts_request: TTSRequest,
    config: ServiceConfig,
    metrics: ServiceMetrics,
) -> tuple[TTSResult, object]:
    client = build_tts_client(tts_request, config)
    try:
        result = await asyncio.wait_for(
            client.synthesize(tts_request.text),
            timeout=config.request_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        error = UpstreamTimeoutError("Service request timed out")
        _record_error(metrics, error)
        raise error from exc

    if not result.success:
        error = map_tts_result_error(result)
        _record_error(metrics, error)
        raise error

    metrics.request_success_total += 1
    return result, client


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content=_model_dump(ErrorResponse(error="bad_request", detail=_build_validation_detail(exc))),
    )


@app.exception_handler(ServiceHTTPError)
async def service_http_exception_handler(_, exc: ServiceHTTPError):
    return JSONResponse(
        status_code=exc.status_code,
        content=_model_dump(ErrorResponse(error=exc.error, detail=exc.detail)),
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_, exc: Exception):
    LOGGER.exception("Unexpected service error", exc_info=exc)
    error = InternalServiceError()
    return JSONResponse(
        status_code=error.status_code,
        content=_model_dump(ErrorResponse(error=error.error, detail=error.detail)),
    )


@app.get("/healthz", response_model=HealthResponse)
async def healthz():
    try:
        config = get_service_config()
    except ConfigError as exc:
        error = ServiceUnavailableError(str(exc))
        return JSONResponse(status_code=error.status_code, content=_model_dump(ErrorResponse(error=error.error, detail=error.detail)))

    if not config.cookie:
        error = ServiceUnavailableError("TTS_COOKIE is not configured")
        return JSONResponse(status_code=error.status_code, content=_model_dump(ErrorResponse(error=error.error, detail=error.detail)))

    return HealthResponse(status="ok")


@app.get("/v1/speakers")
async def list_speakers() -> dict[str, str]:
    return SPEAKERS


@app.get("/metrics")
async def metrics(request: Request):
    config = _load_service_config()
    metric_state = _get_metrics(request)
    _require_auth(request, config, metric_state)
    if not config.metrics_enabled:
        raise ServiceUnavailableError("Metrics are disabled")
    return PlainTextResponse(metric_state.render() + "\n")


@app.post(
    "/v1/tts",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def synthesize_tts(tts_request: TTSRequest, request: Request):
    config = _load_service_config()
    metrics = _get_metrics(request)
    _require_auth(request, config, metrics)
    semaphore = _get_semaphore(request, config)

    metrics.requests_total += 1
    metrics.inflight_requests += 1
    try:
        async with semaphore:
            result, client = await _run_synthesis(tts_request, config, metrics)
    finally:
        metrics.inflight_requests -= 1

    return Response(
        content=result.audio_data,
        media_type=_resolve_media_type(client.config.format),
        headers={
            "X-TTS-Speaker": client.config.speaker,
            "X-TTS-Format": client.config.format,
            "X-TTS-Attempt-Count": str(result.attempt_count),
        },
    )


@app.post(
    "/v1/tts/stream",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def stream_tts(tts_request: TTSRequest, request: Request):
    config = _load_service_config()
    metrics = _get_metrics(request)
    _require_auth(request, config, metrics)
    client = build_tts_client(tts_request, config)
    semaphore = _get_semaphore(request, config)

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    state: dict[str, object | None] = {"result": None, "error": None}

    def on_audio_chunk(chunk: bytes) -> None:
        queue.put_nowait(chunk)

    async def produce_stream() -> None:
        try:
            result = await asyncio.wait_for(
                client.synthesize(tts_request.text, on_audio_chunk=on_audio_chunk),
                timeout=config.request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            error = UpstreamTimeoutError("Service request timed out")
            _record_error(metrics, error)
            state["error"] = error
        else:
            state["result"] = result
            if not result.success:
                error = map_tts_result_error(result)
                _record_error(metrics, error)
                state["error"] = error
            else:
                metrics.request_success_total += 1
        finally:
            queue.put_nowait(None)

    metrics.requests_total += 1
    metrics.stream_requests_total += 1
    metrics.inflight_requests += 1
    await semaphore.acquire()

    producer_task = asyncio.create_task(produce_stream())
    first_chunk_task = asyncio.create_task(queue.get())
    done, _ = await asyncio.wait({producer_task, first_chunk_task}, return_when=asyncio.FIRST_COMPLETED)

    async def finalize_stream() -> None:
        semaphore.release()
        metrics.inflight_requests -= 1

    if first_chunk_task in done:
        first_chunk = first_chunk_task.result()
        if first_chunk is None:
            await producer_task
            await finalize_stream()
            error = state["error"]
            if error is not None:
                raise error
            result = state["result"]
            if result is None or not isinstance(result, TTSResult) or not result.audio_data:
                raise InternalServiceError("Streaming request finished without audio data")
            return Response(
                content=result.audio_data,
                media_type=_resolve_media_type(client.config.format),
                headers={
                    "X-TTS-Speaker": client.config.speaker,
                    "X-TTS-Format": client.config.format,
                    "X-TTS-Attempt-Count": str(result.attempt_count),
                },
            )

        async def stream_generator():
            try:
                yield first_chunk
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
                await producer_task
                if state["error"] is not None:
                    LOGGER.warning("Streaming response ended after upstream error: %s", state["error"])
            finally:
                await finalize_stream()

        return StreamingResponse(
            stream_generator(),
            media_type=_resolve_media_type(client.config.format),
            headers={
                "X-TTS-Speaker": client.config.speaker,
                "X-TTS-Format": client.config.format,
            },
        )

    first_chunk_task.cancel()
    await producer_task
    await finalize_stream()

    error = state["error"]
    if error is not None:
        raise error

    result = state["result"]
    if result is None or not isinstance(result, TTSResult) or not result.audio_data:
        raise InternalServiceError("Streaming request finished without audio data")

    return Response(
        content=result.audio_data,
        media_type=_resolve_media_type(client.config.format),
        headers={
            "X-TTS-Speaker": client.config.speaker,
            "X-TTS-Format": client.config.format,
            "X-TTS-Attempt-Count": str(result.attempt_count),
        },
    )
