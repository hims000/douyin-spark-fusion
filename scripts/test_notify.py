#!/usr/bin/env python3
"""Test notification channels for Douyin Spark Fusion."""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.notifier import send_notification


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', choices=['dingtalk','feishu','wecom','telegram','bark','email','all'], default='all')
    args = parser.parse_args()

    title = "🧪 Douyin Spark Fusion - Notification Test"
    content = f"**Test notification** from Douyin Spark Fusion\n\nChannel: {args.channel}\nTime: {__import__('datetime').datetime.now()}"

    if args.channel != 'all':
        content = f"Channel: {args.channel}"

    results = await send_notification(title, content)
    for r in results:
        print(f"  Result: {r}")
    print("Done")

if __name__ == '__main__':
    asyncio.run(main())
