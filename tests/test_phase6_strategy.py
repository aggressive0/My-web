"""
Phase 6.5：动态指标、策略生成、落库与接口的测试。

重点保护两条不变量：
1. 指标必须按真实可得窗口计算，缺数据要说缺，不能用 0 冒充。
2. 模型输出里的数值必须与本地统计一致，否则不落库。
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.recall.generator import (STATUS_FAILED, STATUS_GENERATED,   # noqa: E402
                                 STATUS_INSUFFICIENT, StrategyGenerator,
                                 verify_data_evidence)
from ai.recall.metrics import MetricsCollector                      # noqa: E402
from ai.recall.store import RecallStrategyStore                     # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def make_db(path, ccu_span_hours=26, reviews=True):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE ccu (appid INTEGER, ts_utc TEXT, players INTEGER);
        CREATE TABLE steam_reviews (appid INTEGER, review_id TEXT, created_ts TEXT,
            voted_up INTEGER, language TEXT, playtime_minutes INTEGER,
            votes_up INTEGER, refunded INTEGER, text TEXT);
    """)
    # CCU：每 5 分钟一个点，覆盖 span 小时；数值从 1000 线性涨到 2000
    total = max(2, int(ccu_span_hours * 12))
    for i in range(total):
        ts = NOW - timedelta(hours=ccu_span_hours) + timedelta(minutes=5 * i)
        conn.execute("INSERT INTO ccu VALUES (?,?,?)",
                     (1, ts.isoformat(timespec="seconds"), 1000 + i))
    if reviews:
        rows = [(1, "r1", NOW - timedelta(days=2), 0, "en", 100, 0, 0, "bad"),
                (1, "r2", NOW - timedelta(days=3), 1, "en", 200, 0, 0, "good"),
                (1, "r3", NOW - timedelta(days=4), 0, "en", 300, 0, 1, "bad2")]
        conn.executemany("INSERT INTO steam_reviews VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "steam.db")
        make_db(self.db)

    def test_ccu_change_computed(self):
        result = MetricsCollector(self.db).collect(1)
        names = {e["name"]: e for e in result["evidence"]}
        self.assertIn("ccu_latest", names)
        self.assertIn("ccu_change_24h_pct", names)
        self.assertEqual(names["ccu_latest"]["value"], 1000 + 26 * 12 - 1)

    def test_short_ccu_span_is_reported_missing(self):
        short = os.path.join(self.tmp, "short.db")
        make_db(short, ccu_span_hours=5)
        result = MetricsCollector(short).collect(1)
        missing = {m["name"]: m["reason"] for m in result["missing"]}
        self.assertIn("ccu_change_24h_pct", missing)
        self.assertIn("不足以计算", missing["ccu_change_24h_pct"])

    def test_review_ratio_and_as_of(self):
        result = MetricsCollector(self.db).collect(1)
        names = {e["name"]: e for e in result["evidence"]}
        self.assertAlmostEqual(names["negative_ratio_window"]["value"], 2 / 3.0, places=4)
        self.assertAlmostEqual(names["refunded_ratio_window"]["value"], 1 / 3.0, places=4)
        # 锚点是最新一条评论的时间，不是 now()
        self.assertTrue(names["negative_ratio_window"]["as_of"].startswith("2026-11-16"))

    def test_no_reviews_is_missing_not_zero(self):
        bare = os.path.join(self.tmp, "bare.db")
        make_db(bare, reviews=False)
        result = MetricsCollector(bare).collect(1)
        names = {e["name"] for e in result["evidence"]}
        self.assertNotIn("negative_ratio_window", names, "没有评论却给了差评率")
        self.assertTrue(any(m["name"] == "reviews" for m in result["missing"]))

    def test_no_data_at_all(self):
        empty = os.path.join(self.tmp, "empty.db")
        make_db(empty, ccu_span_hours=1, reviews=False)
        conn = sqlite3.connect(empty)
        conn.execute("DELETE FROM ccu")
        conn.commit()
        conn.close()
        result = MetricsCollector(empty).collect(1)
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["available_count"], 0)


class DataEvidenceGuardTest(unittest.TestCase):
    """模型复述的数字必须与本地统计一致。"""

    COMPUTED = [{"name": "negative_ratio_window", "value": 0.1111},
                {"name": "ccu_latest", "value": 36353.0}]

    def test_match_passes(self):
        self.assertEqual(verify_data_evidence(
            [{"name": "negative_ratio_window", "value": 0.1111}], self.COMPUTED), [])

    def test_value_mismatch_rejected(self):
        problems = verify_data_evidence(
            [{"name": "negative_ratio_window", "value": 0.2}], self.COMPUTED)
        self.assertTrue(any("value_mismatch" in p for p in problems))

    def test_unknown_metric_rejected(self):
        problems = verify_data_evidence([{"name": "made_up", "value": 1}], self.COMPUTED)
        self.assertTrue(any("unknown_metric" in p for p in problems))

    def test_non_numeric_rejected(self):
        problems = verify_data_evidence(
            [{"name": "ccu_latest", "value": "约 3.6 万"}], self.COMPUTED)
        self.assertTrue(any("value_not_numeric" in p for p in problems))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = RecallStrategyStore(os.path.join(self.tmp, "steam.db"))
        self.store.init_schema()

    def test_save_and_get(self):
        sid = self.store.save({"app_id": 1, "summary": "s", "execution_status": "suggested",
                               "risk_analysis": {"risk_level": "high"},
                               "recall_actions": [{"name": "a"}],
                               "data_evidence": [], "knowledge_evidence": []})
        record = self.store.get(sid)
        self.assertEqual(record["app_id"], 1)
        self.assertEqual(record["strategy"]["summary"], "s")
        self.assertEqual(record["action_count"], 1)

    def test_list_filters_by_app(self):
        for app in (1, 1, 2):
            self.store.save({"app_id": app, "summary": "s", "execution_status": "suggested",
                             "risk_analysis": {}, "recall_actions": [],
                             "data_evidence": [], "knowledge_evidence": []})
        self.assertEqual(self.store.list(app_id=1)["total"], 2)
        self.assertEqual(self.store.list()["total"], 3)

    def test_missing_returns_none(self):
        self.assertIsNone(self.store.get("nope"))


class FakeRag:
    def __init__(self, hits=None, raise_exc=False):
        self.hits = hits if hits is not None else [
            {"chunk_id": "chunk_x", "document_id": "doc_x", "title": "更新公告",
             "score": 0.6, "content": "本次更新修复了匹配超时问题。"}]
        self.raise_exc = raise_exc
        self.last_query = None

    def search(self, app_id, query, top_k=None):
        self.last_query = query
        if self.raise_exc:
            raise RuntimeError("vector down")
        return {"hits": self.hits, "stats": {}, "timings_ms": {}}


class FakeProvider:
    name = "fake"

    def __init__(self, payload=None, raise_exc=False):
        self.payload = payload
        self.raise_exc = raise_exc
        self.last_metrics = {"done_reason": "stop"}

    def generate(self, prompt, system_prompt=None):
        if self.raise_exc:
            raise RuntimeError("llm down")
        return self.payload


def valid_strategy(app_id, metrics, chunk_id="chunk_x"):
    evidence = metrics["evidence"]
    return {
        "app_id": app_id,
        "execution_status": "suggested",
        "summary": "负面反馈集中在匹配体验，建议先沟通再评估活动",
        "risk_analysis": {
            "risk_level": "medium",
            "observed_signals": [{"metric": evidence[0]["name"],
                                  "value": evidence[0]["value"], "source": "sqlite"}],
            "possible_causes": ["版本更新后的匹配问题"],
        },
        "data_evidence": [{"name": e["name"], "value": e["value"],
                           "description": e.get("description", ""), "source": "sqlite"}
                          for e in evidence],
        "knowledge_evidence": [{"document_id": "doc_x", "chunk_id": chunk_id,
                                "title": "更新公告", "source_name": "Steam 官方公告",
                                "source_url": None, "claim": "公告说明修复了匹配超时"}],
        "ai_inference": [{"reasoning": "负面情绪与更新时间接近，仅相关不构成因果",
                          "confidence": "medium", "limitations": "公开数据不足以证明因果"}],
        "recall_actions": [{"name": "发布匹配机制说明", "target_group": "近期差评玩家",
                            "objective": "降低误解", "action_type": "community_operation",
                            "priority": "high", "description": "在社区说明匹配逻辑",
                            "expected_effect": "差评增速放缓",
                            "measurement": ["差评率", "同时在线人数"]}],
        "limitations": ["本方案为群体级建议，不具备玩家级识别能力"],
    }


class GeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "steam.db")
        make_db(self.db)
        self.collector = MetricsCollector(self.db)
        self.store = RecallStrategyStore(self.db)
        self.store.init_schema()
        self.metrics = self.collector.collect(1)

    def generator(self, provider, rag=None):
        return StrategyGenerator(provider, rag or FakeRag(), self.collector, store=self.store)

    def test_success_is_validated_and_saved(self):
        payload = json.dumps(valid_strategy(1, self.metrics), ensure_ascii=False)
        result = self.generator(FakeProvider(payload)).generate(1, game_name="测试游戏")
        self.assertEqual(result["status"], STATUS_GENERATED)
        self.assertTrue(result["strategy_id"])
        self.assertEqual(self.store.list()["total"], 1)
        self.assertEqual(result["strategy"]["execution_status"], "suggested")

    def test_no_metrics_is_insufficient(self):
        empty = os.path.join(self.tmp, "empty.db")
        make_db(empty, ccu_span_hours=1, reviews=False)
        conn = sqlite3.connect(empty)
        conn.execute("DELETE FROM ccu")
        conn.commit()
        conn.close()
        collector = MetricsCollector(empty)
        result = StrategyGenerator(FakeProvider("{}"), FakeRag(), collector,
                                   store=self.store).generate(1)
        self.assertEqual(result["status"], STATUS_INSUFFICIENT)
        self.assertEqual(result["reason"], "no_dynamic_metrics")
        self.assertEqual(self.store.list()["total"], 0, "没数据却落了库")

    def test_invalid_json_does_not_persist(self):
        result = self.generator(FakeProvider("不是 JSON")).generate(1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertEqual(result["reason"], "output_unusable")
        self.assertEqual(self.store.list()["total"], 0)

    def test_data_mismatch_does_not_persist(self):
        broken = valid_strategy(1, self.metrics)
        broken["data_evidence"][0]["value"] = 999.0
        result = self.generator(FakeProvider(json.dumps(broken, ensure_ascii=False))).generate(1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertEqual(result["reason"], "data_evidence_mismatch")
        self.assertEqual(self.store.list()["total"], 0, "编造的数值被存进去了")

    def test_unknown_chunk_does_not_persist(self):
        payload = valid_strategy(1, self.metrics, chunk_id="ghost#0")
        result = self.generator(FakeProvider(json.dumps(payload, ensure_ascii=False))).generate(1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertEqual(result["reason"], "validation_failed")
        self.assertEqual(self.store.list()["total"], 0)

    def test_wrong_app_id_is_rejected(self):
        payload = valid_strategy(2, self.metrics)
        result = self.generator(FakeProvider(json.dumps(payload, ensure_ascii=False))).generate(1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("APP_ID_MISMATCH", result["detail"])

    def test_no_provider(self):
        result = StrategyGenerator(None, FakeRag(), self.collector,
                                   store=self.store).generate(1)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertEqual(result["reason"], "llm_provider_unavailable")

    def test_knowledge_query_follows_signals(self):
        """检索词必须来自信号，而不是那个写死的常量。"""
        payload = json.dumps(valid_strategy(1, self.metrics), ensure_ascii=False)
        rag = FakeRag()
        result = self.generator(FakeProvider(payload), rag=rag).generate(1)
        self.assertEqual(result["status"], STATUS_GENERATED)
        self.assertIsNotNone(rag.last_query)
        # 测试库的 CCU 是线性上涨，不该出现「服务器/稳定性」这类下跌方向词
        self.assertNotIn("服务器", rag.last_query)
        self.assertIn("版本更新", rag.last_query)
        self.assertIn("knowledge_query_basis", result)

    def test_label_reference_is_mapped_to_real_chunk(self):
        """模型只写 E1，chunk_id/document_id/标题由程序补全——它无法伪造来源。"""
        from ai.recall.generator import resolve_evidence_refs
        document = {"knowledge_evidence": [{"chunk_id": "e1", "claim": "修复了匹配"}]}
        hits = [{"chunk_id": "doc_x#0000", "document_id": "doc_x",
                 "title": "更新公告", "source_name": "Steam 官方公告"}]
        resolved = resolve_evidence_refs(document, hits)
        item = resolved["knowledge_evidence"][0]
        self.assertEqual(item["chunk_id"], "doc_x#0000")
        self.assertEqual(item["document_id"], "doc_x")
        self.assertEqual(item["title"], "更新公告")

    def test_label_reference_generates_passing_strategy(self):
        payload = valid_strategy(1, self.metrics, chunk_id="E1")
        result = self.generator(FakeProvider(json.dumps(payload, ensure_ascii=False))).generate(1)
        self.assertEqual(result["status"], STATUS_GENERATED)
        stored = result["strategy"]["knowledge_evidence"][0]
        self.assertEqual(stored["chunk_id"], "chunk_x")
        self.assertEqual(stored["document_id"], "doc_x")
