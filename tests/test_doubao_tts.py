import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

import doubao_tts


VALID_COOKIE = "sessionid=test-session; sid_guard=test-guard; uid_tt=test-uid"


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent_messages = []

    async def send(self, message):
        self.sent_messages.append(message)

    async def recv(self):
        if not self.messages:
            raise AssertionError("测试消息耗尽，说明协议流程和预期不一致")

        message = self.messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message


class FakeConnect:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb):
        return False


class CookieConfigTests(unittest.TestCase):
    def test_save_and_load_cookie_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / ".cookie.json"
            legacy_path = Path(temp_dir) / ".cookie"

            with patch.object(doubao_tts, "COOKIE_CONFIG_FILE", json_path), patch.object(
                doubao_tts, "LEGACY_COOKIE_FILE", legacy_path
            ):
                self.assertTrue(doubao_tts.save_cookie_to_file(VALID_COOKIE))

                payload = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["cookie"]["sessionid"], "test-session")
                self.assertEqual(payload["cookie"]["sid_guard"], "test-guard")
                self.assertEqual(payload["cookie"]["uid_tt"], "test-uid")
                self.assertEqual(doubao_tts.load_cookie_from_file(), VALID_COOKIE)

    def test_client_auto_loads_cookie_from_local_config(self):
        with patch.object(doubao_tts, "load_cookie_from_file", return_value=VALID_COOKIE):
            client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig())
        self.assertEqual(client.config.cookie, VALID_COOKIE)

    def test_build_headers_normalizes_cookie(self):
        scrambled_cookie = "uid_tt=test-uid; sessionid=test-session; sid_guard=test-guard"
        client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=scrambled_cookie))

        headers = client.build_headers()

        self.assertEqual(headers["Cookie"], VALID_COOKIE)
        self.assertEqual(headers["Origin"], "https://www.doubao.com")


class SynthesizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthesize_marks_success_only_after_normal_close(self):
        websocket = FakeWebSocket(
            [
                json.dumps({"event": "open_success", "code": 0, "message": ""}),
                json.dumps({
                    "event": "sentence_start",
                    "sentence_start_result": {"readable_text": "测试文本"},
                }),
                b"audio-chunk",
                json.dumps({"event": "sentence_end", "code": 0, "message": ""}),
                ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"), True),
            ]
        )

        with patch.object(doubao_tts.websockets, "connect", return_value=FakeConnect(websocket)):
            client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=VALID_COOKIE))
            result = await client.synthesize("测试文本")

        self.assertTrue(result.success)
        self.assertEqual(result.audio_data, b"audio-chunk")
        self.assertEqual(result.sentences, ["测试文本"])
        self.assertEqual(result.event_order, ["open_success", "sentence_start", "sentence_end"])
        self.assertEqual(
            [message.get("event") for message in result.json_messages],
            ["open_success", "sentence_start", "sentence_end"],
        )
        self.assertEqual(result.audio_chunk_count, 1)
        self.assertFalse(result.finish_received)
        self.assertEqual(result.close_code, 1000)
        self.assertEqual(result.close_reason, "OK")
        self.assertEqual(
            [json.loads(message)["event"] for message in websocket.sent_messages],
            ["text", "finish"],
        )

    async def test_synthesize_marks_success_on_finish_event(self):
        websocket = FakeWebSocket(
            [
                json.dumps({"event": "open_success", "code": 0, "message": ""}),
                json.dumps({
                    "event": "sentence_start",
                    "sentence_start_result": {"readable_text": "测试文本"},
                }),
                b"audio-chunk",
                json.dumps({"event": "sentence_end", "code": 0, "message": ""}),
                json.dumps({"event": "finish", "code": 0, "message": ""}),
            ]
        )

        with patch.object(doubao_tts.websockets, "connect", return_value=FakeConnect(websocket)):
            client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=VALID_COOKIE))
            result = await client.synthesize("测试文本")

        self.assertTrue(result.success)
        self.assertEqual(result.audio_data, b"audio-chunk")
        self.assertEqual(
            result.event_order,
            ["open_success", "sentence_start", "sentence_end", "finish"],
        )
        self.assertEqual(
            [message.get("event") for message in result.json_messages],
            ["open_success", "sentence_start", "sentence_end", "finish"],
        )
        self.assertEqual(result.audio_chunk_count, 1)
        self.assertTrue(result.finish_received)
        self.assertIsNone(result.close_code)

    async def test_synthesize_rejects_timeout_as_incomplete_audio(self):
        websocket = FakeWebSocket(
            [
                json.dumps({"event": "open_success", "code": 0, "message": ""}),
                json.dumps({
                    "event": "sentence_start",
                    "sentence_start_result": {"readable_text": "测试文本"},
                }),
                b"audio-chunk",
                asyncio.TimeoutError(),
            ]
        )

        with patch.object(doubao_tts.websockets, "connect", return_value=FakeConnect(websocket)):
            client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=VALID_COOKIE))
            result = await client.synthesize("测试文本")

        self.assertFalse(result.success)
        self.assertIn("接收超时", result.error)
        self.assertEqual(result.event_order, ["open_success", "sentence_start"])
        self.assertEqual(result.audio_chunk_count, 1)

    async def test_synthesize_preserves_error_response_without_event(self):
        websocket = FakeWebSocket(
            [
                json.dumps({"code": 710022002, "message": "block"}),
            ]
        )

        with patch.object(doubao_tts.websockets, "connect", return_value=FakeConnect(websocket)):
            client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=VALID_COOKIE))
            result = await client.synthesize("测试文本")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "block")
        self.assertEqual(result.error_code, 710022002)
        self.assertEqual(result.event_order, [])
        self.assertEqual(result.json_messages, [{"code": 710022002, "message": "block"}])
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.retry_delays, [])

    async def test_synthesize_retries_block_only_when_explicitly_enabled(self):
        blocked_websocket = FakeWebSocket(
            [
                json.dumps({"code": 710022002, "message": "block"}),
            ]
        )
        success_websocket = FakeWebSocket(
            [
                json.dumps({"event": "open_success", "code": 0, "message": ""}),
                json.dumps({
                    "event": "sentence_start",
                    "sentence_start_result": {"readable_text": "测试文本"},
                }),
                b"audio-chunk",
                json.dumps({"event": "sentence_end", "code": 0, "message": ""}),
                json.dumps({"event": "finish", "code": 0, "message": ""}),
            ]
        )
        sleep_mock = AsyncMock()

        with patch.object(
            doubao_tts.websockets,
            "connect",
            side_effect=[FakeConnect(blocked_websocket), FakeConnect(success_websocket)],
        ) as connect_mock, patch.object(doubao_tts.asyncio, "sleep", sleep_mock):
            client = doubao_tts.DoubaoTTS(
                doubao_tts.TTSConfig(
                    cookie=VALID_COOKIE,
                    retry_on_block=True,
                    retry_max_retries=1,
                    retry_backoff_seconds=1.5,
                    retry_backoff_multiplier=2.0,
                )
            )
            result = await client.synthesize("测试文本")

        self.assertTrue(result.success)
        self.assertEqual(connect_mock.call_count, 2)
        sleep_mock.assert_awaited_once_with(1.5)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.retry_delays, [1.5])
        self.assertTrue(result.finish_received)
        self.assertEqual(result.audio_data, b"audio-chunk")

    async def test_synthesize_applies_optional_jitter_to_retry_delay(self):
        blocked_websocket = FakeWebSocket(
            [
                json.dumps({"code": 710022002, "message": "block"}),
            ]
        )
        success_websocket = FakeWebSocket(
            [
                json.dumps({"event": "open_success", "code": 0, "message": ""}),
                json.dumps({
                    "event": "sentence_start",
                    "sentence_start_result": {"readable_text": "测试文本"},
                }),
                b"audio-chunk",
                json.dumps({"event": "sentence_end", "code": 0, "message": ""}),
                json.dumps({"event": "finish", "code": 0, "message": ""}),
            ]
        )
        sleep_mock = AsyncMock()

        with patch.object(
            doubao_tts.websockets,
            "connect",
            side_effect=[FakeConnect(blocked_websocket), FakeConnect(success_websocket)],
        ), patch.object(doubao_tts.asyncio, "sleep", sleep_mock), patch.object(
            doubao_tts.random,
            "uniform",
            return_value=1.25,
        ) as uniform_mock:
            client = doubao_tts.DoubaoTTS(
                doubao_tts.TTSConfig(
                    cookie=VALID_COOKIE,
                    retry_on_block=True,
                    retry_max_retries=1,
                    retry_backoff_seconds=2.0,
                    retry_backoff_multiplier=2.0,
                    retry_backoff_jitter_ratio=0.25,
                )
            )
            result = await client.synthesize("测试文本")

        uniform_mock.assert_called_once_with(0.75, 1.25)
        sleep_mock.assert_awaited_once_with(2.5)
        self.assertTrue(result.success)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.retry_delays, [2.5])

    async def test_synthesize_rejects_invalid_retry_config(self):
        client = doubao_tts.DoubaoTTS(
            doubao_tts.TTSConfig(
                cookie=VALID_COOKIE,
                retry_on_block=True,
                retry_max_retries=1,
                retry_backoff_seconds=0,
            )
        )

        result = await client.synthesize("测试文本")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "retry_backoff_seconds 必须大于 0")

    async def test_synthesize_rejects_invalid_retry_jitter_ratio(self):
        client = doubao_tts.DoubaoTTS(
            doubao_tts.TTSConfig(
                cookie=VALID_COOKIE,
                retry_on_block=True,
                retry_max_retries=1,
                retry_backoff_seconds=1,
                retry_backoff_jitter_ratio=1.2,
            )
        )

        result = await client.synthesize("测试文本")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "retry_backoff_jitter_ratio 必须在 0 到 1 之间")

    async def test_synthesize_sync_raises_clear_error_inside_running_loop(self):
        client = doubao_tts.DoubaoTTS(doubao_tts.TTSConfig(cookie=VALID_COOKIE))
        with self.assertRaisesRegex(RuntimeError, "不能在已有事件循环中调用"):
            client.synthesize_sync("测试文本")


if __name__ == "__main__":
    unittest.main()
