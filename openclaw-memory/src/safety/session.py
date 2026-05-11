"""V1.12: 用户会话管理 — 基于 lark-cli 认证的多用户支持。

飞书 CLI 已通过 `lark-cli auth login` 完成 OAuth 认证。
本模块读取 lark-cli 的 token/config 信息，管理多用户会话。

用法:
    session = UserSession()
    session.refresh()                    # 从 lark-cli 读取当前用户
    print(session.current_user_name)     # "沈哲熙"
    print(session.current_open_id)       # "ou_487c..."
    session.list_users()                 # 列出已登录用户
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("session")


@dataclass
class UserProfile:
    """单个飞书用户的身份信息。"""

    open_id: str = ""
    name: str = ""
    tenant_key: str = ""
    app_id: str = ""
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_id": self.open_id,
            "name": self.name,
            "tenant_key": self.tenant_key,
            "app_id": self.app_id,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        return cls(
            open_id=str(data.get("open_id", "")),
            name=str(data.get("name", "")),
            tenant_key=str(data.get("tenant_key", "")),
            app_id=str(data.get("app_id", "")),
            is_active=bool(data.get("is_active", False)),
        )


class UserSession:
    """管理基于 lark-cli 的用户会话。

    从 lark-cli 的 doctor 命令和 config.json 中读取用户信息。
    支持多用户配置文件持久化。

    V1.19 P0-F: 增加 TTL 缓存 + 错误日志 + is_session_valid()。
    """

    DEFAULT_TTL_SECONDS = 300  # 5 分钟缓存

    def __init__(self, data_dir: str | Path = "data", ttl_seconds: int | None = None) -> None:
        self._data_dir = Path(data_dir)
        self._profiles_path = self._data_dir / "user_profiles.json"
        self._current: UserProfile = UserProfile()
        self._last_refreshed_at: float = 0.0
        self._ttl: int = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS

    # ── 公开 API ────────────────────────────────────────────────

    @property
    def current_user_name(self) -> str:
        return self._current.name

    @property
    def current_open_id(self) -> str:
        return self._current.open_id

    @property
    def current_tenant_key(self) -> str:
        return self._current.tenant_key

    def is_authenticated(self) -> bool:
        """当前是否有有效的用户会话。"""
        return bool(self._current.open_id) and self._current.is_active

    def is_session_valid(self) -> bool:
        """检查会话是否在 TTL 内且 token 有效。

        如果 TTL 已过期，不清除状态——由调用方决定是否 refresh()。
        """
        if not self.is_authenticated():
            return False
        elapsed = time.monotonic() - self._last_refreshed_at
        return elapsed < self._ttl

    def seconds_until_expiry(self) -> float:
        """距离 TTL 过期还剩多少秒。负数表示已过期。"""
        return self._ttl - (time.monotonic() - self._last_refreshed_at)

    def refresh(self, force: bool = False) -> UserProfile:
        """从 lark-cli doctor 刷新当前用户信息。

        Args:
            force: 如果为 True，跳过 TTL 缓存强制刷新。
        """
        if not force and self.is_session_valid():
            return self._current

        try:
            result = subprocess.run(
                ["lark-cli.cmd", "doctor"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False, timeout=15,
            )
            if result.returncode != 0:
                logger.warning("lark-cli doctor 返回非零: rc=%d stderr=%s",
                              result.returncode, result.stderr[:200])
                return self._current

            data = json.loads(result.stdout)
            checks = data.get("checks", [])

            for check in checks:
                name = check.get("name", "")
                msg = check.get("message", "")
                if name == "app_resolved":
                    # "app: cli_xxx (feishu)"
                    self._current.app_id = msg.split(":")[-1].strip().split(" ")[0] if ":" in msg else ""
                elif name == "token_exists":
                    # "token found for 沈哲熙 (ou_xxx)"
                    if "(" in msg:
                        self._current.open_id = msg.split("(")[-1].rstrip(")")
                        name_part = msg.split("(")[0].replace("token found for ", "").strip()
                        self._current.name = name_part
                elif name == "token_verified":
                    self._current.is_active = "valid" in msg.lower()

            self._last_refreshed_at = time.monotonic()

            if self._current.open_id:
                self._save_profile()
            else:
                logger.warning("lark-cli doctor 未返回 open_id，token 可能已过期")

        except json.JSONDecodeError:
            logger.warning("lark-cli doctor 返回了非 JSON 输出，会话可能不可用")
        except subprocess.TimeoutExpired:
            logger.warning("lark-cli doctor 超时（15s），网络或 lark-cli 可能异常")
        except OSError as e:
            logger.warning("无法执行 lark-cli.cmd: %s", e)

        return self._current

    def list_users(self) -> list[UserProfile]:
        """列出所有已登录过的用户。"""
        profiles = self._load_profiles()
        return [UserProfile.from_dict(p) for p in profiles]

    def switch_user(self, open_id: str) -> bool:
        """切换到指定用户（将标记为 active）。"""
        profiles = self._load_profiles()
        found = False
        for p in profiles:
            p["is_active"] = (p.get("open_id", "") == open_id)
            if p["is_active"]:
                self._current = UserProfile.from_dict(p)
                found = True
        if found:
            self._save_profiles_raw(profiles)
            self._last_refreshed_at = 0.0  # 切换用户后强制下次 refresh
        return found

    # ── 内部 ────────────────────────────────────────────────────

    def _load_profiles(self) -> list[dict]:
        if not self._profiles_path.exists():
            return []
        try:
            return json.loads(self._profiles_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_profile(self) -> None:
        profiles = self._load_profiles()
        current_dict = self._current.to_dict()
        # 更新或新增
        updated = False
        for i, p in enumerate(profiles):
            if p.get("open_id") == current_dict["open_id"]:
                profiles[i] = current_dict
                updated = True
                break
        if not updated:
            profiles.append(current_dict)
        # 只有当前用户标记为 active
        for p in profiles:
            p["is_active"] = (p.get("open_id") == current_dict["open_id"])
        self._save_profiles_raw(profiles)

    def _save_profiles_raw(self, profiles: list[dict]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._profiles_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
