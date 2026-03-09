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
from service.db import create_request_log


VALID_COOKIE = "sessionid=test-session; sid_guard=test-guard; uid_tt=test-uid"


class FakeTTSClient:
    def __init__(self, result: TTSResult):
        self.result = result
        self.config = SimpleNamespace(speaker=SPEAKERS["taozi"], format="aac")

    async def synthesize(self, text: str, on_audio_chunk=None) -> TTSResult:
        return self.result


class ReportingTests(unittest.TestCase):
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
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()
        self.client = TestClient(app)
        self.api_key_payload = self._setup_admin_and_api_key()
        self.api_headers = {"Authorization": f"Bearer {self.api_key_payload['raw_key']}"}

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

    def _setup_admin_and_api_key(self) -> dict:
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
            json={"name": "reporting-key"},
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _update_service_settings(self, **overrides) -> None:
        payload = {
            "default_speaker": "taozi",
            "default_format": "aac",
            "request_timeout_seconds": 35.0,
            "max_concurrency": 4,
            "retry_on_block": False,
            "retry_max_retries": 0,
            "retry_backoff_seconds": 1.0,
            "retry_backoff_multiplier": 2.0,
            "retry_backoff_jitter_ratio": 0.0,
            "enable_streaming": True,
            "allow_request_override": True,
            "report_retention_days": 30,
        }
        payload.update(overrides)
        response = self.client.post(
            "/admin/settings",
            json=payload,
            headers={"X-CSRF-Token": self._current_csrf_token()},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_request_logs_are_persisted_and_visible_in_report_page(self):
        first_client = FakeTTSClient(TTSResult(audio_data=b"ok", attempt_count=1, success=True))
        second_client = FakeTTSClient(
            TTSResult(success=False, error="block", error_code=BLOCK_ERROR_CODE)
        )

        with patch("service.app.build_tts_client", side_effect=[first_client, second_client]):
            success_response = self.client.post("/v1/tts", json={"text": "成功请求"}, headers=self.api_headers)
            failure_response = self.client.post("/v1/tts", json={"text": "失败请求"}, headers=self.api_headers)

        self.assertEqual(success_response.status_code, 200)
        self.assertEqual(failure_response.status_code, 502)

        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT endpoint, status_code, success, error_type FROM request_logs ORDER BY id ASC"
            ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][:3], ("/v1/tts", 200, 1))
        self.assertEqual(rows[1][:3], ("/v1/tts", 502, 0))
        self.assertEqual(rows[1][3], "bad_gateway")

        report_response = self.client.get(
            f"/admin/reports?result=failure&api_key_id={self.api_key_payload['key_id']}"
        )

        self.assertEqual(report_response.status_code, 200)
        self.assertIn("最近失败请求", report_response.text)
        self.assertIn("reporting-key", report_response.text)
        self.assertIn("block", report_response.text)

    def test_request_log_retention_prunes_old_rows(self):
        self._update_service_settings(report_retention_days=1)
        create_request_log(
            {
                "request_id": "old-log",
                "api_key_id": self.api_key_payload["key_id"],
                "doubao_account_id": 1,
                "endpoint": "/v1/tts",
                "speaker": SPEAKERS["taozi"],
                "format": "aac",
                "text_chars": 3,
                "status_code": 200,
                "success": True,
                "latency_ms": 1,
                "error_type": None,
                "error_detail": None,
                "created_at": "2000-01-01T00:00:00+00:00",
            }
        )

        with patch(
            "service.app.build_tts_client",
            return_value=FakeTTSClient(TTSResult(audio_data=b"ok", attempt_count=1, success=True)),
        ):
            response = self.client.post("/v1/tts", json={"text": "新的请求"}, headers=self.api_headers)

        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute("SELECT request_id FROM request_logs ORDER BY id ASC").fetchall()

        self.assertTrue(rows)
        self.assertNotIn("old-log", [row[0] for row in rows])

    def test_admin_test_tts_page_and_route_work(self):
        page_response = self.client.get("/admin/test-tts")
        self.assertEqual(page_response.status_code, 200)
        self.assertIn("测试合成", page_response.text)

        with patch(
            "service.admin_routes.synthesize_once",
            return_value=(
                TTSResult(audio_data=b"admin-audio", attempt_count=1, success=True),
                SimpleNamespace(config=SimpleNamespace(speaker=SPEAKERS["taozi"], format="aac")),
            ),
        ):
            response = self.client.post(
                "/admin/test-tts",
                json={"text": "后台测试"},
                headers={"X-CSRF-Token": self._current_csrf_token()},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["audio_bytes"], len(b"admin-audio"))
        self.assertEqual(payload["account_name"], "seed-from-env")
