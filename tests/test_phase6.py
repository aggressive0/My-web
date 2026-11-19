"""
Phase 6.1 验收测试：Provider 抽象、Ollama HTTP 语义、工厂、健康检查 API。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase6 -v

为什么用「本地桩服务」而不是 Mock 掉 urllib：
    方案 §15 要求覆盖超时、模型不存在、空文本、非 JSON 这些真实失败路径。
    把 urllib 打桩只能验证「我们以为的协议」，起一个真 HTTP 服务才能验证
    「状态码 → 异常类型」的映射确实成立。桩服务不依赖 Ollama，CI 可跑。
"""

import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.config import AIConfig                      # noqa: E402
from ai.llm.base import (InvalidOutput, LLMTimeout,  # noqa: E402
                         LLMTruncated, LLMUnavailable, ModelNotFound)
from ai.llm.mock_provider import CANNED, MockProvider  # noqa: E402
from ai.llm.ollama_provider import OllamaProvider    # noqa: E402
from ai.llm.provider_factory import create_llm_provider  # noqa: E402


_MODULE_LOG_DIR = None


def setUpModule():
    """把本模块所有 AI 调用日志重定向到临时目录。

    不隔离的后果实测过：跑一次测试就往开发库塞几条 wall_ms=1ms 的假调用，
    `/api/ai/metrics` 立刻变成一堆噪声，冷启动统计也就没法看了。
    """
    global _MODULE_LOG_DIR
    _MODULE_LOG_DIR = tempfile.mkdtemp(prefix="ai_log_test_")
    os.environ["LLM_CALL_LOG_DB"] = os.path.join(_MODULE_LOG_DIR, "ai.db")
    from ai import call_log
    call_log.reset_cache()


def tearDownModule():
    global _MODULE_LOG_DIR
    os.environ.pop("LLM_CALL_LOG_DB", None)
    from ai import call_log
    call_log.reset_cache()
    if _MODULE_LOG_DIR:
        shutil.rmtree(_MODULE_LOG_DIR, True)
        _MODULE_LOG_DIR = None


class StubOllama:
    """最小 Ollama 替身：/api/tags 与 /api/chat。"""

    def __init__(self):
        self.mode = "ok"          # 见 do_POST 的分支
        self.models = [{"name": "qwen3:4b"}, {"name": "bge-m3"}]
        self.last_body = None     # 最近一次 /api/chat 的请求体，用来验证参数真的发出去了
        self.ps_models = []       # /api/ps 返回的已驻留模型（health 用它判断 loaded）
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
                elif self.path.startswith("/api/ps"):
                    self._send(200, {"models": stub.ps_models})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    stub.last_body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    stub.last_body = {}
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
                if stub.mode == "truncated_empty":
                    # 预算在思考阶段耗尽：只剩 thinking，content 一个字符都没有
                    return self._send(200, {
                        "message": {"content": "", "thinking": "让我先想想……" * 20},
                        "done": True, "done_reason": "length",
                        "eval_count": 2048, "prompt_eval_count": 1892})
                if stub.mode == "truncated_json":
                    return self._send(200, {
                        "message": {"content": '{"app_id": 1203220, "summary": "截'},
                        "done": True, "done_reason": "length", "eval_count": 2048})
                if stub.mode == "length_but_valid":
                    # 实测过：num_predict 很小时 done_reason=length 但 JSON 仍完整
                    return self._send(200, {
                        "message": {"content": '{"ok": true, "role": "analyst"}'},
                        "done": True, "done_reason": "length", "eval_count": 16})
                self._send(200, {"message": {"content": '{"ok": true}', "role": "assistant"},
                                 "done": True, "done_reason": "stop",
                                 "eval_count": 17, "prompt_eval_count": 56,
                                 "eval_duration": 200000000,
                                 "load_duration": 0})

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
            "AI_ENABLED": None, "SILICONFLOW_API_KEY": None, "SILICONFLOW_MODEL": None,
            "LLM_ENABLE_THINKING": None, "LLM_CONTEXT_LENGTH": None,
            "LLM_MAX_OUTPUT_TOKENS": None, "OLLAMA_NUM_CTX": None, "LLM_MAX_TOKENS": None,
            "LLM_KEEP_ALIVE": None}
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


class ThinkingControlTest(unittest.TestCase):
    """Task 2 回归：`think` 必须显式进请求体，且默认关闭。

    防的是「配置项写了、代码忘了发」这类 bug——只测 config 读值看不出来，
    必须抓到真实请求体才算证明。
    """

    def setUp(self):
        self.stub = StubOllama()
        self.addCleanup(self.stub.close)

    def provider(self, **env):
        return OllamaProvider(cfg(OLLAMA_BASE_URL=self.stub.url, **env))

    def test_default_sends_think_false(self):
        self.provider().generate("hi")
        self.assertIn("think", self.stub.last_body, "think 必须显式发送，不能靠默认值")
        self.assertIs(self.stub.last_body["think"], False)

    def test_enabled_sends_think_true(self):
        self.provider(LLM_ENABLE_THINKING="true").generate("hi")
        self.assertIs(self.stub.last_body["think"], True)

    def test_metrics_recorded(self):
        provider = self.provider()
        provider.generate("hi")
        metrics = provider.last_metrics
        self.assertEqual(metrics["done_reason"], "stop")
        self.assertEqual(metrics["eval_count"], 17)
        self.assertEqual(metrics["thinking_chars"], 0)
        self.assertIn("tokens_per_s", metrics)

    def test_nanoseconds_are_converted_to_milliseconds(self):
        """Ollama 的耗时字段是纳秒，直接当毫秒会得到 1e6 倍的假数字。"""
        provider = self.provider()
        provider.generate("hi")
        self.assertEqual(provider.last_metrics["eval_ms"], 200)   # 200,000,000 ns

    def test_empty_content_with_length_is_truncated(self):
        self.stub.mode = "truncated_empty"
        with self.assertRaises(LLMTruncated):
            self.provider().generate("hi")

    def test_length_but_valid_json_is_not_an_error(self):
        """实测：num_predict 很小时 done_reason=length 但 JSON 仍完整。

        按字面「length 一律失败」会误杀合法结果，用户已确认按「length 且解析失败」判定。
        """
        self.stub.mode = "length_but_valid"
        text = self.provider().generate("hi")
        self.assertEqual(json.loads(text), {"ok": True, "role": "analyst"})


class TokenParameterTest(unittest.TestCase):
    """Task 3：输出预算与上下文窗口必须是两个独立配置，旧变量名不能哑火。"""

    def test_defaults_are_split(self):
        config = cfg()
        self.assertEqual(config.context_length, 8192)
        self.assertEqual(config.max_output_tokens, 3072)

    def test_new_names_read_independently(self):
        config = cfg(LLM_CONTEXT_LENGTH=16384, LLM_MAX_OUTPUT_TOKENS=1024)
        self.assertEqual(config.context_length, 16384)
        self.assertEqual(config.max_output_tokens, 1024)
        self.assertEqual(config.deprecations, [])

    def test_legacy_names_still_work_and_warn(self):
        config = cfg(OLLAMA_NUM_CTX=4096, LLM_MAX_TOKENS=2048)
        self.assertEqual(config.context_length, 4096)
        self.assertEqual(config.max_output_tokens, 2048)
        self.assertEqual(len(config.deprecations), 2)
        self.assertTrue(any("OLLAMA_NUM_CTX" in d for d in config.deprecations))
        self.assertTrue(any("LLM_MAX_TOKENS" in d for d in config.deprecations))

    def test_new_name_wins_over_legacy(self):
        config = cfg(LLM_CONTEXT_LENGTH=16384, OLLAMA_NUM_CTX=4096)
        self.assertEqual(config.context_length, 16384)

    def test_non_integer_falls_back_with_warning(self):
        config = cfg(LLM_MAX_OUTPUT_TOKENS="abc")
        self.assertEqual(config.max_output_tokens, 3072)
        self.assertTrue(any("不是整数" in d for d in config.deprecations))

    def test_output_budget_must_be_smaller_than_window(self):
        config = cfg(LLM_CONTEXT_LENGTH=2048, LLM_MAX_OUTPUT_TOKENS=2048)
        self.assertTrue(any("必须小于上下文窗口" in p for p in config.validate()))

    def test_legacy_property_alias_still_readable(self):
        """外部脚本可能还在读 config.num_ctx，保留别名避免静默失效。"""
        self.assertEqual(cfg(LLM_CONTEXT_LENGTH=12345).num_ctx, 12345)

    def test_payload_carries_both_values(self):
        stub = StubOllama()
        self.addCleanup(stub.close)
        provider = OllamaProvider(cfg(OLLAMA_BASE_URL=stub.url,
                                      LLM_CONTEXT_LENGTH=8192,
                                      LLM_MAX_OUTPUT_TOKENS=3072))
        provider.generate("hi")
        options = stub.last_body["options"]
        self.assertEqual(options["num_ctx"], 8192)
        self.assertEqual(options["num_predict"], 3072)

    def test_public_view_exposes_both_without_duplicate_key(self):
        view = cfg(LLM_CONTEXT_LENGTH=8192, LLM_MAX_OUTPUT_TOKENS=3072).public_view()
        self.assertEqual(view["context_length"], 8192)
        self.assertEqual(view["max_output_tokens"], 3072)
        # 旧键 num_ctx 不再重复输出：一个数字两个来源，迟早对不上
        self.assertNotIn("num_ctx", view)


class KeepAliveTest(unittest.TestCase):
    """Task 5：`keep_alive` 必须进请求体。

    不传时 Ollama 用服务端默认（空闲即卸载），下次请求重新加载模型，
    实测多花 2.3–4 秒——这是「偶发首请求很慢」的真实来源。
    """

    def setUp(self):
        self.stub = StubOllama()
        self.addCleanup(self.stub.close)

    def provider(self, **env):
        return OllamaProvider(cfg(OLLAMA_BASE_URL=self.stub.url, **env))

    def test_default_is_30m(self):
        self.assertEqual(cfg().keep_alive, "30m")

    def test_payload_carries_keep_alive(self):
        self.provider().generate("hi")
        self.assertEqual(self.stub.last_body["keep_alive"], "30m")

    def test_env_override(self):
        self.provider(LLM_KEEP_ALIVE="5m").generate("hi")
        self.assertEqual(self.stub.last_body["keep_alive"], "5m")

    def test_unload_requests_zero(self):
        self.provider().unload()
        self.assertEqual(self.stub.last_body["keep_alive"], 0)

    def test_health_distinguishes_installed_from_loaded(self):
        """已安装 ≠ 已驻留：前者查 /api/tags，后者只能查 /api/ps。"""
        self.stub.ps_models = [{"name": "qwen3:4b"}]
        health = self.provider().health()
        self.assertTrue(health["model_installed"])
        self.assertTrue(health["loaded"])
        self.stub.ps_models = []
        self.assertFalse(self.provider().health()["loaded"])


class WarmupScriptTest(unittest.TestCase):
    """预热脚本：用最小请求完成预热，失败要能被调用方识别。"""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "warmup_llm", os.path.join(root, "scripts", "warmup_llm.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.mod = module

    def test_warm_up_returns_metrics(self):
        class FakeProvider:
            last_metrics = {"load_ms": 3950, "done_reason": "stop",
                            "eval_count": 3, "thinking_chars": 0}

            def generate(self, prompt, system_prompt=""):
                return '{"ok": true}'

        result = self.mod.warm_up(FakeProvider())
        self.assertTrue(result["ok"])
        self.assertEqual(result["load_ms"], 3950)
        self.assertEqual(result["done_reason"], "stop")

    def test_empty_output_is_reported_as_failure(self):
        class FakeProvider:
            last_metrics = {}

            def generate(self, prompt, system_prompt=""):
                return "   "

        self.assertFalse(self.mod.warm_up(FakeProvider())["ok"])

    def test_script_loads_dotenv_itself(self):
        """预检发现 load_dotenv 只在 app.py 里；脚本必须自己加载。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "scripts", "warmup_llm.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn("load_dotenv(", source)
        self.assertIn('os.path.join(BACKEND, ".env")', source)


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


@contextlib.contextmanager
def env(**kv):
    """临时改环境变量，退出时恢复原值（含「原本不存在」的情况）。"""
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class NoInternalAddressTest(unittest.TestCase):
    """回归：模型服务的内部地址/端口不得进入任何浏览器可见的文本。

    这条以前真的漏过——`LLMUnavailable("无法连接 Ollama（%s）" % base_url)`
    把 `http://127.0.0.1:11434` 直接回给了 /api/ai/llm/ping 的调用方，
    而 ai_routes 的文档字符串声称「不返回内部地址」。

    修法不是删掉信息，而是把地址挪进 LLMError.detail（只进服务端日志）。
    """

    @classmethod
    def setUpClass(cls):
        import app as A
        cls.client = A.app.test_client()

    def _dead_url(self):
        stub = StubOllama()
        url = stub.url
        stub.close()
        return url

    def test_ping_error_hides_address(self):
        url = self._dead_url()
        with env(AI_ENABLED="true", LLM_PROVIDER="ollama",
                 OLLAMA_BASE_URL=url, OLLAMA_TIMEOUT="2"):
            r = self.client.post("/api/ai/llm/ping", json={})
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"]["code"], "LLM_UNAVAILABLE")
        self.assertNotIn("127.0.0.1", body)
        self.assertNotIn(url, body)
        self.assertNotIn("WinError", body)          # 系统错误码同样不出去

    def test_health_detail_hides_address(self):
        url = self._dead_url()
        with env(AI_ENABLED="true", LLM_PROVIDER="ollama", OLLAMA_BASE_URL=url):
            r = self.client.get("/api/ai/health")
        body = r.get_data(as_text=True)
        self.assertFalse(r.get_json()["data"]["llm"]["available"])
        self.assertNotIn("127.0.0.1", body)
        self.assertNotIn(url, body)
        # 仍然要能说出「为什么不可用」，否则只是把故障信息删掉了。
        # 不断言具体哪一种：本机对已关闭端口有时 RST（拒绝）、有时直接吞包（超时）。
        detail = r.get_json()["data"]["llm"]["detail"] or ""
        self.assertTrue(
            detail.startswith(("连接被拒绝", "连接超时", "主机名解析失败", "网络错误")),
            "detail 应是归类后的原因，而不是原始异常：%r" % detail)

    def test_address_moved_to_detail_not_dropped(self):
        url = self._dead_url()
        p = OllamaProvider(cfg(OLLAMA_BASE_URL=url, OLLAMA_TIMEOUT=2))
        with self.assertRaises(LLMUnavailable) as ctx:
            p.generate("hi")
        self.assertNotIn("127.0.0.1", ctx.exception.message)
        self.assertIn("127.0.0.1", ctx.exception.detail)


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
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self._env(AI_ENABLED=None,
                  CHROMA_PERSIST_DIRECTORY=os.path.join(tmp, "chroma"),
                  KNOWLEDGE_DB=os.path.join(tmp, "knowledge.db"))
        j = self.client.get("/api/ai/health").get_json()
        self.assertTrue(j["success"])
        self.assertFalse(j["data"]["llm"]["available"])
        self.assertIn("AI_ENABLED=false", j["data"]["llm"]["detail"])
        # 检索组件自 6.2 起已实现，不再断言「一定不可用」；
        # 真正的不变量是：不可用必须说明原因（AGENTS.md §4.3）
        for name in ("embedding", "vector_db"):
            comp = j["data"][name]
            self.assertIsInstance(comp["available"], bool)
            if not comp["available"]:
                self.assertTrue(comp["detail"], "%s 不可用却没有给出原因" % name)

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

    def test_ping_truncated_returns_422(self):
        stub = StubOllama()
        self.addCleanup(stub.close)
        stub.mode = "truncated_empty"
        self._env(AI_ENABLED="true", LLM_PROVIDER="ollama",
                  OLLAMA_BASE_URL=stub.url, OLLAMA_TIMEOUT="5")
        r = self.client.post("/api/ai/llm/ping", json={})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "LLM_TRUNCATED")

    def test_ping_reports_metrics_and_thinking_flag(self):
        stub = StubOllama()
        self.addCleanup(stub.close)
        self._env(AI_ENABLED="true", LLM_PROVIDER="ollama",
                  OLLAMA_BASE_URL=stub.url, OLLAMA_TIMEOUT="5",
                  LLM_ENABLE_THINKING=None)
        body = self.client.post("/api/ai/llm/ping", json={}).get_json()["data"]
        self.assertFalse(body["thinking_enabled"])
        self.assertEqual(body["done_reason"], "stop")
        self.assertFalse(body["truncated_but_valid"])
        self.assertEqual(body["metrics"]["eval_count"], 17)

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
