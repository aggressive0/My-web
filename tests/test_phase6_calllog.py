"""
Phase 6.2 Task 7：LLM 调用日志的测试。

测三件事：
1. 表能重复建、能写能读、统计口径正确；
2. 保留策略真的会删旧数据，且不误删新的；
3. 路由层在失败路径上也会留痕——只记成功的日志回答不了「为什么失败」。
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai import call_log  # noqa: E402
from ai.call_log import LLMCallLogRepository  # noqa: E402


def metrics(**over):
    base = {"done_reason": "stop", "prompt_eval_count": 56, "eval_count": 17,
            "thinking_chars": 0, "content_chars": 37, "load_ms": 3,
            "prompt_eval_ms": 50, "eval_ms": 209, "tokens_per_s": 81.2}
    base.update(over)
    return base


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = LLMCallLogRepository(os.path.join(self.tmp, "ai.db"))
        self.repo.init_schema()

    def test_init_is_idempotent(self):
        self.repo.init_schema()
        self.repo.init_schema()
        self.assertEqual(self.repo.stats()["calls"], 0)

    def test_record_and_recent(self):
        call_id = self.repo.record(purpose="ping", provider="ollama", model="qwen3:4b",
                                   ok=True, metrics=metrics(), wall_ms=350,
                                   thinking_enabled=False)
        rows = self.repo.recent(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["call_id"], call_id)
        self.assertEqual(rows[0]["eval_tokens"], 17)
        self.assertEqual(rows[0]["ok"], 1)
        self.assertEqual(rows[0]["thinking_enabled"], 0)

    def test_failure_is_recorded_with_code(self):
        self.repo.record(purpose="ping", provider="ollama", model="qwen3:4b",
                         ok=False, metrics={}, error_code="LLM_UNAVAILABLE")
        row = self.repo.recent(limit=1)[0]
        self.assertEqual(row["ok"], 0)
        self.assertEqual(row["error_code"], "LLM_UNAVAILABLE")

    def test_stats_aggregate(self):
        for _ in range(2):
            self.repo.record(purpose="ping", provider="ollama", model="m", ok=True,
                             metrics=metrics(), wall_ms=300)
        self.repo.record(purpose="ping", provider="ollama", model="m", ok=False,
                         metrics=metrics(eval_count=None), error_code="LLM_TRUNCATED",
                         truncated=True)
        stats = self.repo.stats(days=30)
        self.assertEqual(stats["calls"], 3)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["truncated"], 1)
        self.assertEqual(stats["avg_eval_tokens"], 17.0)

    def test_purpose_filter(self):
        self.repo.record(purpose="ping", provider="ollama", model="m", ok=True,
                         metrics=metrics())
        self.repo.record(purpose="warmup", provider="ollama", model="m", ok=True,
                         metrics=metrics())
        self.assertEqual(len(self.repo.recent(limit=10, purpose="warmup")), 1)

    def test_purge_removes_only_old_rows(self):
        self.repo.record(purpose="ping", provider="ollama", model="m", ok=True,
                         metrics=metrics())
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        with self.repo.session() as conn:
            conn.execute(
                """INSERT INTO llm_call_logs(call_id, purpose, ok, created_at)
                   VALUES('old_row', 'ping', 1, ?)""", (old,))
        removed = self.repo.purge(keep_days=30)
        self.assertEqual(removed, 1)
        remaining = [r["call_id"] for r in self.repo.recent(limit=10)]
        self.assertNotIn("old_row", remaining)
        self.assertEqual(len(remaining), 1)

    def test_purge_runs_on_record(self):
        """每次写入顺手清理：不为一张小表引入定时任务。"""
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        with self.repo.session() as conn:
            conn.execute(
                """INSERT INTO llm_call_logs(call_id, purpose, ok, created_at)
                   VALUES('old_row', 'ping', 1, ?)""", (old,))
        self.repo.record(purpose="ping", provider="ollama", model="m", ok=True,
                         metrics=metrics())
        self.assertEqual(len(self.repo.recent(limit=10)), 1)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        call_log.reset_cache()

    def tearDown(self):
        call_log.reset_cache()

    def test_same_path_returns_same_instance(self):
        path = os.path.join(self.tmp, "a.db")
        self.assertIs(call_log.get_repository(path), call_log.get_repository(path))

    def test_different_paths_are_isolated(self):
        first = call_log.get_repository(os.path.join(self.tmp, "a.db"))
        second = call_log.get_repository(os.path.join(self.tmp, "b.db"))
        first.record(purpose="ping", provider="ollama", model="m", ok=True,
                     metrics=metrics())
        self.assertEqual(second.stats()["calls"], 0)


class ApiLoggingTest(unittest.TestCase):
    """路由层：成功、截断、失败三条路径都要留痕。"""

    @classmethod
    def setUpClass(cls):
        import app as A
        cls.client = A.app.test_client()

    def setUp(self):
        # 兼容两种跑法：discover（模块名为 test_phase6）与 -m unittest tests.xxx
        try:
            import test_phase6 as helper
        except ImportError:                       # pragma: no cover
            from tests import test_phase6 as helper
        self.stub = helper.StubOllama()
        self.addCleanup(self.stub.close)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = helper.env
        self.ctx = self.env(
            AI_ENABLED="true", LLM_PROVIDER="ollama", OLLAMA_TIMEOUT="5",
            OLLAMA_BASE_URL=self.stub.url,
            LLM_CALL_LOG_DB=os.path.join(self.tmp, "ai.db"))
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        call_log.reset_cache()

    def tearDown(self):
        call_log.reset_cache()

    def rows(self):
        return call_log.get_repository().recent(limit=10)

    def test_success_is_logged(self):
        self.client.post("/api/ai/llm/ping", json={})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["purpose"], "ping")
        self.assertEqual(rows[0]["ok"], 1)
        self.assertEqual(rows[0]["eval_tokens"], 17)
        self.assertEqual(rows[0]["thinking_enabled"], 0)

    def test_truncation_is_logged_as_failure(self):
        self.stub.mode = "truncated_empty"
        response = self.client.post("/api/ai/llm/ping", json={})
        self.assertEqual(response.status_code, 422)
        row = self.rows()[0]
        self.assertEqual(row["ok"], 0)
        self.assertEqual(row["error_code"], "LLM_TRUNCATED")
        self.assertEqual(row["truncated"], 1)

    def test_invalid_json_is_logged_as_failure(self):
        """调用成功但输出不可用，ok 记 0——否则成功率会把垃圾算成健康。"""
        self.stub.mode = "not_json"
        self.client.post("/api/ai/llm/ping", json={})
        row = self.rows()[0]
        self.assertEqual(row["ok"], 0)
        self.assertEqual(row["error_code"], "INVALID_JSON")

    def test_metrics_endpoint_shape(self):
        self.client.post("/api/ai/llm/ping", json={})
        body = self.client.get("/api/ai/metrics?days=7&limit=5").get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["stats"]["calls"], 1)
        self.assertEqual(body["data"]["stats"]["window_days"], 7)
        self.assertEqual(len(body["data"]["recent"]), 1)
        self.assertNotIn("prompt", body["data"]["recent"][0])

    def test_metrics_rejects_bad_params(self):
        self.assertEqual(self.client.get("/api/ai/metrics?days=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/ai/metrics?days=999").status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
