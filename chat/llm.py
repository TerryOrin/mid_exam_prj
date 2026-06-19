from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import tools

SYSTEM_PROMPT = """You are the AIOT Water Quality Assistant for an aquaculture website.
Help users interpret pond data, explain risk signals, and suggest practical next actions.
Be specific, concise, and grounded in the tool data when tools are used.
If measurements look risky, say why and suggest immediate checks or mitigation steps.
"""

MAX_TOOL_LOOPS = 5
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_OPENAI_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class ModelConfig:
    key: str
    label: str
    description: str
    provider: str


MODEL_CATALOG: dict[str, ModelConfig] = {
    "deepseek-v4-flash": ModelConfig(
        key="deepseek-v4-flash",
        label="DeepSeek V4 Flash",
        description="Fast DeepSeek option with reasoning enabled for quick analysis.",
        provider="deepseek",
    ),
    "gemini-2.5-flash-lite": ModelConfig(
        key="gemini-2.5-flash-lite",
        label="Gemini 2.5 Flash Lite",
        description="Lightweight Gemini option for lower-latency responses.",
        provider="gemini",
    ),
    "gemini-2.5-flash": ModelConfig(
        key="gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        description="Stronger Gemini option for more detailed explanations.",
        provider="gemini",
    ),
}


def _is_placeholder_key(value: str) -> bool:
    lowered = value.lower()
    return "your-key" in lowered or "your_api_key" in lowered or "sk-your-" in lowered


def _get_deepseek_api_key() -> str:
    return ((os.environ.get("DS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")).strip()


def get_default_model_name() -> str:
    configured_model = (os.environ.get("LLM_MODEL") or "").strip()
    if configured_model in MODEL_CATALOG:
        return configured_model

    provider = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
    if provider == "gemini":
        return "gemini-2.5-flash-lite"
    if provider == "deepseek":
        return "deepseek-v4-flash"

    deepseek_api_key = _get_deepseek_api_key()
    if deepseek_api_key and not _is_placeholder_key(deepseek_api_key):
        return "deepseek-v4-flash"

    gemini_api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini_api_key and not _is_placeholder_key(gemini_api_key):
        return "gemini-2.5-flash-lite"

    return "deepseek-v4-flash"


def get_available_models() -> list[dict[str, str]]:
    default_model = get_default_model_name()
    return [
        {
            "key": config.key,
            "label": config.label,
            "description": config.description,
            "provider": config.provider,
            "is_default": config.key == default_model,
        }
        for config in MODEL_CATALOG.values()
    ]


def resolve_model(model_name: str | None = None) -> ModelConfig:
    candidate = (model_name or "").strip() or get_default_model_name()
    config = MODEL_CATALOG.get(candidate)
    if config is None:
        raise ValueError(f"Unsupported model: {candidate}")
    return config


def _client(model: ModelConfig):
    from openai import OpenAI

    if model.provider == "gemini":
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key or _is_placeholder_key(api_key):
            raise RuntimeError("Missing GEMINI_API_KEY.")
        return OpenAI(api_key=api_key, base_url=GEMINI_OPENAI_BASE_URL)

    if model.provider == "deepseek":
        api_key = _get_deepseek_api_key()
        if not api_key or _is_placeholder_key(api_key):
            raise RuntimeError("Missing DS_API_KEY or DEEPSEEK_API_KEY.")
        return OpenAI(api_key=api_key, base_url=DEEPSEEK_OPENAI_BASE_URL)

    raise RuntimeError(f"Unsupported provider: {model.provider}")


def _message_text(msg) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _completion_request_kwargs(model: ModelConfig, messages: list[dict]) -> dict:
    kwargs = {
        "model": model.key,
        "messages": messages,
    }
    if model.provider == "deepseek":
        kwargs["reasoning_effort"] = "high"
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def _tool_completion_request_kwargs(model: ModelConfig, messages: list[dict]) -> dict:
    kwargs = _completion_request_kwargs(model, messages)
    kwargs["tools"] = tools.TOOL_SCHEMAS
    return kwargs


def direct_chat(
    user_message: str,
    *,
    system_prompt: str,
    history: list[dict] | None = None,
    model_name: str | None = None,
) -> str:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    model = resolve_model(model_name)
    client = _client(model)
    response = client.chat.completions.create(**_completion_request_kwargs(model, messages))
    text = _message_text(response.choices[0].message).strip()
    if not text:
        raise RuntimeError("The model did not return a final answer.")
    return text


def chat(user_message: str, history: list[dict] | None = None, model_name: str | None = None) -> str:
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    model = resolve_model(model_name)
    client = _client(model)

    for _ in range(MAX_TOOL_LOOPS):
        response = client.chat.completions.create(**_tool_completion_request_kwargs(model, messages))
        msg = response.choices[0].message

        if not msg.tool_calls:
            text = _message_text(msg).strip()
            if text:
                return text
            messages.append(msg.model_dump(exclude_none=True))
            messages.append(
                {
                    "role": "user",
                    "content": "Please answer directly with a concise explanation.",
                }
            )
            continue

        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = tools.dispatch(call.function.name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "The model did not return a final answer. Please try again."
