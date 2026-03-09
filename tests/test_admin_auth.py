import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from service.app import app
from service.config import clear_service_config_cache


class AdminAuthTests(unittest.TestCase):
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

    def test_admin_setup_auto_initializes_sqlite_schema(self):
        self.assertFalse(self.db_path.exists())

        response = self.client.get("/admin/setup")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.db_path.exists())

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()

        table_names = {row[0] for row in rows}
        self.assertTrue(
            {
                "admin_settings",
                "service_settings",
                "doubao_accounts",
                "api_keys",
                "request_logs",
            }.issubset(table_names)
        )

        with sqlite3.connect(self.db_path) as conn:
            service_settings_count = conn.execute(
                "SELECT COUNT(*) FROM service_settings"
            ).fetchone()[0]

        self.assertEqual(service_settings_count, 1)

    def test_admin_setup_requires_csrf_token(self):
        self.client.get("/admin/setup")

        response = self.client.post(
            "/admin/setup",
            json={
                "bootstrap_password": "bootstrap-secret",
                "new_password": "correct horse battery staple",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_healthz_is_not_ready_before_admin_setup(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "not_ready",
                "ready": False,
                "setup_completed": False,
                "enabled_api_keys": 0,
                "total_accounts": 0,
                "healthy_accounts": 0,
                "detail": "Admin setup has not been completed",
            },
        )

    def test_admin_setup_persists_password_hash(self):
        self._setup_admin_password()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT setup_completed, password_hash FROM admin_settings WHERE id = 1"
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        self.assertTrue(row[1])
        self.assertNotEqual(row[1], "correct horse battery staple")

    def test_admin_login_establishes_session(self):
        self._setup_admin_password()
        self.client.cookies.clear()

        csrf_token = self._get_csrf_cookie("/admin/login")
        response = self.client.post(
            "/admin/login",
            json={"password": "correct horse battery staple"},
            headers={"X-CSRF-Token": csrf_token},
        )

        self.assertEqual(response.status_code, 200)

        dashboard = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(dashboard.status_code, 200)

    def test_admin_login_rejects_invalid_password(self):
        self._setup_admin_password()
        self.client.cookies.clear()

        csrf_token = self._get_csrf_cookie("/admin/login")
        response = self.client.post(
            "/admin/login",
            json={"password": "wrong-password"},
            headers={"X-CSRF-Token": csrf_token},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_admin_logout_clears_session(self):
        self._setup_admin_password()
        self.client.cookies.clear()

        csrf_token = self._get_csrf_cookie("/admin/login")
        login_response = self.client.post(
            "/admin/login",
            json={"password": "correct horse battery staple"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(login_response.status_code, 200)

        csrf_token = self.client.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)
        logout_response = self.client.post(
            "/admin/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(logout_response.status_code, 200)

        dashboard = self.client.get("/admin", follow_redirects=False)
        self.assertIn(dashboard.status_code, {302, 303, 307})
        self.assertTrue(dashboard.headers["location"].endswith("/admin/login"))

    def test_admin_redirects_to_login_when_not_authenticated(self):
        self._setup_admin_password()
        self.client.cookies.clear()

        response = self.client.get("/admin", follow_redirects=False)

        self.assertIn(response.status_code, {302, 303, 307})
        self.assertTrue(response.headers["location"].endswith("/admin/login"))

    def test_admin_settings_update_persists_service_settings(self):
        self._setup_admin_password()
        csrf_token = self.client.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)

        response = self.client.post(
            "/admin/settings",
            json={
                "default_speaker": "rap",
                "default_format": "mp3",
                "request_timeout_seconds": 12.5,
                "max_concurrency": 6,
                "retry_on_block": True,
                "retry_max_retries": 2,
                "retry_backoff_seconds": 1.2,
                "retry_backoff_multiplier": 2.5,
                "retry_backoff_jitter_ratio": 0.3,
                "enable_streaming": False,
                "allow_request_override": False,
                "report_retention_days": 45,
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 200)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    default_speaker,
                    default_format,
                    request_timeout_seconds,
                    max_concurrency,
                    retry_on_block,
                    enable_streaming,
                    allow_request_override,
                    report_retention_days
                FROM service_settings WHERE id = 1
                """
            ).fetchone()

        self.assertEqual(row[0], "rap")
        self.assertEqual(row[1], "mp3")
        self.assertEqual(row[2], 12.5)
        self.assertEqual(row[3], 6)
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5], 0)
        self.assertEqual(row[6], 0)
        self.assertEqual(row[7], 45)

    def test_admin_setup_sets_secure_cookies_when_enabled(self):
        self.env_patcher.stop()
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "TTS_SQLITE_PATH": str(self.db_path),
                "TTS_SQLITE_JOURNAL_MODE": "DELETE",
                "TTS_SESSION_SECRET": "test-session-secret",
                "TTS_ADMIN_BOOTSTRAP_PASSWORD": "bootstrap-secret",
                "TTS_SECURE_COOKIES": "true",
            },
            clear=False,
        )
        self.env_patcher.start()
        clear_service_config_cache()

        response = self.client.get("/admin/setup")
        self.assertEqual(response.status_code, 200)
        csrf_token = response.cookies.get("tts_admin_csrf")
        self.assertTrue(csrf_token)
        response = self.client.post(
            "/admin/setup",
            json={
                "bootstrap_password": "bootstrap-secret",
                "new_password": "correct horse battery staple",
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Cookie": f"tts_admin_csrf={csrf_token}",
            },
        )

        self.assertEqual(response.status_code, 200)
        set_cookie_headers = response.headers.get_list("set-cookie")
        self.assertTrue(any("Secure" in header for header in set_cookie_headers))
