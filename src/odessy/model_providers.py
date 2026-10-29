"""Stdlib-only cloud provider metadata shared by runtime and setup bootstrap."""
from dataclasses import dataclass
from typing import Literal

Protocol = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class CloudProvider:
    name: str
    protocol: Protocol
    base_url: str
    api_key_env: str
    default_model: str


#: Built-in providers. Every field can be overridden through ``ODESSY_CLOUD_*``.
CLOUD_PROVIDERS: dict[str, CloudProvider] = {
    "claude": CloudProvider(
        name="claude",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-opus-5-5",
    ),
    "openai": CloudProvider(
        name="openai",
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-5",
    ),
    "deepseek": CloudProvider(
        name="deepseek",
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-flash",
    ),
    "qwen": CloudProvider(
        name="qwen",
        protocol="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        default_model="qwen-plus",
    ),
    "kimi": CloudProvider(
        name="kimi",
        protocol="openai",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        default_model="kimi-k2-turbo-preview",
    ),
    # Any other OpenAI-compatible endpoint (OpenRouter, Zhipu, a local vLLM, ...).
    # ODESSY_CLOUD_BASE_URL and ODESSY_CLOUD_MODEL are required.
    "custom": CloudProvider(
        name="custom",
        protocol="openai",
        base_url="",
        api_key_env="ODESSY_CLOUD_API_KEY",
        default_model="",
    ),
}

