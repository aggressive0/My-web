"""
LLM Provider 抽象层（Phase 6.1）。

为什么要抽象：策略生成逻辑不能和某个模型服务的 URL 绑死。
本地 Ollama 与云端 SiliconFlow 必须能被同一段业务代码调用，
换 Provider 只改环境变量，不改调用方。

错误码与方案 §11.7 对齐，抛异常时带 code，路由层直接映射成 HTTP 响应，
不把堆栈或内部地址泄露给前端。
"""

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """所有 Provider 异常的基类。code 是给前端看的稳定标识。"""

    code = "LLM_ERROR"

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail        # 只进日志，不进响应

    def to_public(self):
        return {"code": self.code, "message": self.message}


class LLMUnavailable(LLMError):
    code = "LLM_UNAVAILABLE"


class LLMTimeout(LLMError):
    code = "LLM_TIMEOUT"


class ModelNotFound(LLMError):
    code = "MODEL_NOT_FOUND"


class InvalidOutput(LLMError):
    code = "INVALID_OUTPUT"


class BaseLLMProvider(ABC):
    """统一接口。generate 返回模型原始文本（不在这里解析业务结构）。"""

    name = "base"

    def __init__(self, config):
        self.config = config
        self.last_duration_ms = None

    @abstractmethod
    def generate(self, prompt, system_prompt="", response_schema=None):
        """返回文本。失败抛 LLMError 子类。"""

    @abstractmethod
    def health(self):
        """返回结构化健康信息：{available, model, detail}。

        注意 health 不能抛异常——健康检查自己崩掉是最糟的失败方式。
        """

    def health_check(self):
        """兼容方案 §6.2 的布尔接口。"""
        try:
            return bool(self.health().get("available"))
        except Exception:
            return False
