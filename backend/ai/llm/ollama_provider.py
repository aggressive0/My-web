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

from .base import (BaseLLMProvider, InvalidOutput, LLMTimeout, LLMTruncated,
                   LLMUnavailable, ModelNotFound, failure_reason)

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
            # 连接被拒 / 主机不可达都归到「服务不可用」。
            # message 里不出现 base_url——它是要回给浏览器的（AGENTS.md §8.1）。
            raise LLMUnavailable(
                "无法连接本地模型服务（Ollama 未启动或端口不可达）",
                "base_url=%s reason=%r" % (self.base_url, getattr(e, "reason", e)))
        except ValueError as e:
            raise InvalidOutput("Ollama 返回的不是合法 JSON", str(e))

    def _get(self, path, timeout=5):
        req = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json"})
        with OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _record_metrics(self, doc):
        """把 Ollama 返回的统计落到 last_metrics。

        Ollama 的耗时字段单位是**纳秒**，直接当毫秒用会得到 1e6 倍的假数字。
        这里统一换成毫秒并保留原始 token 计数，方便事后算 tok/s。
        """
        doc = doc or {}
        msg = doc.get("message") or {}
        ns_to_ms = lambda v: int((v or 0) / 1e6)          # noqa: E731
        self.last_metrics = {
            "done_reason": doc.get("done_reason"),
            "eval_count": doc.get("eval_count"),
            "prompt_eval_count": doc.get("prompt_eval_count"),
            "load_ms": ns_to_ms(doc.get("load_duration")),
            "prompt_eval_ms": ns_to_ms(doc.get("prompt_eval_duration")),
            "eval_ms": ns_to_ms(doc.get("eval_duration")),
            "total_ms": ns_to_ms(doc.get("total_duration")),
            "thinking_chars": len((msg.get("thinking") or "")),
            "content_chars": len((msg.get("content") or "")),
            "thinking_enabled": bool(self.config.enable_thinking),
        }
        eval_ms = doc.get("eval_duration") or 0
        if eval_ms:
            self.last_metrics["tokens_per_s"] = round(
                (doc.get("eval_count") or 0) / (eval_ms / 1e9), 1)

    # ------------------------------------------------------------------ 接口
    def generate(self, prompt, system_prompt="", response_schema=None, options=None):
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
            # think 必须显式传：不传时 Ollama 按模型默认（本项目实测为**开启**），
            # 同一提示下思考消耗 580–664 token 而答案只有 17 token。
            "think": bool(self.config.enable_thinking),
            # keep_alive 同理：不传就用服务端默认（空闲即卸载），
            # 下次请求要重新加载模型，实测多花 2.3–4 秒。
            "keep_alive": self.config.keep_alive,
            "options": {
                "temperature": self.config.temperature,
                # num_ctx 是总窗口，num_predict 是输出上限。两者取自不同配置项
                # （LLM_CONTEXT_LENGTH / LLM_MAX_OUTPUT_TOKENS），不再共用一个数。
                "num_ctx": self.config.context_length,
                "num_predict": self.config.max_output_tokens,
            },
        }
        # 按调用覆盖：判定类调用用 temperature=0 换取可重复性，
        # 生成类调用仍走配置值，两者不互相绑架。
        if options:
            payload["options"].update(options)

        started = time.time()
        try:
            doc = self._post("/api/chat", payload, self.timeout)
        finally:
            self.last_duration_ms = int((time.time() - started) * 1000)

        self._record_metrics(doc)

        content = ((doc or {}).get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            # 空内容 + done_reason=length = 预算在思考阶段就被烧光，一个字符都没剩下。
            # 这和「模型什么都没说」是两回事，必须分开报，否则调参方向会错。
            if (doc or {}).get("done_reason") == "length":
                raise LLMTruncated(
                    "模型输出被截断：num_predict 预算耗尽，未产生可解析内容"
                    "（若 LLM_ENABLE_THINKING=true，思考会先吃掉预算）",
                    json.dumps({k: (doc or {}).get(k) for k in
                                ("done_reason", "eval_count", "prompt_eval_count")},
                               ensure_ascii=False))
            raise InvalidOutput("模型返回空内容", json.dumps(doc)[:300])

        # 有些版本会把 JSON 包在 ```json 围栏里，这里剥掉——
        # 但剥不掉就报错，不猜（方案 §10.1 明确不允许围栏外的额外文本）
        if content.startswith("```"):
            first_nl = content.find("\n")
            last = content.rfind("```")
            if first_nl != -1 and last > first_nl:
                content = content[first_nl + 1:last].strip()

        return content

    def unload(self):
        """让模型立刻退出显存（keep_alive=0）。

        只给预热脚本与排查用：验证「冷启动到底多慢」必须先能可靠卸载，
        否则每次测到的都是热启动。
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "keep_alive": 0,
            "options": {"num_predict": 1},
        }
        self._post("/api/chat", payload, timeout=60)
        return True

    def _loaded(self):
        """模型当前是否在内存/显存里。查不到就返回 None，不猜。"""
        try:
            ps = self._get("/api/ps", timeout=5)
        except Exception:
            return None
        for item in ps.get("models") or []:
            name = item.get("name") or item.get("model") or ""
            if name == self.model or name.startswith(self.model):
                return True
        return False

    def health(self):
        """绝不抛异常：健康检查自己崩掉比「报告不可用」更糟。

        detail 直接出现在 /api/ai/health 响应里，因此只放可公开的原因，
        不放 base_url、端口或系统错误码。
        """
        try:
            doc = self._get("/api/tags", timeout=5)
        except Exception as e:                       # 连接失败与解析失败同一处理
            return {"available": False, "model": self.model,
                    "detail": failure_reason(e)}

        models = [m.get("name") or m.get("model") or "" for m in (doc.get("models") or [])]
        # 允许 "qwen3:4b" 匹配 "qwen3:4b-instruct-q4_K_M" 这类带量化后缀的标签
        hit = any(m == self.model or m.startswith(self.model) for m in models)
        return {
            "available": True,
            "model": self.model,
            "model_installed": hit,
            # 已安装 ≠ 已加载。前者查 /api/tags，后者只能查 /api/ps——
            # 首次请求慢 2–4 秒就是差在「已安装但没驻留」这一格。
            "loaded": self._loaded(),
            "installed": sorted(x for x in models if x)[:10],
            "detail": None if hit else "服务在线，但未找到模型 %s（需 ollama pull）" % self.model,
        }
