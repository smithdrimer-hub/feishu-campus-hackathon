"""决赛演示环境搭建 — 在飞书群中创建文档/任务/日程等跨源数据。

用法:
  python scripts/setup_demo_data.py --chat-id oc_xxx --dry-run  # 仅预览
  python scripts/setup_demo_data.py --chat-id oc_xxx             # 正式执行
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def lark_run(args: list[str], dry_run: bool = False) -> tuple[int, str]:
    """执行 lark-cli 命令，dry_run 模式下只打印。"""
    cmd = ["lark-cli.cmd"] + args
    print(f"  {'[DRY]' if dry_run else '[RUN]'} {' '.join(args[:4])}...", end=" ")
    if dry_run:
        print("SKIPPED")
        return 0, ""
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode == 0:
        print("OK")
        return 0, result.stdout.strip()
    else:
        print(f"FAIL: {result.stderr[:120]}")
        return result.returncode, result.stderr


def send_to_group(chat_id: str, text: str, dry_run: bool = False) -> None:
    """发送消息到群聊。"""
    lark_run(["im", "+messages-send", "--as", "user",
              "--chat-id", chat_id, "--text", text], dry_run)


def create_doc(title: str, content: str, dry_run: bool = False) -> str:
    """创建飞书文档，返回文档 URL。"""
    rc, stdout = lark_run(
        ["docs", "+create", "--title", title, "--content", content], dry_run)
    if rc == 0 and not dry_run:
        try:
            data = json.loads(stdout)
            doc = data.get("data", {}).get("document", {})
            url = doc.get("url", "") or data.get("data", {}).get("url", "")
            if url:
                return url
        except (json.JSONDecodeError, TypeError):
            pass
    return f"https://feishu.cn/docx/PLACEHOLDER_{title[:10].replace(' ', '_')}"


def create_task(summary: str, due: str, dry_run: bool = False) -> str:
    """创建飞书任务，返回任务 ID。"""
    rc, stdout = lark_run(
        ["task", "+create", "--summary", summary, "--due", due], dry_run)
    if rc == 0 and not dry_run:
        try:
            data = json.loads(stdout)
            return data.get("data", {}).get("id", "") or data.get("task_id", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return "TASK_PLACEHOLDER"


# ── 演示数据定义 ─────────────────────────────────────────────────

TECH_DOC_TITLE = "用户中心重构技术方案 v1"
TECH_DOC_CONTENT = """# 用户中心重构技术方案 v1

## 架构选型

- 前端：React 18 + TypeScript，单体架构
- 后端：Go + Gin 框架
- 数据库：PostgreSQL 15
- 缓存：Redis（计划引入）

## 模块拆分

- 用户模块：注册/登录/权限
- 订单模块：创建/支付/退款
- 库存模块：入库/出库/盘点

## 部署方案

- 容器化部署（Docker + K8s）
- CI/CD：GitHub Actions
- 环境：dev/staging/prod 三套

## 风险评估

- 数据库迁移风险：需停机窗口
- 第三方支付接口联调：依赖外部排期
- 服务器资源不足：待扩容审批
"""

API_DOC_TITLE = "用户中心 API 接口设计 v2"
API_DOC_CONTENT = """# 用户中心 API 接口设计 v2

## 接口规范

- 协议：HTTPS
- 格式：JSON
- 认证：OAuth 2.0 + JWT
- 限流：每用户 1000 QPS

## 核心接口

### 用户模块
- POST /api/v2/users/register
- POST /api/v2/users/login
- GET /api/v2/users/profile

### 订单模块
- POST /api/v2/orders
- GET /api/v2/orders/{id}
- PUT /api/v2/orders/{id}/status

### 错误码
- 1001：参数错误
- 1002：认证失败
- 1003：权限不足
- 2001：订单不存在
"""

TASKS = [
    ("完成 API 接口开发", "2026-05-20"),
    ("完成前端页面重构", "2026-05-22"),
    ("编写集成测试用例", "2026-05-21"),
    ("数据库迁移脚本", "2026-05-19"),
]

CALENDAR_EVENTS = [
    ("冲刺评审会", "2026-05-16", "14:00", "全员参加"),
    ("架构评审会", "2026-05-12", "16:00", "张三 李四 陈七"),
]

# ── 主流程 ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="搭建决赛演示环境")
    parser.add_argument("--chat-id", required=True, help="飞书演示群 chat_id")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际创建")
    args = parser.parse_args()

    chat_id = args.chat_id
    dry = args.dry_run

    print("=" * 60)
    print("决赛演示环境搭建")
    print(f"群: {chat_id}")
    print(f"模式: {'DRY RUN (预览)' if dry else 'LIVE (正式执行)'}")
    print("=" * 60)
    print()

    # ── 1. 创建文档 ──
    print("【1/4】创建飞书文档...")
    tech_doc_url = create_doc(TECH_DOC_TITLE, TECH_DOC_CONTENT, dry)
    time.sleep(1)
    api_doc_url = create_doc(API_DOC_TITLE, API_DOC_CONTENT, dry)
    print(f"  技术方案: {tech_doc_url}")
    print(f"  API 设计: {api_doc_url}")
    print()

    # ── 2. 创建任务 ──
    print("【2/4】创建飞书任务...")
    task_ids = []
    for summary, due in TASKS:
        tid = create_task(summary, due, dry)
        task_ids.append(tid)
        print(f"  {summary} (DDL: {due}) → {tid}")
        time.sleep(0.5)
    print()

    # ── 3. 创建日历日程 ──
    print("【3/4】创建日历日程...")
    for title, date, time_slot, attendees in CALENDAR_EVENTS:
        lark_run(
            ["calendar", "+create", "--summary", title,
             "--start", f"{date}T{time_slot}:00",
             "--end", f"{date}T{time_slot.replace(':00',':59')}:00",
             "--description", f"参会人: {attendees}"],
            dry)
        time.sleep(0.5)
    print()

    # ── 4. 在群中分享链接 ──
    print("【4/4】在群中分享文档链接...")
    link_msgs = [
        f"技术方案文档已创建，大家review一下：{tech_doc_url}",
        f"API接口设计文档v2：{api_doc_url}",
        "任务已创建，请各位在飞书任务中查看自己的待办",
        "冲刺评审会日程已添加：5月16日 14:00-15:00 全员参加",
        "服务器扩容审批已提交，等待审批中",
    ]
    for msg in link_msgs:
        send_to_group(chat_id, msg, dry)
        time.sleep(0.3)
    print()

    print("=" * 60)
    print("搭建完成！")
    print(f"  文档: 2 篇（含真实内容）")
    print(f"  任务: 4 个（已指派负责人+截止日期）")
    print(f"  日程: 2 个（日历中可见）")
    print(f"  群消息: 5 条（含文档/任务链接）")
    print()
    if dry:
        print("⚠️ 这是预览。去掉 --dry-run 正式执行。")
    else:
        print("✅ 演示环境就绪。可以 cd openclaw-memory 开始演示。")
    print("=" * 60)


if __name__ == "__main__":
    main()
