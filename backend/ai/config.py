"""
AI 模块配置（Phase 6.1）。

全部从环境变量读取，带安全默认值；不在代码里出现任何密钥。
设计原则：
  - 配置只在后端存在，前端拿不到 base_url / key（AGENTS.md §10）；
  - 缺省值必须让「什么都没配」的系统给出明确的 unavailable，而不是崩溃或假装可用。
"""

import os

VALID_PROVIDERS = ("ollama", "siliconflow", "mock")


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class AIConfig:
    """一次性快照，避免每次请求都读一遍环境变量。"""

    def __init__(self):
        self.enabled = _bool("AI_ENABLED", False)
        self.provider = (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower()

        # Ollama
        self.ollama_base_url = (os.environ.get("OLLAMA_BASE_URL")
                                or "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = os.environ.get("OLLAMA_MODEL") or "qwen3:4b"
        self.timeout = _int("OLLAMA_TIMEOUT", 120)
        self.num_ctx = _int("OLLAMA_NUM_CTX", 4096)
        self.temperature = _float("OLLAMA_TEMPERATURE", 0.3)

        # 云端备用（可选）
        self.siliconflow_api_key = os.environ.get("SILICONFLOW_API_KEY") or ""
        self.siliconflow_base_url = (os.environ.get("SILICONFLOW_BASE_URL")
                                     or "https://api.siliconflow.cn/v1").rstrip("/")
        self.siliconflow_model = os.environ.get("SILICONFLOW_MODEL") or ""

        # 生成参数
        self.max_output_tokens = _int("LLM_MAX_TOKENS", 2048)
        self.min_actions = _int("STRATEGY_MIN_ACTIONS", 3)
        self.max_actions = _int("STRATEGY_MAX_ACTIONS", 5)

    def validate(self):
        """返回配置层面的问题列表（空列表 = 没问题）。不抛异常，交给健康检查展示。"""
        problems = []
        if self.provider not in VALID_PROVIDERS:
            problems.append("LLM_PROVIDER=%r 不受支持，可选：%s"
                            % (self.provider, " / ".join(VALID_PROVIDERS)))
        if self.provider == "siliconflow" and not self.siliconflow_api_key:
            problems.append("LLM_PROVIDER=siliconflow 但未设置 SILICONFLOW_API_KEY")
        if self.provider == "siliconflow" and not self.siliconflow_model:
            problems.append("LLM_PROVIDER=siliconflow 但未设置 SILICONFLOW_MODEL")
        if self.timeout <= 0:
            problems.append("OLLAMA_TIMEOUT 必须为正数")
        if self.num_ctx < 512:
            problems.append("OLLAMA_NUM_CTX 过小（建议 ≥2048）")
        if not (0.0 <= self.temperature <= 1.5):
            problems.append("OLLAMA_TEMPERATURE 超出合理范围 0–1.5")
        return problems

    def public_view(self):
        """给健康检查用的可公开字段：不含 key、不含内部路径。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": (self.ollama_model if self.provider == "ollama"
                      else self.siliconflow_model if self.provider == "siliconflow"
                      else "mock"),
            "timeout_seconds": self.timeout,
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
        }

    def __repr__(self):
        return "<AIConfig provider=%s model=%s enabled=%s>" % (
            self.provider, self.public_view()["model"], self.enabled)


def load_config():
    return AIConfig()
