#!/usr/bin/env python3
"""命令行入口，和库逻辑分离。"""

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional, Sequence


def get_api(api=None):
    if api is not None:
        return api

    import doubao_tts as api_module

    return api_module


def build_parser(api=None) -> argparse.ArgumentParser:
    api = get_api(api)
    parser = argparse.ArgumentParser(description="豆包 TTS 文本转语音工具")
    parser.add_argument("text", nargs="?", default="", help="要转换的文本")
    parser.add_argument("-o", "--output", default="output.aac", help="输出文件路径")
    parser.add_argument("-s", "--speaker", default="taozi", help=f"语音角色: {', '.join(api.SPEAKERS.keys())}")
    parser.add_argument("--speed", type=float, default=0, help="语速 (-1.0 ~ 1.0)")
    parser.add_argument("--pitch", type=float, default=0, help="音调 (-1.0 ~ 1.0)")
    parser.add_argument("--format", default="aac", choices=["aac", "mp3"], help="音频格式")
    parser.add_argument("--list-speakers", action="store_true", help="列出可用语音")
    parser.add_argument("--cookie", help="豆包网站 Cookie (首次使用需要)")
    parser.add_argument("--save-cookie", action="store_true", help="保存 Cookie 到配置文件")
    parser.add_argument("--retry-on-block", action="store_true", help="命中 block 时按退避策略重试，默认关闭")
    parser.add_argument("--retry-max-retries", type=int, default=0, help="block 最大重试次数，不包含首轮请求")
    parser.add_argument("--retry-backoff-seconds", type=float, default=1.0, help="block 首次退避秒数")
    parser.add_argument("--retry-backoff-multiplier", type=float, default=2.0, help="block 重试退避倍率")
    parser.add_argument("--retry-backoff-jitter-ratio", type=float, default=0.0, help="block 退避随机抖动比例，0 表示关闭")
    return parser


def print_speakers(api=None):
    api = get_api(api)
    print("\n可用语音角色:")
    print("-" * 60)
    for name, speaker_id in api.SPEAKERS.items():
        print(f"  {name:15} -> {speaker_id}")
    print("-" * 60)


def print_cookie_help(api=None):
    api = get_api(api)
    print("[WARN] 需要提供 Cookie 才能使用豆包 TTS")
    print("\n获取方法:")
    print("  1. 打开浏览器访问 https://www.doubao.com 并登录")
    print("  2. 按 F12 打开开发者工具")
    print("  3. 切换到 Network 标签页")
    print("  4. 刷新页面，点击任意请求")
    print("  5. 在 Headers 中找到 Cookie 并复制")
    print("\n使用方法:")
    print('  1. 直接传参: python doubao_tts.py "文本" --cookie "sessionid=...; sid_guard=...; uid_tt=..."')
    print('  2. 保存 JSON: python doubao_tts.py "文本" --cookie "sessionid=...; sid_guard=...; uid_tt=..." --save-cookie')
    print(f"  3. 手动配置: 在 {api.COOKIE_CONFIG_FILE.name} 中写入 JSON")


async def run_async(args, api=None) -> int:
    api = get_api(api)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if args.list_speakers:
        print_speakers(api)
        return 0

    cookie = args.cookie or api.load_cookie_from_file()

    if args.save_cookie and args.cookie:
        if not api.save_cookie_to_file(args.cookie):
            print("[ERROR] 保存 Cookie 失败")
            return 1
        print(f"[OK] Cookie 已保存到: {api.COOKIE_CONFIG_FILE}")

    if not cookie:
        print_cookie_help(api)
        return 1

    cookie, missing_fields = api.normalize_cookie(cookie)
    if missing_fields:
        print(f"[ERROR] Cookie 缺少必需字段: {', '.join(missing_fields)}")
        return 1

    if not args.text:
        build_parser(api).print_help()
        return 1

    config = api.TTSConfig(
        format=args.format,
        cookie=cookie,
        verbose=True,
        retry_on_block=args.retry_on_block,
        retry_max_retries=args.retry_max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        retry_backoff_multiplier=args.retry_backoff_multiplier,
        retry_backoff_jitter_ratio=args.retry_backoff_jitter_ratio,
    )
    tts = api.DoubaoTTS(config)
    tts.set_speaker(args.speaker)
    tts.set_speed(args.speed)
    tts.set_pitch(args.pitch)

    print(f"\n[INFO] 豆包 TTS")
    print(f"   文本: {args.text[:50]}{'...' if len(args.text) > 50 else ''}")
    print(f"   语音: {args.speaker}")
    print(f"   输出: {args.output}\n")

    result = await tts.synthesize(args.text)
    if not result.success:
        print(f"\n[ERROR] 合成失败: {result.error}")
        if result.error_code is not None:
            print(f"   错误码: {result.error_code}")
        if result.attempt_count > 1:
            print(f"   尝试次数: {result.attempt_count}")
            print(f"   退避延迟: {result.retry_delays}")
        return 1

    output_path = Path(args.output)
    output_path.write_bytes(result.audio_data)
    print(f"\n[OK] 已保存到: {output_path.absolute()}")
    print(f"   文件大小: {len(result.audio_data):,} bytes")
    if result.attempt_count > 1:
        print(f"   尝试次数: {result.attempt_count}")
        print(f"   退避延迟: {result.retry_delays}")
    return 0


def main(argv: Optional[Sequence[str]] = None, api=None) -> int:
    parser = build_parser(api)
    args = parser.parse_args(argv)
    return asyncio.run(run_async(args, api))


if __name__ == "__main__":
    raise SystemExit(main())
