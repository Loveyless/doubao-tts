from dataclasses import dataclass

from doubao_tts import BLOCK_ERROR_CODE, TTSResult

_TIMEOUT_MARKERS = (
    "超时",
    "不完整",
    "未收到音频数据",
    "未收到 open_success 事件",
    "连接未正常关闭",
    "音频流提前结束",
)

_UPSTREAM_MARKERS = (
    "握手",
    "Cookie",
    "连接失败",
    "连接异常关闭",
    "收到无法解析的 JSON 响应",
    "block",
)


@dataclass
class ServiceHTTPError(Exception):
    detail: str
    status_code: int
    error: str


class BadRequestError(ServiceHTTPError):
    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=400, error="bad_request")


class UnauthorizedError(ServiceHTTPError):
    def __init__(self, detail: str = "Missing or invalid bearer token"):
        super().__init__(detail=detail, status_code=401, error="unauthorized")


class UpstreamBadGatewayError(ServiceHTTPError):
    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=502, error="bad_gateway")


class UpstreamTimeoutError(ServiceHTTPError):
    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=504, error="gateway_timeout")


class ServiceUnavailableError(ServiceHTTPError):
    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=503, error="service_unavailable")


class InternalServiceError(ServiceHTTPError):
    def __init__(self, detail: str = "Internal service error"):
        super().__init__(detail=detail, status_code=500, error="internal_error")


def map_tts_result_error(result: TTSResult) -> ServiceHTTPError:
    detail = result.error or "TTS synthesis failed"

    if detail == "文本不能为空":
        return BadRequestError(detail)

    if result.error_code == BLOCK_ERROR_CODE:
        return UpstreamBadGatewayError(detail)

    if any(marker in detail for marker in _TIMEOUT_MARKERS):
        return UpstreamTimeoutError(detail)

    if any(marker in detail for marker in _UPSTREAM_MARKERS):
        return UpstreamBadGatewayError(detail)

    if result.error_code is not None:
        return UpstreamBadGatewayError(detail)

    return InternalServiceError(detail)

