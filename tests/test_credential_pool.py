import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from doubao_tts import BLOCK_ERROR_CODE, SPEAKERS, TTSResult
from service.app import app
from service.config import clear_service_config_cache


SEED_COOKIE = "sessionid=seed-session; sid_guard=seed-guard; uid_tt=seed-uid"
SECOND_COOKIE = "sessionid=second-session; sid_guard=second-guard; uid_tt=second-uid"


class FakeTTSClient:
    def __init__(self, result: TTSResult):
        self.result = result
        self.config = SimpleNamespace(speaker=SPEAKERS["taozi"], format="aac")

    async def synthesize(self, text: str, on_audio_chunk=None) -> TTSResult:
        if on_audio_chunk and self.result.audio_data:
            midpoint = max(1, len(self.result.audio_data) // 2)
            on_audio_chunk(self.result.audio_data[:midpoint])
            on_audio_chunk(self.result.audio_data[midpoint:])
        return self.result


class CredentialPoolTests(unittest.TestCase):
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
                "TTS_COOKIE": SEED_COOKIE,
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        self.client = TestClient(app)
        self.api_key = self._create_api_key()
        self.api_headers = {"Authorization": f"Bearer {self.api_key}"}

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
        return str(csrf_token)

    def _current_csrf_token(self) -> str:
        csrf_token = self.client.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)
        return str(csrf_token)

    def _create_api_key(self) -> str:
        csrf_token = self._get_csrf_cookie("/admin/setup")
        response = self.client.post(
            "/admin/setup",
            json={
                "bootstrap_password": "bootstrap-secret",
                "new_password": "correct horse battery staple",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/admin/api-keys",
            json={"name": "credential-pool-tests"},
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["raw_key"]

    def _create_second_account(self) -> int:
        response = self.client.post(
            "/admin/accounts",
            json={
                "name": "second-account",
                "sessionid": "second-session",
                "sid_guard": "second-guard",
                "uid_tt": "second-uid",
            },
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return int(response.json()["account_id"])

    def test_public_requests_rotate_across_healthy_accounts(self):
        self._create_second_account()
        seen_cookies: list[str] = []

        def build_client_side_effect(request, service_config=None, *, cookie_override=None):
            seen_cookies.append(str(cookie_override))
            return FakeTTSClient(TTSResult(audio_data=b"audio", attempt_count=1, success=True))

        with patch("service.app.build_tts_client", side_effect=build_client_side_effect):
            first = self.client.post("/v1/tts", json={"text": "第一次"}, headers=self.api_headers)
            second = self.client.post("/v1/tts", json={"text": "第二次"}, headers=self.api_headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(seen_cookies), 2)
        self.assertNotEqual(seen_cookies[0], seen_cookies[1])

    def test_retryable_failure_cools_down_first_account_and_uses_second_account(self):
        self._create_second_account()
        seen_cookies: list[str] = []

        def build_client_side_effect(request, service_config=None, *, cookie_override=None):
            seen_cookies.append(str(cookie_override))
            if cookie_override == SEED_COOKIE:
                return FakeTTSClient(
                    TTSResult(success=False, error="block", error_code=BLOCK_ERROR_CODE)
                )
            return FakeTTSClient(TTSResult(audio_data=b"ok", attempt_count=1, success=True))

        with patch("service.app.build_tts_client", side_effect=build_client_side_effect):
            response = self.client.post("/v1/tts", json={"text": "需要切换"}, headers=self.api_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen_cookies, [SEED_COOKIE, SECOND_COOKIE])

        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT name, status, cooldown_until, success_count, failure_count FROM doubao_accounts ORDER BY id ASC"
            ).fetchall()

        self.assertEqual(rows[0][0], "seed-from-env")
        self.assertEqual(rows[0][1], "cooldown")
        self.assertIsNotNone(rows[0][2])
        self.assertEqual(rows[0][4], 1)
        self.assertEqual(rows[1][0], "second-account")
        self.assertEqual(rows[1][3], 1)

    def test_stream_falls_back_before_first_chunk(self):
        self._create_second_account()
        seen_cookies: list[str] = []

        def build_client_side_effect(request, service_config=None, *, cookie_override=None):
            seen_cookies.append(str(cookie_override))
            if cookie_override == SEED_COOKIE:
                return FakeTTSClient(
                    TTSResult(success=False, error="block", error_code=BLOCK_ERROR_CODE)
                )
            return FakeTTSClient(TTSResult(audio_data=b"stream-audio", attempt_count=1, success=True))

        with patch("service.app.build_tts_client", side_effect=build_client_side_effect):
            response = self.client.post(
                "/v1/tts/stream",
                json={"text": "流式切换"},
                headers=self.api_headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"stream-audio")
        self.assertEqual(seen_cookies, [SEED_COOKIE, SECOND_COOKIE])
