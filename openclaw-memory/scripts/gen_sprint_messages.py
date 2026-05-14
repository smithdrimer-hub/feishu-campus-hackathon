"""生成冲刺场景消息的 lark-cli send 命令。

用法:
  python scripts/gen_sprint_messages.py --chat-id oc_NEW_GROUP_ID --as user
  python scripts/gen_sprint_messages.py --chat-id oc_NEW_GROUP_ID --dry-run  # 仅输出不执行

输出 134 条 lark-cli send 命令，按时间线发送到飞书群。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# 从测试文件中加载消息数据
from tests.test_e2e_real_scenario import SPRINT_EVENTS


def main():
    parser = argparse.ArgumentParser(description="发送冲刺场景消息到飞书群")
    parser.add_argument("--chat-id", required=True, help="飞书群 chat_id")
    parser.add_argument("--as", dest="identity", default="user", help="user 或 bot")
    parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不执行")
    parser.add_argument("--delay", type=float, default=0.5, help="发送间隔(秒)")
    args = parser.parse_args()

    total = len(SPRINT_EVENTS)
    print(f"准备发送 {total} 条消息到 {args.chat_id} (--as {args.identity})")
    if args.dry_run:
        print("(DRY RUN — 仅打印，不执行)")
    print()

    for i, ev in enumerate(SPRINT_EVENTS):
        sender_name = (ev.get("sender", {}) or {}).get("name", "")
        text = ev["text"]
        # 加上发送人前缀（如 "张三：xxx"），演示群所有消息由同一用户发送
        if sender_name and not text.startswith(sender_name + "："):
            text = sender_name + "：" + text
        msg_type = ev.get("msg_type", "text")

        # 构建 lark-cli 命令
        if msg_type == "text":
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]
        elif msg_type == "image":
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]
        elif msg_type == "file":
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]
        elif msg_type == "post":
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]
        elif msg_type == "sticker":
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]
        else:
            cmd = [
                "lark-cli.cmd", "im", "+messages-send",
                "--as", args.identity,
                "--chat-id", args.chat_id,
                "--text", text,
            ]

        if args.dry_run:
            # 简洁输出
            sender = (ev.get("sender", {}) or {}).get("name", "")
            day = ev.get("created_at", "")[:10]
            preview = text[:60].replace("\n", " ")
            print(f"[{i+1:3d}/{total}] [{day}] {sender}: {preview}")
        else:
            safe_preview = text[:50].encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            print(f"[{i+1:3d}/{total}] {safe_preview}...", end=" ", flush=True)
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            if result.returncode == 0:
                print("OK")
            else:
                print(f"FAIL: {result.stderr[:100]}")
                # 不中断，继续发送

        time.sleep(args.delay)

    print()
    print(f"Done. {total} messages sent to {args.chat_id}")


if __name__ == "__main__":
    main()
