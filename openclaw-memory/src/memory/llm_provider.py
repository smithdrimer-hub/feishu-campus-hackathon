"""LLM provider interface and fake provider for V1.1 demos/tests."""

from __future__ import annotations

import json
import os
from typing import Any


class LLMProvider:
    """Minimal interface for structured JSON LLM providers."""

    def generate(self, prompt: str) -> str:
        """Generate a strict JSON string from a prompt."""
        raise NotImplementedError


class FakeLLMProvider(LLMProvider):
    """Deterministic provider that returns trusted JSON for the example scenario."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        """Create a fake provider with an optional fixed payload."""
        self.payload = payload or scenario_01_payload()

    def generate(self, prompt: str) -> str:
        """Return the fixed payload as strict JSON and ignore the prompt."""
        return json.dumps(self.payload, ensure_ascii=False)


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible LLM provider for structured JSON extraction.

    支持任意兼容 OpenAI 接口的服务（OpenAI、DeepSeek、Groq、Poe 等）。
    通过三个配置项适配：api_key_env / base_url / model。

    Requires openai>=1.0.0.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> None:
        """Create an OpenAI-compatible provider.

        Args:
            api_key: API key 直接传入。优先级高于 api_key_env。
            api_key_env: 环境变量名，默认 OPENAI_API_KEY。当 api_key 为 None 时读取。
            base_url: 请求地址。None 时用 OpenAI 默认地址。
            model: 模型名。默认 gpt-4o-mini。
            temperature: 生成温度，结构化提取推荐 0.1。
            max_tokens: 最大输出 token 数。
        """
        from openai import OpenAI

        resolved_key = api_key or os.environ.get(api_key_env, "")
        if not resolved_key:
            raise ValueError(
                f"API key not provided. Set {api_key_env} environment variable "
                f"or pass api_key to OpenAIProvider()."
            )
        client_kwargs = {"api_key": resolved_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._supports_json_mode = base_url is None or any(
            provider in (base_url or "").lower()
            for provider in ("openai", "api.openai")
        )

    def generate(self, prompt: str) -> str:
        """Send prompt and return the JSON response text.

        尝试使用 response_format=json_object（OpenAI 原生支持），
        如果 base_url 指向非 OpenAI 服务，fallback 到普通 text 模式。
        对非 json_object 的后端，prompt 会附加明确要求纯 JSON 的指令。
        """
        final_prompt = prompt
        if not self._supports_json_mode:
            final_prompt = prompt.rstrip() + (
                "\n\n【重要】只返回纯 JSON，不要包含 markdown 代码块、不要加说明文字。"
                "直接以 { 开头，以 } 结尾。"
            )
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": final_prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._supports_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


def scenario_01_payload() -> dict[str, Any]:
    """Return fixed structured candidates for examples/handoff_scenario_01.jsonl."""
    return {
        "candidates": [
            {
                "project_id": "openclaw-memory-demo",
                "state_type": "owner",
                "key": "current_owner",
                "current_value": "C 负责 V1.1 可信提取模块",
                "rationale": "后续讨论明确把 V1.1 的 LLM 结构化提取交给 C。",
                "owner": "C",
                "status": "active",
                "confidence": 0.86,
                "detected_at": "2026-04-25T10:04:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_example_handoff_01",
                        "message_id": "om_example_02",
                        "excerpt": "负责人先从 B 调整为 C，C 负责 V1.1 可信提取模块。",
                        "created_at": "2026-04-25T10:04:00+08:00",
                    }
                ],
            },
            {
                "project_id": "openclaw-memory-demo",
                "state_type": "decision",
                "key": "extractor_strategy",
                "current_value": "采用 LLM 结构化提取 + schema 校验 + 规则兜底",
                "rationale": "团队确认提高识别能力，但失败时必须回退规则提取。",
                "owner": None,
                "status": "active",
                "confidence": 0.9,
                "detected_at": "2026-04-25T10:06:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_example_handoff_01",
                        "message_id": "om_example_03",
                        "excerpt": "关键决策：V1.1 采用 LLM 结构化提取 + schema 校验 + 规则兜底。",
                        "created_at": "2026-04-25T10:06:00+08:00",
                    }
                ],
            },
            {
                "project_id": "openclaw-memory-demo",
                "state_type": "deferred",
                "key": "real_write_execution",
                "current_value": "暂缓真实写入执行和 UI",
                "rationale": "V1.1 目标是可信提取，不扩展真实写入和 UI。",
                "owner": None,
                "status": "deferred",
                "confidence": 0.84,
                "detected_at": "2026-04-25T10:08:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_example_handoff_01",
                        "message_id": "om_example_04",
                        "excerpt": "暂缓事项：真实写入执行和 UI 都先不做。",
                        "created_at": "2026-04-25T10:08:00+08:00",
                    }
                ],
            },
            {
                "project_id": "openclaw-memory-demo",
                "state_type": "blocker",
                "key": "docs_create_dry_run",
                "current_value": "docs +create --dry-run 不能作为安全保护机制",
                "rationale": "该命令曾经实际创建文档，必须被安全策略拦截。",
                "owner": None,
                "status": "active",
                "confidence": 0.93,
                "detected_at": "2026-04-25T10:10:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_example_handoff_01",
                        "message_id": "om_example_05",
                        "excerpt": "阻塞/风险：docs +create --dry-run 之前实际创建过文档，不能拿它当保护机制。",
                        "created_at": "2026-04-25T10:10:00+08:00",
                    }
                ],
            },
            {
                "project_id": "openclaw-memory-demo",
                "state_type": "next_step",
                "key": "implement_v11_tests",
                "current_value": "补充合法/非法 LLM 输出和证据锚点测试",
                "rationale": "下一步要求测试合法输出、非法输出和无证据锚点拦截。",
                "owner": "C",
                "status": "active",
                "confidence": 0.82,
                "detected_at": "2026-04-25T10:12:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_example_handoff_01",
                        "message_id": "om_example_06",
                        "excerpt": "下一步：C 补上合法/非法 LLM 输出、无证据锚点不能写入的测试。",
                        "created_at": "2026-04-25T10:12:00+08:00",
                    }
                ],
            },
        ]
    }
