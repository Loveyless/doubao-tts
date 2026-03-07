#!/usr/bin/env python3
"""记录一次真实 WebSocket 会话的原始事件序列。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import websockets

from doubao_tts import DoubaoTTS, TTSConfig, load_cookie_from_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="观察豆包 TTS 的原始协议返回")
    parser.add_argument("text", nargs="?", default="日志观察：这是一次真实调用，用于记录服务端事件序列。", help="要观察的文本")
    parser.add_argument("-s", "--speaker", default="taozi", help="语音角色简称或完整 ID")
    parser.add_argument("--format", default="aac", choices=["aac", "mp3"], help="音频格式")
    parser.add_argument("--output-dir", default=str(ROOT_DIR / "logs"), help="日志输出目录")
    parser.add_argument("--log-name", default="session_observation.log", help="日志文件名")
    parser.add_argument("--audio-name", default="session_observation.aac", help="音频文件名")
    parser.add_argument("--cookie", help="临时指定 Cookie，默认读取本地配置")
    return parser


def summarize_json_message(data: dict) -> dict:
    summary = {
        "event": data.get("event", ""),
        "code": data.get("code"),
        "message": data.get("message"),
    }
    readable_text = data.get("sentence_start_result", {}).get("readable_text")
    if readable_text is not None:
        summary["readable_text"] = readable_text
    return summary


async def observe(args) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cookie = args.cookie or load_cookie_from_file()
    if not cookie:
        print("[ERROR] 未找到可用 Cookie，请先配置 .cookie.json 或使用 --cookie")
        return 1

    client = DoubaoTTS(TTSConfig(cookie=cookie, format=args.format))
    client.set_speaker(args.speaker)

    try:
        headers = client.build_headers()
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    ws_url = client.build_ws_url()
    log_path = output_dir / args.log_name
    audio_path = output_dir / args.audio_name

    lines = [
        f"text={args.text}",
        f"ws_url={ws_url}",
        "events:",
    ]
    audio_chunks: list[bytes] = []
    index = 0

    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            await ws.send(json.dumps({"event": "text", "text": args.text}, ensure_ascii=False))
            lines.append('send[1]=json {"event":"text"}')
            await ws.send(json.dumps({"event": "finish"}))
            lines.append('send[2]=json {"event":"finish"}')

            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=30)
                    index += 1
                    if isinstance(message, bytes):
                        audio_chunks.append(message)
                        lines.append(f"recv[{index}]=binary {len(message)} bytes")
                    else:
                        data = json.loads(message)
                        lines.append(f"recv[{index}]=json {json.dumps(summarize_json_message(data), ensure_ascii=False)}")
                except asyncio.TimeoutError:
                    lines.append("close=timeout")
                    break
                except websockets.exceptions.ConnectionClosed as exc:
                    lines.append(f"close=websocket code={exc.code} reason={exc.reason!r}")
                    break
    except Exception as exc:
        lines.append(f"error={type(exc).__name__}: {exc}")

    audio_data = b"".join(audio_chunks)
    if audio_data:
        audio_path.write_bytes(audio_data)
        lines.append(f"audio_file={audio_path.as_posix()} size={len(audio_data)}")
    else:
        lines.append("audio_file=<none>")

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] 已写入日志: {log_path}")
    if audio_data:
        print(f"[OK] 已写入音频: {audio_path} ({len(audio_data)} bytes)")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(observe(args))


if __name__ == "__main__":
    raise SystemExit(main())
