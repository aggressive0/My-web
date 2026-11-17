"""Provider 工厂：按配置返回实例。未知 provider 直接报错，不静默降级。"""

from ..config import AIConfig
from .base import LLMError  # noqa: F401  re-export 便于调用方统一捕获
from .mock_provider import MockProvider
from .ollama_provider import OllamaProvider
from .siliconflow_provider import SiliconFlowProvider


def create_llm_provider(config=None):
    config = config or AIConfig()
    provider = (config.provider or "").strip().lower()

    if provider == "ollama":
        return OllamaProvider(config)
    if provider == "siliconflow":
        return SiliconFlowProvider(config)
    if provider == "mock":
        return MockProvider(config)

    raise ValueError("Unsupported LLM provider: %s" % provider)
