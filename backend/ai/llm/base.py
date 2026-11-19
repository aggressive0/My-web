"""
LLM Provider 抽象层（Phase 6.1）。

为什么要抽象：策略生成逻辑不能和某个模型服务的 URL 绑死。
本地 Ollama 与云端 SiliconFlow 必须能被同一段业务代码调用，
换 Provider 只改环境变量，不改调用方。

错误码与方案 §11.7 对齐，抛异常时带 code，路由层直接映射成 HTTP 响应，
不把堆栈或内部地址泄露给前端。
"""

import socket
from abc import ABC, abstractmethod


def failure_reason(exc):
    """把异常压成一句可公开的原因：不含地址、端口、系统错误码。

    为什么要这一步：provider.health() 的 detail 会原样进入 /api/ai/health，
    LLMError.message 会原样进入错误响应（AGENTS.md §8.1：不向前端返回内部地址）。
    原始信息不丢弃——放 LLMError.detail，只进服务端日志。
    """
    if isinstance(exc, ValueError):              # json 解析失败
        return "服务返回的不是合法 JSON"
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ConnectionRefusedError):
        return "连接被拒绝，服务可能未启动"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "连接超时"
    if isinstance(reason, socket.gaierror):
        return "主机名解析失败"
    return "网络错误（%s）" % type(reason).__name__


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


class LLMTruncated(LLMError):
    """输出被 num_predict 截断，且截断后的文本无法解析。

    注意判据是「截断 **且** 解析失败」，不是只看 `done_reason=length`：
    实测 `num_predict=16` 时 done_reason=length 但 JSON 仍然完整合法，
    按字面拒绝会误杀合法结果。
    """
    code = "LLM_TRUNCATED"


class BaseLLMProvider(ABC):
    """统一接口。generate 返回模型原始文本（不在这里解析业务结构）。"""

    name = "base"

    def __init__(self, config):
        self.config = config
        self.last_duration_ms = None
        # 最近一次调用的性能指标（T7 的基础字段）。空字典表示"没调用过"，
        # 而不是"调用过但没数据"——调用方要能区分这两者。
        self.last_metrics = {}

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
