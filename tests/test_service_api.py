import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from doubao_tts import SPEAKERS, TTSResult
from service.app import app
from service.config import ServiceConfig, clear_service_config_cache, get_service_config
from service.dependencies import build_tts_client
from service.errors import InternalServiceError
from service.models import TTSRequest


VALID_COOKIE = "sessionid=test-session; sid_guard=test-guard; uid_tt=test-uid"


class FakeTTSClient:
    def __init__(self, result: TTSResult, speaker: str = SPEAKERS["taozi"]):
        self.result = result
        self.config = SimpleNamespace(speaker=speaker, format="aac")
        self.received_text = None

    async def synthesize(self, text: str, on_audio_chunk=None) -> TTSResult:
        self.received_text = text
        if on_audio_chunk and self.result.audio_data:
            midpoint = max(1, len(self.result.audio_data) // 2)
            on_audio_chunk(self.result.audio_data[:midpoint])
            on_audio_chunk(self.result.audio_data[midpoint:])
        return self.result


class SlowTTSClient:
    def __init__(self):
        self.config = SimpleNamespace(speaker=SPEAKERS["taozi"], format="aac")

    async def synthesize(self, text: str, on_audio_chunk=None) -> TTSResult:
        await asyncio.sleep(0.05)
        return TTSResult(success=True, audio_data=b"late-audio")


class ServiceConfigTests(unittest.TestCase):
    def tearDown(self):
        clear_service_config_cache()

    def test_service_config_reads_env(self):
        with patch.dict(
            os.environ,
            {
                "TTS_COOKIE": VALID_COOKIE,
                "TTS_DEFAULT_SPEAKER": "rap",
                "TTS_DEFAULT_FORMAT": "mp3",
                "TTS_PORT": "9090",
                "TTS_MAX_CONCURRENCY": "3",
                "TTS_REQUEST_TIMEOUT_SECONDS": "12.5",
                "TTS_AUTH_TOKEN": "secret-token",
            },
            clear=False,
        ):
            config = get_service_config()

        self.assertEqual(config.cookie, VALID_COOKIE)
        self.assertEqual(config.default_speaker, "rap")
        self.assertEqual(config.default_format, "mp3")
        self.assertEqual(config.port, 9090)
        self.assertEqual(config.max_concurrency, 3)
        self.assertEqual(config.request_timeout_seconds, 12.5)
        self.assertEqual(config.auth_token, "secret-token")


class ServiceDependencyTests(unittest.TestCase):
    def tearDown(self):
        clear_service_config_cache()

    def test_build_tts_client_uses_explicit_cookie_without_autoload(self):
        request = TTSRequest(text="测试文本", speaker="taozi", format="aac", speed=0, pitch=0)

        service_config = ServiceConfig(cookie=VALID_COOKIE)
        client = build_tts_client(request, service_config)

        self.assertEqual(client.config.cookie, VALID_COOKIE)
        self.assertFalse(client.config.autoload_cookie)
        self.assertEqual(client.config.speaker, SPEAKERS["taozi"])
        self.assertEqual(client.config.format, "aac")

    def test_build_tts_client_rejects_missing_cookie(self):
        request = TTSRequest(text="测试文本", speaker="taozi", format="aac", speed=0, pitch=0)

        with self.assertRaises(InternalServiceError) as context:
            build_tts_client(request, ServiceConfig(cookie=""))

        self.assertEqual(context.exception.status_code, 500)
        self.assertIn("TTS_COOKIE is not configured", context.exception.detail)


class ServiceApiTests(unittest.TestCase):
    def setUp(self):
        self.base_env = {
            "TTS_COOKIE": VALID_COOKIE,
            "TTS_ENABLE_METRICS": "true",
        }
        self.env_patcher = patch.dict(os.environ, self.base_env, clear=False)
        self.env_patcher.start()
        clear_service_config_cache()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env_patcher.stop()
        clear_service_config_cache()

    def test_healthz_returns_ok(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_healthz_reports_missing_cookie(self):
        self.env_patcher.stop()
        previous_cookie = os.environ.pop("TTS_COOKIE", None)
        clear_service_config_cache()
        try:
            response = self.client.get("/healthz")
        finally:
            if previous_cookie is not None:
                os.environ["TTS_COOKIE"] = previous_cookie
            self.env_patcher.start()
            clear_service_config_cache()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "service_unavailable")

    def test_list_speakers_returns_mapping(self):
        response = self.client.get("/v1/speakers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["taozi"], SPEAKERS["taozi"])

    def test_tts_rejects_blank_text(self):
        response = self.client.post("/v1/tts", json={"text": "   ", "format": "aac"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_request")

    def test_tts_rejects_invalid_format(self):
        response = self.client.post("/v1/tts", json={"text": "测试文本", "format": "wav"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_request")

    @patch("service.app.build_tts_client")
    def test_tts_returns_audio_binary(self, build_tts_client_mock):
        result = TTSResult(audio_data=b"audio-bytes", attempt_count=2, success=True)
        fake_client = FakeTTSClient(result=result, speaker=SPEAKERS["taozi"])
        build_tts_client_mock.return_value = fake_client

        response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "speaker": "taozi", "format": "aac", "speed": 0, "pitch": 0},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"audio-bytes")
        self.assertEqual(response.headers["content-type"], "audio/aac")
        self.assertEqual(response.headers["x-tts-speaker"], SPEAKERS["taozi"])
        self.assertEqual(response.headers["x-tts-format"], "aac")
        self.assertEqual(response.headers["x-tts-attempt-count"], "2")
        self.assertEqual(fake_client.received_text, "测试文本")

    @patch("service.app.build_tts_client")
    def test_tts_maps_upstream_error_to_502(self, build_tts_client_mock):
        result = TTSResult(success=False, error="block", error_code=710022002)
        build_tts_client_mock.return_value = FakeTTSClient(result=result)

        response = self.client.post("/v1/tts", json={"text": "测试文本", "format": "aac"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "bad_gateway")

    @patch("service.app.build_tts_client")
    def test_tts_maps_timeout_to_504(self, build_tts_client_mock):
        result = TTSResult(success=False, error="接收超时，音频可能不完整")
        build_tts_client_mock.return_value = FakeTTSClient(result=result)

        response = self.client.post("/v1/tts", json={"text": "测试文本", "format": "aac"})

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"], "gateway_timeout")

    @patch("service.app.build_tts_client")
    def test_service_timeout_control_returns_504(self, build_tts_client_mock):
        self.env_patcher.stop()
        self.env_patcher = patch.dict(
            os.environ,
            {
                **self.base_env,
                "TTS_REQUEST_TIMEOUT_SECONDS": "0.01",
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        build_tts_client_mock.return_value = SlowTTSClient()

        response = self.client.post("/v1/tts", json={"text": "测试文本", "format": "aac"})

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"], "gateway_timeout")

    @patch("service.app.build_tts_client")
    def test_stream_endpoint_returns_audio(self, build_tts_client_mock):
        result = TTSResult(audio_data=b"stream-audio", attempt_count=1, success=True)
        build_tts_client_mock.return_value = FakeTTSClient(result=result)

        response = self.client.post("/v1/tts/stream", json={"text": "测试文本", "format": "aac"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"stream-audio")
        self.assertEqual(response.headers["content-type"], "audio/aac")

    @patch("service.app.build_tts_client")
    def test_metrics_endpoint_reports_counters(self, build_tts_client_mock):
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=1, success=True)
        )

        self.client.post("/v1/tts", json={"text": "测试文本", "format": "aac"})
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("tts_requests_total", response.text)
        self.assertIn("tts_request_success_total", response.text)

    @patch("service.app.build_tts_client")
    def test_auth_guard_rejects_missing_token(self, build_tts_client_mock):
        self.env_patcher.stop()
        self.env_patcher = patch.dict(
            os.environ,
            {
                **self.base_env,
                "TTS_AUTH_TOKEN": "secret-token",
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=1, success=True)
        )

        response = self.client.post("/v1/tts", json={"text": "测试文本", "format": "aac"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    @patch("service.app.build_tts_client")
    def test_auth_guard_accepts_bearer_token(self, build_tts_client_mock):
        self.env_patcher.stop()
        self.env_patcher = patch.dict(
            os.environ,
            {
                **self.base_env,
                "TTS_AUTH_TOKEN": "secret-token",
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=1, success=True)
        )

        response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(response.status_code, 200)

