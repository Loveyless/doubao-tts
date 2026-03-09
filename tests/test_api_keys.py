import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from doubao_tts import SPEAKERS, TTSResult
from service.app import app
from service.config import clear_service_config_cache


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


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tts-service.sqlite3"
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "TTS_SQLITE_PATH": str(self.db_path),
                "TTS_SQLITE_JOURNAL_MODE": "DELETE",
                "TTS_SESSION_SECRET": "test-session-secret",
                "TTS_ADMIN_BOOTSTRAP_PASSWORD": "bootstrap-secret",
                "TTS_COOKIE": VALID_COOKIE,
                "TTS_ENABLE_METRICS": "true",
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env_patcher.stop()
        clear_service_config_cache()
        for _ in range(5):
            try:
                self.temp_dir.cleanup()
                break
            except PermissionError:
                time.sleep(0.1)

    def _get_csrf_cookie(self, path: str) -> str:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        csrf_token = response.cookies.get("tts_admin_csrf") or self.client.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)
        return csrf_token

    def _current_csrf_token(self) -> str:
        csrf_token = self.client.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)
        return str(csrf_token)

    def _setup_admin_password(self, password: str = "correct horse battery staple") -> None:
        csrf_token = self._get_csrf_cookie("/admin/setup")
        response = self.client.post(
            "/admin/setup",
            json={
                "bootstrap_password": "bootstrap-secret",
                "new_password": password,
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200)

    def _create_api_key(self, name: str = "ci-key") -> dict:
        response = self.client.post(
            "/admin/api-keys",
            json={"name": name},
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertLess(response.status_code, 300, response.text)
        payload = response.json()
        self.assertEqual(payload["name"], name)
        self.assertTrue(payload["raw_key"])
        self.assertIsNotNone(payload.get("key_id"))
        return payload

    @staticmethod
    def _bearer_headers(raw_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {raw_key}"}

    @patch("service.app.build_tts_client")
    def test_admin_can_create_api_key_and_store_only_hash(self, build_tts_client_mock):
        self._setup_admin_password()

        payload = self._create_api_key(name="primary")
        raw_key = payload["raw_key"]
        key_id = payload["key_id"]

        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT name, key_prefix, key_hash, enabled FROM api_keys WHERE id = ?",
                (key_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "primary")
        self.assertTrue(row[1])
        self.assertTrue(raw_key.startswith(row[1]))
        self.assertTrue(row[2])
        self.assertNotEqual(row[2], raw_key)
        self.assertEqual(row[3], 1)

        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=1, success=True)
        )
        response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
            headers=self._bearer_headers(raw_key),
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_api_key_list_does_not_return_raw_key_again(self):
        self._setup_admin_password()
        payload = self._create_api_key(name="reporting")
        raw_key = payload["raw_key"]

        response = self.client.get("/admin/api-keys")

        self.assertEqual(response.status_code, 200)
        self.assertIn("reporting", response.text)
        self.assertNotIn(raw_key, response.text)

    @patch("service.app.build_tts_client")
    def test_public_tts_requires_and_accepts_bearer_api_key(self, build_tts_client_mock):
        self._setup_admin_password()
        payload = self._create_api_key(name="public-access")
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=2, success=True)
        )

        unauthorized_response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
        )
        self.assertEqual(unauthorized_response.status_code, 401)
        self.assertEqual(unauthorized_response.json()["error"], "unauthorized")

        authorized_response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "speaker": "taozi", "format": "aac", "speed": 0, "pitch": 0},
            headers=self._bearer_headers(payload["raw_key"]),
        )
        self.assertEqual(authorized_response.status_code, 200)
        self.assertEqual(authorized_response.content, b"audio-bytes")
        self.assertEqual(authorized_response.headers["x-tts-speaker"], SPEAKERS["taozi"])
        self.assertEqual(authorized_response.headers["x-tts-format"], "aac")
        self.assertEqual(authorized_response.headers["x-tts-attempt-count"], "2")

    @patch("service.app.build_tts_client")
    def test_disable_and_enable_api_key_controls_public_access(self, build_tts_client_mock):
        self._setup_admin_password()
        payload = self._create_api_key(name="toggle-key")
        key_id = payload["key_id"]
        raw_key = payload["raw_key"]
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"audio-bytes", attempt_count=1, success=True)
        )

        initial_response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
            headers=self._bearer_headers(raw_key),
        )
        self.assertEqual(initial_response.status_code, 200)

        disable_response = self.client.post(
            f"/admin/api-keys/{key_id}/disable",
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertLess(disable_response.status_code, 300, disable_response.text)

        disabled_response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
            headers=self._bearer_headers(raw_key),
        )
        self.assertEqual(disabled_response.status_code, 401)
        self.assertEqual(disabled_response.json()["error"], "unauthorized")

        enable_response = self.client.post(
            f"/admin/api-keys/{key_id}/enable",
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertLess(enable_response.status_code, 300, enable_response.text)

        enabled_response = self.client.post(
            "/v1/tts",
            json={"text": "测试文本", "format": "aac"},
            headers=self._bearer_headers(raw_key),
        )
        self.assertEqual(enabled_response.status_code, 200)

    @patch("service.app.build_tts_client")
    def test_stream_endpoint_accepts_bearer_api_key(self, build_tts_client_mock):
        self._setup_admin_password()
        payload = self._create_api_key(name="stream-key")
        build_tts_client_mock.return_value = FakeTTSClient(
            result=TTSResult(audio_data=b"stream-audio", attempt_count=1, success=True)
        )

        response = self.client.post(
            "/v1/tts/stream",
            json={"text": "测试文本", "format": "aac"},
            headers=self._bearer_headers(payload["raw_key"]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"stream-audio")
        self.assertEqual(response.headers["content-type"], "audio/aac")

    def test_metrics_rejects_api_key_but_accepts_admin_session(self):
        self._setup_admin_password()
        payload = self._create_api_key(name="metrics-key")

        anonymous_client = TestClient(app)
        try:
            api_key_response = anonymous_client.get(
                "/metrics",
                headers=self._bearer_headers(payload["raw_key"]),
            )
        finally:
            anonymous_client.close()

        self.assertEqual(api_key_response.status_code, 401)
        self.assertEqual(api_key_response.json()["error"], "unauthorized")

        admin_response = self.client.get("/metrics")

        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("tts_requests_total", admin_response.text)

    def test_metrics_rejects_legacy_metrics_token(self):
        self._setup_admin_password()

        with patch.dict(os.environ, {"TTS_AUTH_TOKEN": "legacy-metrics-token"}, clear=False):
            clear_service_config_cache()
            anonymous_client = TestClient(app)
            try:
                response = anonymous_client.get(
                    "/metrics",
                    headers={"Authorization": "Bearer legacy-metrics-token"},
                )
            finally:
                anonymous_client.close()
                clear_service_config_cache()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")
