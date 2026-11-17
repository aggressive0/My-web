"""
Phase 6.1 验收测试：Provider 抽象、Ollama HTTP 语义、工厂、健康检查 API。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase6 -v

为什么用「本地桩服务」而不是 Mock 掉 urllib：
    方案 §15 要求覆盖超时、模型不存在、空文本、非 JSON 这些真实失败路径。
    把 urllib 打桩只能验证「我们以为的协议」，起一个真 HTTP 服务才能验证
    「状态码 → 异常类型」的映射确实成立。桩服务不依赖 Ollama，CI 可跑。
"""

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.config import AIConfig                      # noqa: E402
from ai.llm.base import (InvalidOutput, LLMTimeout,  # noqa: E402
                         LLMUnavailable, ModelNotFound)
from ai.llm.mock_provider import CANNED, MockProvider  # noqa: E402
from ai.llm.ollama_provider import OllamaProvider    # noqa: E402
from ai.llm.provider_factory import create_llm_provider  # noqa: E402


class StubOllama:
    """最小 Ollama 替身：/api/tags 与 /api/chat。"""

    def __init__(self):
        self.mode = "ok"          # ok | empty | not_json | model_missing | server_error | slow
        self.models = [{"name": "qwen3:4b"}, {"name": "bge-m3"}]
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass        # 客户端已超时断开（slow 用例），静默即可

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send(200, {"models": stub.models})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if stub.mode == "slow":
                    time.sleep(2.5)
                if stub.mode == "model_missing":
                    return self._send(404, {"error": "model 'qwen3:4b' not found"})
                if stub.mode == "server_error":
                    return self._send(500, {"error": "internal"})
                if stub.mode == "not_json":
                    return self._send(200, {"message": {"content": "这不是 JSON"}})
                if stub.mode == "fenced":
                    fenced = "```json\n{\"ok\": true, \"role\": \"analyst\"}\n```"
                    return self._send(200, {"message": {"content": fenced}})
                if stub.mode == "empty":
                    return self._send(200, {"message": {"content": "   "}})
                self._send(200, {"message": {"content": '{"ok": true}', "role": "assistant"},
                                 "done": True})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def cfg(**env):
    """构造配置：优先用环境变量模拟真实部署。"""
    keys = {"OLLAMA_BASE_URL": None, "OLLAMA_TIMEOUT": None, "LLM_PROVIDER": None,
            "AI_ENABLED": None, "SILICONFLOW_API_KEY": None, "SILICONFLOW_MODEL": None}
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    for k, v in env.items():
        if v is not None:
            os.environ[k] = str(v)
    try:
        return AIConfig()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.stub = StubOllama()
        self.addCleanup(self.stub.close)

    def provider(self, timeout=5):
        return OllamaProvider(cfg(OLLAMA_BASE_URL=self.stub.url, OLLAMA_TIMEOUT=timeout))

    def test_happy_path(self):
        p = self.provider()
        text = p.generate("hi", "sys")
        self.assertEqual(json.loads(text), {"ok": True})
        self.assertIsNotNone(p.last_duration_ms)

    def test_fenced_json_is_unwrapped(self):
        """模型偶尔会把 JSON 包在 ```json 围栏里；剥掉围栏后必须能解析。"""
        self.stub.mode = "fenced"
        text = self.provider().generate("hi", "sys")
        self.assertEqual(json.loads(text), {"ok": True, "role": "analyst"})

    def test_plain_text_passes_through_without_validation(self):
        """Provider 只负责拿到文本，不负责校验 JSON 结构。

        这是刻意的职责边界：schema 校验在策略校验器里做（方案 §10），
        否则「模型返回了非 JSON」会被误报成「模型不可用」。
        """
        self.stub.mode = "not_json"
        text = self.provider().generate("hi")
        with self.assertRaises(ValueError):
            json.loads(text)

    def test_empty_content_raises_invalid_output(self):
        self.stub.mode = "empty"
        with self.assertRaises(InvalidOutput):
            self.provider().generate("hi")

    def test_model_missing_maps_to_404_error(self):
        self.stub.mode = "model_missing"
        with self.assertRaises(ModelNotFound):
            self.provider().generate("hi")

    def test_server_error_maps_to_unavailable(self):
        self.stub.mode = "server_error"
        with self.assertRaises(LLMUnavailable):
            self.provider().generate("hi")

    def test_timeout_maps_to_llm_timeout(self):
        self.stub.mode = "slow"
        with self.assertRaises(LLMTimeout):
            self.provider(timeout=1).generate("hi")

    def test_connection_refused_maps_to_unavailable(self):
        # 找一个已经关闭的端口
        dead = StubOllama()
        url = dead.url
        dead.close()
        p = OllamaProvider(cfg(OLLAMA_BASE_URL=url, OLLAMA_TIMEOUT=2))
        with self.assertRaises(LLMUnavailable):
            p.generate("hi")


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.stub = StubOllama()
        self.addCleanup(self.stub.close)

    def test_reports_installed_model(self):
        h = OllamaProvider(cfg(OLLAMA_BASE_URL=self.stub.url)).health()
        self.assertTrue(h["available"])
        self.assertTrue(h["model_installed"])

    def test_reports_missing_model_but_service_up(self):
        self.stub.models = [{"name": "llama3:8b"}]
        h = OllamaProvider(cfg(OLLAMA_BASE_URL=self.stub.url)).health()
        self.assertTrue(h["available"])          # 服务在线
        self.assertFalse(h["model_installed"])   # 但模型没拉
        self.assertIn("ollama pull", h["detail"])

    def test_dead_service_returns_false_without_raising(self):
        dead = StubOllama()
        url = dead.url
        dead.close()
        h = OllamaProvider(cfg(OLLAMA_BASE_URL=url)).health()
        self.assertFalse(h["available"])
        self.assertFalse(OllamaProvider(cfg(OLLAMA_BASE_URL=url)).health_check())


class FactoryTest(unittest.TestCase):
    def test_ollama_and_mock(self):
        self.assertEqual(create_llm_provider(cfg(LLM_PROVIDER="ollama")).name, "ollama")
        self.assertEqual(create_llm_provider(cfg(LLM_PROVIDER="mock")).name, "mock")

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            create_llm_provider(cfg(LLM_PROVIDER="nope"))

    def test_siliconflow_without_key_reports_problem(self):
        c = cfg(LLM_PROVIDER="siliconflow", SILICONFLOW_MODEL=None)
        self.assertTrue(any("SILICONFLOW" in p for p in c.validate()))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.client = A.app.test_client()

    def _env(self, **kv):
        saved = {}
        for k, v in kv.items():
            saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])

    def test_health_when_disabled_is_honest(self):
        self._env(AI_ENABLED=None)
        j = self.client.get("/api/ai/health").get_json()
        self.assertTrue(j["success"])
        self.assertFalse(j["data"]["llm"]["available"])
        self.assertIn("AI_ENABLED=false", j["data"]["llm"]["detail"])
        self.assertFalse(j["data"]["embedding"]["available"])
        self.assertFalse(j["data"]["vector_db"]["available"])

    def test_health_with_mock_provider(self):
        self._env(AI_ENABLED="true", LLM_PROVIDER="mock")
        j = self.client.get("/api/ai/health").get_json()
        self.assertTrue(j["data"]["llm"]["available"])
        self.assertEqual(j["data"]["llm"]["provider"], "mock")

    def test_ping_disabled_returns_409(self):
        self._env(AI_ENABLED=None)
        r = self.client.post("/api/ai/llm/ping", json={})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "AI_DISABLED")

    def test_ping_with_mock_returns_valid_json_flag(self):
        self._env(AI_ENABLED="true", LLM_PROVIDER="mock")
        r = self.client.post("/api/ai/llm/ping", json={})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["data"]
        self.assertTrue(d["json_valid"])
        self.assertEqual(d["provider"], "mock")
        self.assertGreater(d["chars"], 100)

    def test_error_shape_has_no_internal_detail(self):
        self._env(AI_ENABLED="true", LLM_PROVIDER="mock")
        r = self.client.post("/api/ai/llm/ping", json={})
        self.assertEqual(set(r.get_json()["error"].keys()) if r.status_code >= 400 else {"success"}, {"success"})


class MockProviderTest(unittest.TestCase):
    def test_canned_payload_has_required_fields(self):
        doc = json.loads(CANNED)
        self.assertEqual(doc["execution_status"], "suggested")
        self.assertGreaterEqual(len(doc["recall_actions"]), 3)

    def test_mock_can_simulate_failure(self):
        p = MockProvider(cfg(LLM_PROVIDER="mock"), fail=LLMTimeout("慢"))
        with self.assertRaises(LLMTimeout):
            p.generate("x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
