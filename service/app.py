import asyncio
import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from doubao_tts import SPEAKERS, TTSResult
from service.admin_routes import admin_router
from service.auth import hash_api_key, require_admin_session
from service.config import ConfigError, ServiceConfig, get_service_config
from service.credential_pool import (
    build_account_cookie,
    is_retryable_account_error,
    mark_account_attempt_failure,
    mark_account_attempt_success,
    select_account,
)
from service.db import (
    count_doubao_accounts,
    count_enabled_api_keys,
    count_healthy_doubao_accounts,
    fetch_admin_settings,
    find_enabled_api_key_by_hash,
    initialize_database,
    touch_api_key_last_used,
)
from service.dependencies import build_tts_client
from service.errors import (
    BadRequestError,
    InternalServiceError,
    ServiceHTTPError,
    ServiceUnavailableError,
    UnauthorizedError,
    UpstreamTimeoutError,
    map_tts_result_error,
)
from service.models import ErrorResponse, HealthResponse, TTSRequest
from service.reporting import finish_request_log, start_request_log

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


@app.on_event("startup")
async def startup() -> None:
    config = ServiceConfig.from_env()
    initialize_database(seed_service_settings=config.persistent_settings_payload())


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


def _field_was_provided(model, field_name: str) -> bool:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(model, "__fields_set__", set())
    return field_name in fields_set


def _resolve_requested_speaker(tts_request: TTSRequest, config: ServiceConfig) -> str:
    requested = tts_request.speaker or config.default_speaker
    return SPEAKERS.get(requested, requested)


def _resolve_requested_format(tts_request: TTSRequest, config: ServiceConfig) -> str:
    return tts_request.format or config.default_format


def _validate_request_override_policy(tts_request: TTSRequest, config: ServiceConfig) -> None:
    if config.allow_request_override:
        return

    if tts_request.speaker is not None and tts_request.speaker != config.default_speaker:
        raise BadRequestError("Speaker override is disabled")
    if tts_request.format is not None and tts_request.format != config.default_format:
        raise BadRequestError("Format override is disabled")
    if _field_was_provided(tts_request, "speed"):
        raise BadRequestError("Speed override is disabled")
    if _field_was_provided(tts_request, "pitch"):
        raise BadRequestError("Pitch override is disabled")


def _load_service_config() -> ServiceConfig:
    try:
        return get_service_config()
    except ConfigError as exc:
        raise InternalServiceError(str(exc)) from exc


def _extract_bearer_token(request: Request) -> str:
    header_value = request.headers.get("Authorization", "")
    bearer_prefix = "Bearer "
    if not header_value.startswith(bearer_prefix):
        return ""
    return header_value[len(bearer_prefix) :].strip()


def _require_metrics_access(request: Request, metrics: ServiceMetrics) -> None:
    try:
        require_admin_session(request)
    except UnauthorizedError as exc:
        metrics.request_failure_total += 1
        metrics.unauthorized_total += 1
        raise UnauthorizedError("Admin login required for metrics") from exc


def _require_api_key(request: Request, metrics: ServiceMetrics) -> dict[str, object]:
    token = _extract_bearer_token(request)
    if not token:
        metrics.request_failure_total += 1
        metrics.unauthorized_total += 1
        raise UnauthorizedError("Missing API key")

    if count_enabled_api_keys() <= 0:
        metrics.request_failure_total += 1
        metrics.unauthorized_total += 1
        raise UnauthorizedError("No enabled API keys configured")

    api_key = find_enabled_api_key_by_hash(hash_api_key(token))
    if api_key is None:
        metrics.request_failure_total += 1
        metrics.unauthorized_total += 1
        raise UnauthorizedError("Invalid API key")

    touch_api_key_last_used(int(api_key["id"]))
    return api_key


def _build_health_payload(detail: str | None = None, config_loaded: bool = True) -> HealthResponse:
    admin_settings = fetch_admin_settings()
    setup_completed = bool(admin_settings.get("setup_completed"))
    enabled_api_keys = count_enabled_api_keys()
    total_accounts = count_doubao_accounts()
    healthy_accounts = count_healthy_doubao_accounts()
    ready = config_loaded and setup_completed and enabled_api_keys > 0 and healthy_accounts > 0
    if detail is None and not ready:
        if not setup_completed:
            detail = "Admin setup has not been completed"
        elif enabled_api_keys <= 0:
            detail = "No enabled API keys configured"
        elif healthy_accounts <= 0:
            detail = "No healthy Doubao accounts configured"
    return HealthResponse(
        status="ok" if ready else "not_ready",
        ready=ready,
        setup_completed=setup_completed,
        enabled_api_keys=enabled_api_keys,
        total_accounts=total_accounts,
        healthy_accounts=healthy_accounts,
        detail=detail,
    )


async def _synthesize_once(
    tts_request: TTSRequest,
    config: ServiceConfig,
    *,
    cookie_override: str,
) -> tuple[TTSResult, object]:
    client = build_tts_client(
        tts_request,
        config,
        cookie_override=cookie_override,
    )
    try:
        result = await asyncio.wait_for(
            client.synthesize(tts_request.text),
            timeout=config.request_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise UpstreamTimeoutError("Service request timed out") from exc

    if not result.success:
        raise map_tts_result_error(result)

    return result, client


async def _run_non_stream_with_account_pool(
    tts_request: TTSRequest,
    config: ServiceConfig,
) -> tuple[TTSResult | None, object | None, dict[str, object] | None, ServiceHTTPError | None]:
    attempted_ids: list[int] = []
    last_account: dict[str, object] | None = None
    last_error: ServiceHTTPError | None = None

    for _ in range(2):
        try:
            account = select_account(exclude_ids=attempted_ids)
        except ServiceUnavailableError as exc:
            return None, None, last_account, last_error or exc

        account_id = int(account["id"])
        attempted_ids.append(account_id)
        last_account = account
        try:
            result, client = await _synthesize_once(
                tts_request,
                config,
                cookie_override=build_account_cookie(account),
            )
        except ServiceHTTPError as error:
            mark_account_attempt_failure(account_id, error)
            last_error = error
            if not is_retryable_account_error(error):
                break
            continue

        mark_account_attempt_success(account_id)
        return result, client, account, None

    return None, None, last_account, last_error


async def _start_stream_attempt(
    tts_request: TTSRequest,
    config: ServiceConfig,
    account: dict[str, object],
) -> tuple[object, asyncio.Queue[bytes | None], dict[str, object | None], asyncio.Task, bytes | None]:
    client = build_tts_client(
        tts_request,
        config,
        cookie_override=build_account_cookie(account),
    )
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
        except asyncio.TimeoutError as exc:
            state["error"] = UpstreamTimeoutError("Service request timed out")
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.exception("Unexpected streaming error", exc_info=exc)
            state["error"] = InternalServiceError()
        else:
            state["result"] = result
            if not result.success:
                state["error"] = map_tts_result_error(result)
        finally:
            queue.put_nowait(None)

    producer_task = asyncio.create_task(produce_stream())
    first_item = await queue.get()
    return client, queue, state, producer_task, first_item


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
        get_service_config()
        payload = _build_health_payload()
    except (ConfigError, RuntimeError) as exc:
        payload = _build_health_payload(detail=str(exc), config_loaded=False)
    status_code = 200 if payload.ready else 503
    return JSONResponse(status_code=status_code, content=_model_dump(payload))


@app.get("/v1/speakers")
async def list_speakers() -> dict[str, str]:
    return SPEAKERS


@app.get("/metrics")
async def metrics(request: Request):
    config = _load_service_config()
    metric_state = _get_metrics(request)
    _require_metrics_access(request, metric_state)
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
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def synthesize_tts(tts_request: TTSRequest, request: Request):
    config = _load_service_config()
    metrics = _get_metrics(request)
    api_key = _require_api_key(request, metrics)
    _validate_request_override_policy(tts_request, config)
    semaphore = _get_semaphore(request, config)
    report_context = start_request_log("/v1/tts", int(api_key["id"]), tts_request.text)
    resolved_speaker = _resolve_requested_speaker(tts_request, config)
    resolved_format = _resolve_requested_format(tts_request, config)

    metrics.requests_total += 1
    metrics.inflight_requests += 1
    account: dict[str, object] | None = None
    try:
        async with semaphore:
            result, client, account, error = await _run_non_stream_with_account_pool(tts_request, config)
    finally:
        metrics.inflight_requests -= 1

    if error is not None:
        _record_error(metrics, error)
        finish_request_log(
            report_context,
            account_id=int(account["id"]) if account is not None else None,
            speaker=resolved_speaker,
            audio_format=resolved_format,
            status_code=error.status_code,
            success=False,
            error_type=error.error,
            error_detail=error.detail,
            retention_days=config.report_retention_days,
        )
        raise error

    assert result is not None and client is not None and account is not None
    metrics.request_success_total += 1
    finish_request_log(
        report_context,
        account_id=int(account["id"]),
        speaker=str(client.config.speaker),
        audio_format=str(client.config.format),
        status_code=200,
        success=True,
        retention_days=config.report_retention_days,
    )
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
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def stream_tts(tts_request: TTSRequest, request: Request):
    config = _load_service_config()
    metrics = _get_metrics(request)
    api_key = _require_api_key(request, metrics)
    if not config.enable_streaming:
        raise ServiceUnavailableError("Streaming is disabled")
    _validate_request_override_policy(tts_request, config)

    metrics.requests_total += 1
    metrics.stream_requests_total += 1
    metrics.inflight_requests += 1

    semaphore = _get_semaphore(request, config)
    await semaphore.acquire()
    release_in_generator = False
    report_context = start_request_log("/v1/tts/stream", int(api_key["id"]), tts_request.text)
    fallback_speaker = _resolve_requested_speaker(tts_request, config)
    fallback_format = _resolve_requested_format(tts_request, config)
    attempted_ids: list[int] = []
    last_account: dict[str, object] | None = None
    last_error: ServiceHTTPError | None = None

    try:
        for _ in range(2):
            try:
                account = select_account(exclude_ids=attempted_ids)
            except ServiceUnavailableError as exc:
                error = last_error or exc
                _record_error(metrics, error)
                finish_request_log(
                    report_context,
                    account_id=int(last_account["id"]) if last_account is not None else None,
                    speaker=fallback_speaker,
                    audio_format=fallback_format,
                    status_code=error.status_code,
                    success=False,
                    error_type=error.error,
                    error_detail=error.detail,
                    retention_days=config.report_retention_days,
                )
                raise error

            account_id = int(account["id"])
            attempted_ids.append(account_id)
            last_account = account
            client, queue, state, producer_task, first_item = await _start_stream_attempt(
                tts_request,
                config,
                account,
            )

            if first_item is None:
                await producer_task
                error = state["error"]
                if error is None:
                    result = state["result"]
                    if result is None or not isinstance(result, TTSResult) or not result.audio_data:
                        error = InternalServiceError("Streaming request finished without audio data")
                    else:
                        mark_account_attempt_success(account_id)
                        metrics.request_success_total += 1
                        finish_request_log(
                            report_context,
                            account_id=account_id,
                            speaker=str(client.config.speaker),
                            audio_format=str(client.config.format),
                            status_code=200,
                            success=True,
                            retention_days=config.report_retention_days,
                        )
                        return Response(
                            content=result.audio_data,
                            media_type=_resolve_media_type(client.config.format),
                            headers={
                                "X-TTS-Speaker": client.config.speaker,
                                "X-TTS-Format": client.config.format,
                                "X-TTS-Attempt-Count": str(result.attempt_count),
                            },
                        )

                assert isinstance(error, ServiceHTTPError)
                mark_account_attempt_failure(account_id, error)
                last_error = error
                if is_retryable_account_error(error):
                    continue

                _record_error(metrics, error)
                finish_request_log(
                    report_context,
                    account_id=account_id,
                    speaker=str(client.config.speaker),
                    audio_format=str(client.config.format),
                    status_code=error.status_code,
                    success=False,
                    error_type=error.error,
                    error_detail=error.detail,
                    retention_days=config.report_retention_days,
                )
                raise error

            release_in_generator = True

            async def stream_generator():
                try:
                    yield first_item
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        yield item
                    await producer_task
                    error = state["error"]
                    if error is not None and isinstance(error, ServiceHTTPError):
                        LOGGER.warning("Streaming response ended after upstream error: %s", error)
                        mark_account_attempt_failure(account_id, error)
                        _record_error(metrics, error)
                        finish_request_log(
                            report_context,
                            account_id=account_id,
                            speaker=str(client.config.speaker),
                            audio_format=str(client.config.format),
                            status_code=200,
                            success=False,
                            error_type=error.error,
                            error_detail=error.detail,
                            retention_days=config.report_retention_days,
                        )
                    else:
                        mark_account_attempt_success(account_id)
                        metrics.request_success_total += 1
                        finish_request_log(
                            report_context,
                            account_id=account_id,
                            speaker=str(client.config.speaker),
                            audio_format=str(client.config.format),
                            status_code=200,
                            success=True,
                            retention_days=config.report_retention_days,
                        )
                finally:
                    semaphore.release()
                    metrics.inflight_requests -= 1

            return StreamingResponse(
                stream_generator(),
                media_type=_resolve_media_type(client.config.format),
                headers={
                    "X-TTS-Speaker": client.config.speaker,
                    "X-TTS-Format": client.config.format,
                },
            )

        error = last_error or ServiceUnavailableError("No healthy Doubao accounts configured")
        _record_error(metrics, error)
        finish_request_log(
            report_context,
            account_id=int(last_account["id"]) if last_account is not None else None,
            speaker=fallback_speaker,
            audio_format=fallback_format,
            status_code=error.status_code,
            success=False,
            error_type=error.error,
            error_detail=error.detail,
            retention_days=config.report_retention_days,
        )
        raise error
    finally:
        if not release_in_generator:
            semaphore.release()
            metrics.inflight_requests -= 1


app.include_router(admin_router)
