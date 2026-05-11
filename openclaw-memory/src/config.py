"""Centralized configuration loader for OpenClaw Memory Engine.

Loads config from (in priority order, highest wins):
  1. Environment variables (OPENCLAW_LLM_API_KEY, etc.)
  2. config.local.yaml  (gitignored — API keys, secrets)
  3. config.yaml        (tracked — project settings, chat IDs)
  4. Built-in defaults  (safe fallbacks for non-secret values)

Usage:
    from config import get_config
    cfg = get_config()
    provider = OpenAIProvider(
        api_key=cfg.llm.api_key,
        base_url=cfg.llm.base_url,
        model=cfg.llm.model,
    )
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _root_dir() -> Path:
    """Find the project root (parent of src/)."""
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-pro"
    provider: str = "openai"
    temperature: float = 0.1
    max_tokens: int = 2000


@dataclass(frozen=True)
class EmbeddingConfig:
    api_key: str = ""
    base_url: str = "https://api.electronhub.ai/v1"
    model: str = "text-embedding-3-small"
    provider: str = "openai"
    similarity_threshold: float = 0.35


@dataclass(frozen=True)
class AuthConfig:
    admins: list[str] = field(default_factory=list)
    verify_chat_membership_before_write: bool = False


@dataclass(frozen=True)
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    projects: list[dict[str, str]] = field(default_factory=list)
    auto_sync: dict[str, Any] = field(default_factory=dict)
    event_listener: dict[str, Any] = field(default_factory=dict)
    demo: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal: YAML loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning {} if it doesn't exist."""
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _merge_dicts(base: dict, override: dict) -> dict:
    """Shallow-then-deep merge: override keys overwrite base keys."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_config_cache: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Return the singleton Config, loading from disk on first call.

    Set reload=True to force a fresh load (useful in tests).
    """
    global _config_cache
    if _config_cache is not None and not reload:
        return _config_cache

    root = _root_dir()

    # Layer 1: built-in defaults (empty API keys)
    llm_data: dict[str, Any] = {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-pro",
        "provider": "openai",
        "temperature": 0.1,
        "max_tokens": 2000,
    }
    emb_data: dict[str, Any] = {
        "api_key": "",
        "base_url": "https://api.electronhub.ai/v1",
        "model": "text-embedding-3-small",
        "provider": "openai",
        "similarity_threshold": 0.35,
    }
    auth_data: dict[str, Any] = {"admins": [], "verify_chat_membership_before_write": False}
    top_data: dict[str, Any] = {}

    # Layer 2: config.yaml  (tracked in git — project settings)
    top_data = _merge_dicts(top_data, _load_yaml(root / "config.yaml"))

    # Layer 3: config.local.yaml  (gitignored — secrets)
    local_raw = _load_yaml(root / "config.local.yaml")
    if "llm" in local_raw:
        llm_data = _merge_dicts(llm_data, local_raw["llm"])
    if "embedding" in local_raw:
        emb_data = _merge_dicts(emb_data, local_raw["embedding"])
    if "auth" in local_raw:
        auth_data = _merge_dicts(auth_data, local_raw["auth"])
    # Merge non-secret top-level keys from local as well
    for key in ("projects", "auto_sync", "event_listener", "demo", "auth"):
        if key in local_raw and key != "auth":
            top_data[key] = local_raw[key]
    # auth from config.yaml also merges
    if "auth" in top_data:
        auth_data = _merge_dicts(auth_data, top_data["auth"])

    # Layer 4: environment variables (highest priority)
    env_api_key = os.environ.get("OPENCLAW_LLM_API_KEY")
    if env_api_key:
        llm_data["api_key"] = env_api_key
    env_base_url = os.environ.get("OPENCLAW_LLM_BASE_URL")
    if env_base_url:
        llm_data["base_url"] = env_base_url
    env_model = os.environ.get("OPENCLAW_LLM_MODEL")
    if env_model:
        llm_data["model"] = env_model

    env_emb_key = os.environ.get("OPENCLAW_EMBEDDING_API_KEY")
    if env_emb_key:
        emb_data["api_key"] = env_emb_key
    env_emb_url = os.environ.get("OPENCLAW_EMBEDDING_BASE_URL")
    if env_emb_url:
        emb_data["base_url"] = env_emb_url

    _config_cache = Config(
        llm=LLMConfig(**{k: v for k, v in llm_data.items() if k in LLMConfig.__dataclass_fields__}),
        embedding=EmbeddingConfig(**{k: v for k, v in emb_data.items() if k in EmbeddingConfig.__dataclass_fields__}),
        auth=AuthConfig(**{k: v for k, v in auth_data.items() if k in AuthConfig.__dataclass_fields__}),
        projects=top_data.get("projects", []),
        auto_sync=top_data.get("auto_sync", {}),
        event_listener=top_data.get("event_listener", {}),
        demo=top_data.get("demo", {}),
    )
    return _config_cache


def check_llm_configured() -> bool:
    """Return True if the LLM API key is set (via config.local.yaml or env var)."""
    cfg = get_config()
    return bool(cfg.llm.api_key)


def check_embedding_configured() -> bool:
    """Return True if the embedding API key is set."""
    cfg = get_config()
    return bool(cfg.embedding.api_key)


def require_llm() -> str:
    """Return the LLM API key or raise a helpful error message."""
    cfg = get_config()
    if cfg.llm.api_key:
        return cfg.llm.api_key
    raise RuntimeError(
        "LLM API key 未配置。请选择以下方式之一：\n"
        "  1. 编辑 config.local.yaml 中的 llm.api_key 字段\n"
        "  2. 设置环境变量 OPENCLAW_LLM_API_KEY\n"
        "  3. 设置环境变量 OPENAI_API_KEY"
    )


def require_embedding() -> str:
    """Return the embedding API key or raise a helpful error message."""
    cfg = get_config()
    if cfg.embedding.api_key:
        return cfg.embedding.api_key
    raise RuntimeError(
        "Embedding API key 未配置。请选择以下方式之一：\n"
        "  1. 编辑 config.local.yaml 中的 embedding.api_key 字段\n"
        "  2. 设置环境变量 OPENCLAW_EMBEDDING_API_KEY"
    )
