#!/usr/bin/env python3
"""
豆包 TTS (Text-to-Speech) 逆向工程客户端
Doubao TTS Reverse Engineering Client

用法:
    python doubao_tts.py "你好，世界" -o output.aac
    python doubao_tts.py "Hello World" --speaker zh_male_rap_mars_bigtts -o rap.aac
"""

import asyncio
import json
import uuid
import random
from urllib.parse import urlencode
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field

try:
    import websockets
    from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
except ImportError:
    print("请安装 websockets: pip install websockets")
    exit(1)


@dataclass
class TTSConfig:
    """TTS 配置"""
    # 语音角色 ID
    speaker: str = "zh_female_taozi_conversation_v4_wvae_bigtts"
    # 音频格式: aac, mp3, wav
    format: str = "aac"
    # 语速: -1.0 ~ 1.0, 0 为正常
    speech_rate: float = 0
    # 音调: -1.0 ~ 1.0, 0 为正常
    pitch: float = 0
    # 语言
    language: str = "zh"
    # 应用 ID
    aid: int = 497858
    # 版本号
    version_code: int = 20800
    pc_version: str = "2.46.3"
    # Cookie (从浏览器获取)
    cookie: str = ""
    # 是否输出运行日志
    verbose: bool = False
    # 是否在命中 block 时执行退避重试，默认关闭
    retry_on_block: bool = False
    # block 最大重试次数，不包含首轮请求
    retry_max_retries: int = 0
    # 首次退避秒数
    retry_backoff_seconds: float = 1.0
    # 每次重试的退避倍率，建议 >= 1
    retry_backoff_multiplier: float = 2.0
    # 可选随机抖动比例，0 表示关闭，0.2 表示在基准退避时间上下浮动 20%
    retry_backoff_jitter_ratio: float = 0.0


@dataclass
class TTSResult:
    """TTS 结果"""
    audio_data: bytes = field(default_factory=bytes)
    sentences: list = field(default_factory=list)
    event_order: list[str] = field(default_factory=list)
    json_messages: list[dict] = field(default_factory=list)
    audio_chunk_count: int = 0
    finish_received: bool = False
    close_code: Optional[int] = None
    close_reason: str = ""
    error_code: Optional[int] = None
    attempt_count: int = 1
    retry_delays: list[float] = field(default_factory=list)
    success: bool = False
    error: str = ""


# 常用语音角色
SPEAKERS = {
    # 女声
    "taozi": "zh_female_taozi_conversation_v4_wvae_bigtts",  # 桃子 - 对话
    "shuangkuai": "zh_female_shuangkuai_emo_v3_wvae_bigtts",  # 爽快
    "tianmei": "zh_female_tianmei_conversation_v4_wvae_bigtts",  # 甜美
    "qingche": "zh_female_qingche_moon_bigtts",  # 清澈
    
    # 男声
    "yangguang": "zh_male_yangguang_conversation_v4_wvae_bigtts",  # 阳光
    "chenwen": "zh_male_chenwen_moon_bigtts",  # 沉稳
    "rap": "zh_male_rap_mars_bigtts",  # 说唱
    
    # 多语言
    "en_female": "en_female_sarah_conversation_bigtts",
    "en_male": "en_male_adam_conversation_bigtts",
}

REQUIRED_COOKIE_FIELDS = ("sessionid", "sid_guard", "uid_tt")
COOKIE_CONFIG_FILE = Path(__file__).parent / ".cookie.json"
LEGACY_COOKIE_FILE = Path(__file__).parent / ".cookie"
BLOCK_ERROR_CODE = 710022002


def is_missing_cookie_value(value: object) -> bool:
    """判断 Cookie 字段是否缺失或仍是占位符"""
    return not str(value).strip() or str(value).strip() == "..."


def build_cookie_string(cookie_items: dict[str, str]) -> str:
    """按固定顺序构造 Cookie 字符串"""
    return "; ".join(
        f"{field}={cookie_items[field]}"
        for field in REQUIRED_COOKIE_FIELDS
        if field in cookie_items
    )


class DoubaoTTS:
    """豆包 TTS 客户端"""
    
    WS_URL = "wss://ws-samantha.doubao.com/samantha/audio/tts"
    
    def __init__(self, config: Optional[TTSConfig] = None):
        self.config = config or TTSConfig()
        if not self.config.cookie:
            self.config.cookie = load_cookie_from_file()
        self._device_id = self._generate_device_id()
        self._web_id = self._generate_web_id()
    
    def _generate_device_id(self) -> str:
        """生成设备 ID"""
        return str(random.randint(7400000000000000000, 7499999999999999999))
    
    def _generate_web_id(self) -> str:
        """生成 Web ID"""
        return str(random.randint(7400000000000000000, 7499999999999999999))
    
    def _build_ws_url(self) -> str:
        """构建 WebSocket URL"""
        params = {
            "speaker": self.config.speaker,
            "format": self.config.format,
            "speech_rate": int(self.config.speech_rate * 100) if self.config.speech_rate != 0 else 0,
            "pitch": int(self.config.pitch * 100) if self.config.pitch != 0 else 0,
            "version_code": self.config.version_code,
            "language": self.config.language,
            "device_platform": "web",
            "aid": self.config.aid,
            "real_aid": self.config.aid,
            "pkg_type": "release_version",
            "device_id": self._device_id,
            "pc_version": self.config.pc_version,
            "web_id": self._web_id,
            "tea_uuid": self._web_id,
            "region": "",
            "sys_region": "",
            "samantha_web": 1,
            "use-olympus-account": 1,
            "web_tab_id": str(uuid.uuid4()),
        }

        query = urlencode(params)
        return f"{self.WS_URL}?{query}"

    def build_ws_url(self) -> str:
        """公开的 WebSocket URL 构建入口，便于调试和协议观察脚本复用"""
        return self._build_ws_url()

    def build_headers(self) -> dict[str, str]:
        """构建请求头，失败时抛出 ValueError，避免库层直接退出"""
        normalized_cookie, missing_fields = normalize_cookie(self.config.cookie)
        if missing_fields:
            raise ValueError(f"Cookie 缺少必需字段: {', '.join(missing_fields)}")

        self.config.cookie = normalized_cookie
        return {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Origin": "https://www.doubao.com",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Cookie": self.config.cookie,
        }

    def _log(self, level: str, message: str):
        """按需输出日志，避免库层污染标准输出"""
        if self.config.verbose:
            print(f"[{level}] {message}")

    def _validate_retry_config(self):
        """校验显式启用的重试配置，避免配置错误导致无脑重试"""
        if not self.config.retry_on_block:
            return

        if self.config.retry_max_retries < 0:
            raise ValueError("retry_max_retries 不能小于 0")

        if self.config.retry_max_retries == 0:
            return

        if self.config.retry_backoff_seconds <= 0:
            raise ValueError("retry_backoff_seconds 必须大于 0")

        if self.config.retry_backoff_multiplier < 1:
            raise ValueError("retry_backoff_multiplier 必须大于等于 1")

        if not 0 <= self.config.retry_backoff_jitter_ratio <= 1:
            raise ValueError("retry_backoff_jitter_ratio 必须在 0 到 1 之间")

    def _is_block_result(self, result: TTSResult) -> bool:
        """判断结果是否命中服务端 block 响应"""
        return result.error_code == BLOCK_ERROR_CODE or result.error.strip().lower() == "block"

    def _compute_retry_delay(self, base_delay: float) -> float:
        """按可选随机抖动计算本次实际退避时间"""
        jitter_ratio = self.config.retry_backoff_jitter_ratio
        if jitter_ratio <= 0:
            return base_delay

        factor = random.uniform(1 - jitter_ratio, 1 + jitter_ratio)
        return base_delay * factor

    async def _synthesize_once(
        self, 
        text: str,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_sentence_start: Optional[Callable[[str], None]] = None,
        on_sentence_end: Optional[Callable[[], None]] = None,
    ) -> TTSResult:
        """
        合成语音
        
        Args:
            text: 要转换的文本
            on_audio_chunk: 音频块回调 (用于流式播放)
            on_sentence_start: 句子开始回调
            on_sentence_end: 句子结束回调
            
        Returns:
            TTSResult: 合成结果
        """
        result = TTSResult()
        if not text.strip():
            result.error = "文本不能为空"
            self._log("ERROR", result.error)
            return result

        try:
            headers = self.build_headers()
        except ValueError as e:
            result.error = str(e)
            self._log("ERROR", result.error)
            return result

        audio_chunks: list[bytes] = []
        sentence_start_count = 0
        sentence_end_count = 0
        received_open_success = False
        received_finish_event = False
        connection_closed_normally = False

        ws_url = self.build_ws_url()

        try:
            async with websockets.connect(
                ws_url,
                additional_headers=headers,
            ) as ws:
                # 发送文本
                await ws.send(json.dumps({
                    "event": "text",
                    "text": text
                }))
                
                # 发送结束信号
                await ws.send(json.dumps({
                    "event": "finish"
                }))
                
                # 接收响应
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        
                        if isinstance(message, bytes):
                            # 音频数据
                            audio_chunks.append(message)
                            result.audio_chunk_count += 1
                            if on_audio_chunk:
                                on_audio_chunk(message)
                        else:
                            # JSON 消息
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                result.error = "收到无法解析的 JSON 响应"
                                self._log("ERROR", result.error)
                                break
                            result.json_messages.append(data)
                            event = data.get("event", "")
                            if event:
                                result.event_order.append(event)
                            
                            if event == "open_success":
                                received_open_success = True
                                self._log("INFO", "连接成功")
                                
                            elif event == "sentence_start":
                                readable_text = data.get("sentence_start_result", {}).get("readable_text", "")
                                sentence_start_count += 1
                                result.sentences.append(readable_text)
                                if on_sentence_start:
                                    on_sentence_start(readable_text)
                                self._log("INFO", f"句子开始: {readable_text[:50]}...")
                                
                            elif event == "sentence_end":
                                sentence_end_count += 1
                                if on_sentence_end:
                                    on_sentence_end()

                            elif event == "finish":
                                received_finish_event = True
                                result.finish_received = True
                                self._log("INFO", "收到 finish 事件，服务端已完成输出")
                                break
                                    
                            elif event == "error":
                                result.error_code = data.get("code")
                                result.error = data.get("message", "Unknown error")
                                self._log("ERROR", result.error)
                                break
                                
                            elif data.get("code", 0) != 0:
                                result.error_code = data.get("code")
                                result.error = data.get("message", "Unknown error")
                                self._log("ERROR", f"Code: {data.get('code')}, {result.error}")
                                break
                                
                    except asyncio.TimeoutError:
                        result.error = "接收超时，音频可能不完整"
                        self._log("ERROR", result.error)
                        break
                    except ConnectionClosedOK as e:
                        close_info = getattr(e, "rcvd", None) or getattr(e, "sent", None)
                        connection_closed_normally = True
                        # websockets 会在关闭帧里携带真实 close code / reason
                        # 如果取不到，再退回标准正常关闭语义
                        result.close_code = getattr(close_info, "code", 1000)
                        result.close_reason = getattr(close_info, "reason", "") or "OK"
                        self._log("INFO", "连接关闭，合成完成")
                        break
                    except ConnectionClosedError as e:
                        close_info = getattr(e, "rcvd", None) or getattr(e, "sent", None)
                        close_code = getattr(close_info, "code", "unknown")
                        close_reason = getattr(close_info, "reason", "") or "unknown"
                        result.close_code = close_code if isinstance(close_code, int) else None
                        result.close_reason = close_reason
                        result.error = f"连接异常关闭: code={close_code}, reason={close_reason}"
                        self._log("ERROR", result.error)
                        break
                        
        except Exception as e:
            error_message = str(e)
            if "HTTP 200" in error_message:
                error_message = "WebSocket 握手被 HTTP 200 拒绝，通常是 Cookie 无效、已过期，或仍在使用占位符"
            result.error = error_message
            self._log("ERROR", f"连接失败: {error_message}")
            return result
        
        # 合并音频数据
        result.audio_data = b"".join(audio_chunks)

        if not result.error:
            if not received_open_success:
                result.error = "未收到 open_success 事件，连接状态异常"
            elif not result.audio_data:
                result.error = "未收到音频数据"
            elif sentence_end_count > sentence_start_count:
                result.error = (
                    f"句子事件计数异常: sentence_start={sentence_start_count}, "
                    f"sentence_end={sentence_end_count}"
                )
            elif sentence_start_count > sentence_end_count:
                result.error = (
                    f"音频流提前结束: sentence_start={sentence_start_count}, "
                    f"sentence_end={sentence_end_count}"
                )
            elif not received_finish_event and not connection_closed_normally:
                result.error = "连接未正常关闭，音频可能不完整"
            else:
                result.success = True

        if result.success:
            self._log("INFO", f"合成完成, 音频大小: {len(result.audio_data)} bytes")
            self._log(
                "INFO",
                f"事件序列: {result.event_order}, 音频块数: {result.audio_chunk_count}, "
                f"finish={result.finish_received}, close_code={result.close_code}",
            )
        return result

    async def synthesize(
        self,
        text: str,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_sentence_start: Optional[Callable[[str], None]] = None,
        on_sentence_end: Optional[Callable[[], None]] = None,
    ) -> TTSResult:
        """合成语音，必要时按显式配置对 block 执行退避重试"""
        result = TTSResult()

        try:
            self._validate_retry_config()
        except ValueError as e:
            result.error = str(e)
            self._log("ERROR", result.error)
            return result

        max_retries = self.config.retry_max_retries if self.config.retry_on_block else 0
        next_delay = self.config.retry_backoff_seconds
        multiplier = self.config.retry_backoff_multiplier
        retry_delays: list[float] = []
        attempt_count = 0

        while True:
            attempt_count += 1
            if attempt_count > 1:
                self._log("INFO", f"开始第 {attempt_count} 次尝试")

            result = await self._synthesize_once(
                text,
                on_audio_chunk=on_audio_chunk,
                on_sentence_start=on_sentence_start,
                on_sentence_end=on_sentence_end,
            )
            result.attempt_count = attempt_count
            result.retry_delays = retry_delays.copy()

            should_retry = (
                self.config.retry_on_block
                and self._is_block_result(result)
                and (attempt_count - 1) < max_retries
            )
            if not should_retry:
                return result

            actual_delay = self._compute_retry_delay(next_delay)
            retry_delays.append(actual_delay)
            self._log(
                "WARN",
                (
                    f"命中 block (code={result.error_code or BLOCK_ERROR_CODE}), "
                    f"基准退避 {next_delay:.2f}s，实际等待 {actual_delay:.2f}s，"
                    f"第 {attempt_count + 1} 次尝试"
                ),
            )
            await asyncio.sleep(actual_delay)
            next_delay *= multiplier
    
    def synthesize_sync(self, text: str, **kwargs) -> TTSResult:
        """同步版本的合成方法"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.synthesize(text, **kwargs))

        raise RuntimeError("synthesize_sync 不能在已有事件循环中调用，请改用 await synthesize(...)")
    
    def set_speaker(self, speaker: str):
        """设置语音角色"""
        # 如果是简称，转换为完整 ID
        self.config.speaker = SPEAKERS.get(speaker, speaker)
        return self
    
    def set_speed(self, speed: float):
        """设置语速 (-1.0 ~ 1.0)"""
        self.config.speech_rate = max(-1.0, min(1.0, speed))
        return self
    
    def set_pitch(self, pitch: float):
        """设置音调 (-1.0 ~ 1.0)"""
        self.config.pitch = max(-1.0, min(1.0, pitch))
        return self


def load_cookie_from_file() -> str:
    """从配置文件加载 cookie，优先 JSON 配置，兼容旧 .cookie 文件"""
    if COOKIE_CONFIG_FILE.exists():
        try:
            config = json.loads(COOKIE_CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON 配置解析失败: {e}")
            return ""

        cookie_config = config.get("cookie", config)
        if not isinstance(cookie_config, dict):
            print(f"[ERROR] JSON 配置格式错误: {COOKIE_CONFIG_FILE} 中的 cookie 必须是对象")
            return ""

        missing_fields = [
            field for field in REQUIRED_COOKIE_FIELDS
            if is_missing_cookie_value(cookie_config.get(field, ""))
        ]
        if missing_fields:
            print(f"[ERROR] JSON 配置缺少必需字段: {', '.join(missing_fields)}")
            return ""

        normalized_cookie = {
            field: str(cookie_config[field]).strip()
            for field in REQUIRED_COOKIE_FIELDS
        }
        return build_cookie_string(normalized_cookie)

    if LEGACY_COOKIE_FILE.exists():
        return LEGACY_COOKIE_FILE.read_text(encoding="utf-8").strip()
    return ""


def parse_cookie_string(cookie: str) -> dict[str, str]:
    """解析 Cookie 字符串"""
    cookie_items: dict[str, str] = {}
    for item in cookie.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookie_items[key.strip()] = value.strip()
    return cookie_items


def normalize_cookie(cookie: str) -> tuple[str, list[str]]:
    """标准化 Cookie 并返回缺失字段"""
    cookie_items = parse_cookie_string(cookie)
    missing_fields = [
        field for field in REQUIRED_COOKIE_FIELDS
        if is_missing_cookie_value(cookie_items.get(field))
    ]
    normalized = build_cookie_string({
        field: cookie_items[field]
        for field in REQUIRED_COOKIE_FIELDS
        if field in cookie_items and not is_missing_cookie_value(cookie_items[field])
    })
    return normalized, missing_fields


def save_cookie_to_file(cookie: str) -> bool:
    """保存 cookie 到 JSON 配置文件"""
    normalized_cookie, missing_fields = normalize_cookie(cookie)
    if missing_fields:
        print(f"[ERROR] Cookie 缺少必需字段: {', '.join(missing_fields)}")
        return False

    cookie_items = parse_cookie_string(normalized_cookie)
    payload = {
        "cookie": {
            field: cookie_items[field]
            for field in REQUIRED_COOKIE_FIELDS
        }
    }
    COOKIE_CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] Cookie 已保存到: {COOKIE_CONFIG_FILE}")
    return True


__all__ = [
    "BLOCK_ERROR_CODE",
    "COOKIE_CONFIG_FILE",
    "LEGACY_COOKIE_FILE",
    "REQUIRED_COOKIE_FIELDS",
    "SPEAKERS",
    "DoubaoTTS",
    "TTSConfig",
    "TTSResult",
    "build_cookie_string",
    "is_missing_cookie_value",
    "load_cookie_from_file",
    "normalize_cookie",
    "parse_cookie_string",
    "save_cookie_to_file",
]


if __name__ == "__main__":
    import sys
    from doubao_tts_cli import main as cli_main

    raise SystemExit(cli_main(api=sys.modules[__name__]))
