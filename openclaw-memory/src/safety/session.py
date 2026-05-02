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
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    """

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)
        self._profiles_path = self._data_dir / "user_profiles.json"
        self._current: UserProfile = UserProfile()

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
        return bool(self._current.open_id)

    def refresh(self) -> UserProfile:
        """从 lark-cli doctor 刷新当前用户信息。"""
        try:
            result = subprocess.run(
                ["lark-cli.cmd", "doctor"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False, timeout=15,
            )
            if result.returncode != 0:
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

            if self._current.open_id:
                self._save_profile()
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
            pass
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
