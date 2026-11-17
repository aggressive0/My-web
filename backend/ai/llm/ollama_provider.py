"""
Ollama Provider（Phase 6.1 主路径）。

只用标准库 urllib —— 与项目其它采集器一致，不为了一个 HTTP 调用引入 requests。
两个必须显式处理的坑（都在本项目踩过）：

1. **环境代理**：urllib 默认会读 https_proxy，而本机该端口时常是死的，
   会把 http://127.0.0.1:11434 也当代理走，报 502 Tunnel failed。
   这里强制 ProxyHandler({})，本地服务永远直连。
2. **超时语义**：连接失败与读超时要分开报（LLM_UNAVAILABLE vs LLM_TIMEOUT），
   否则前端无法区分「服务没起」和「模型太慢」。

Ollama 接口事实（按 0.3+ 的 /api/chat）：
    POST /api/chat  body={model, messages[], stream:false, format:"json", options{...}}
    成功 → {"message": {"content": "..."}, "done": true}
    模型不存在 → HTTP 404 + {"error": "model 'x' not found"}
"""

import json
import socket
import time
import urllib.error
import urllib.request

from .base import (BaseLLMProvider, InvalidOutput, LLMTimeout, LLMUnavailable,
                   ModelNotFound)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.ollama_base_url
        self.model = config.ollama_model
        self.timeout = config.timeout

    # ------------------------------------------------------------------ 内部
    def _post(self, path, payload, timeout):
        url = self.base_url + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            low = body.lower()
            if e.code == 404 or "model" in low and "not found" in low:
                raise ModelNotFound("模型不存在：%s" % self.model, body)
            raise LLMUnavailable("Ollama 返回 HTTP %s" % e.code, body)
        except (socket.timeout, TimeoutError) as e:
            raise LLMTimeout("模型响应超时（%ss）" % timeout, str(e))
        except urllib.error.URLError as e:
            # 连接被拒 / 主机不可达都归到「服务不可用」
            raise LLMUnavailable("无法连接 Ollama（%s）" % self.base_url, str(e))
        except ValueError as e:
            raise InvalidOutput("Ollama 返回的不是合法 JSON", str(e))

    def _get(self, path, timeout=5):
        req = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json"})
        with OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # ------------------------------------------------------------------ 接口
    def generate(self, prompt, system_prompt="", response_schema=None):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # format=json 让模型约束在 JSON 文本上；它不保证符合我们的 schema，
            # schema 校验必须在上层做（方案 §6.3 的要求）
            "format": "json",
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.max_output_tokens,
            },
        }

        started = time.time()
        try:
            doc = self._post("/api/chat", payload, self.timeout)
        finally:
            self.last_duration_ms = int((time.time() - started) * 1000)

        content = ((doc or {}).get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            raise InvalidOutput("模型返回空内容", json.dumps(doc)[:300])

        # 有些版本会把 JSON 包在 ```json 围栏里，这里剥掉——
        # 但剥不掉就报错，不猜（方案 §10.1 明确不允许围栏外的额外文本）
        if content.startswith("```"):
            first_nl = content.find("\n")
            last = content.rfind("```")
            if first_nl != -1 and last > first_nl:
                content = content[first_nl + 1:last].strip()

        return content

    def health(self):
        """绝不抛异常：健康检查自己崩掉比「报告不可用」更糟。"""
        try:
            doc = self._get("/api/tags", timeout=5)
        except urllib.error.URLError as e:
            return {"available": False, "model": self.model,
                    "detail": "无法连接 %s（%s）" % (self.base_url, e.reason)}
        except Exception as e:                       # 解析失败等
            return {"available": False, "model": self.model, "detail": str(e)[:200]}

        models = [m.get("name") or m.get("model") or "" for m in (doc.get("models") or [])]
        # 允许 "qwen3:4b" 匹配 "qwen3:4b-instruct-q4_K_M" 这类带量化后缀的标签
        hit = any(m == self.model or m.startswith(self.model) for m in models)
        return {
            "available": True,
            "model": self.model,
            "model_installed": hit,
            "installed": sorted(x for x in models if x)[:10],
            "detail": None if hit else "服务在线，但未找到模型 %s（需 ollama pull）" % self.model,
        }
