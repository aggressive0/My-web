"""
SiliconFlow Provider（可选云端，OpenAI 兼容协议）。

保留它不是为了现在用，而是为了让「Provider 抽象」不是空话：
只有当真的存在第二个实现时，接口设计才被验证过。
密钥只从环境变量读，永不写日志、永不出现在响应里。
"""

import json
import socket
import time
import urllib.error
import urllib.request

from .base import (BaseLLMProvider, InvalidOutput, LLMTimeout, LLMUnavailable,
                   ModelNotFound)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SiliconFlowProvider(BaseLLMProvider):
    name = "siliconflow"

    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.siliconflow_base_url
        self.model = config.siliconflow_model
        self.api_key = config.siliconflow_api_key
        self.timeout = config.timeout

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer %s" % self.api_key,
        }

    def generate(self, prompt, system_prompt="", response_schema=None):
        if not self.api_key:
            raise LLMUnavailable("未配置 SILICONFLOW_API_KEY")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST", headers=self._headers())

        started = time.time()
        try:
            with OPENER.open(req, timeout=self.timeout) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code in (401, 403):
                raise LLMUnavailable("SiliconFlow 鉴权失败（HTTP %s）" % e.code, body)
            if e.code == 404:
                raise ModelNotFound("模型不存在：%s" % self.model, body)
            raise LLMUnavailable("SiliconFlow 返回 HTTP %s" % e.code, body)
        except (socket.timeout, TimeoutError) as e:
            raise LLMTimeout("模型响应超时（%ss）" % self.timeout, str(e))
        except urllib.error.URLError as e:
            raise LLMUnavailable("无法连接 SiliconFlow", str(e))
        except ValueError as e:
            raise InvalidOutput("SiliconFlow 返回的不是合法 JSON", str(e))
        finally:
            self.last_duration_ms = int((time.time() - started) * 1000)

        try:
            content = doc["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise InvalidOutput("响应结构缺少 choices[0].message.content",
                                json.dumps(doc)[:300])
        content = content.strip()
        if not content:
            raise InvalidOutput("模型返回空内容")
        return content

    def health(self):
        if not self.api_key:
            return {"available": False, "model": self.model,
                    "detail": "未配置 SILICONFLOW_API_KEY"}
        try:
            req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
            with OPENER.open(req, timeout=5) as r:
                r.read()
            return {"available": True, "model": self.model, "detail": None}
        except Exception as e:
            return {"available": False, "model": self.model, "detail": str(e)[:200]}
