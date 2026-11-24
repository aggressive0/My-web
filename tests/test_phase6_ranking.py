"""
新近度重排的测试（Phase 6.3 遗留：publish_date 不参与排序）。

必须验证的三件事：
1. 默认（weight=0）**完全不动**既有排序——否则这次改动就是拿召回做实验。
2. 新鲜文档能被抬上来，但不是无条件压过更相关的旧文档。
3. 没有发布日期的文档不奖不罚，不能把「没写日期」当成「很旧」。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.rag.ranking import apply_recency, recency_bonus   # noqa: E402
from ai.rag.vector_store import SearchHit                 # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def hit(chunk_id, score, days_ago=None):
    published = "" if days_ago is None else (
        NOW - timedelta(days=days_ago)).date().isoformat()
    return {"chunk_id": chunk_id, "document_id": chunk_id.split("#")[0],
            "app_id": 1, "score": score, "publish_date": published}


class BonusTest(unittest.TestCase):
    def test_fresh_document_is_near_one(self):
        # publish_date 只有日期没有时刻，当天最坏差半天，所以不写死 1.0
        self.assertGreater(recency_bonus("2026-11-18", NOW, 30), 0.98)
        self.assertLessEqual(recency_bonus("2026-11-18", NOW, 30), 1.0)

    def test_one_tau_old_is_about_e_inverse(self):
        """30 天衰减常数下，一个月前的文档权重约 e^-1（含半天的日期粒度误差）。"""
        value = recency_bonus("2026-10-19", NOW, 30)
        self.assertGreater(value, 0.35)
        self.assertLess(value, 0.38)

    def test_missing_date_is_zero(self):
        """不奖不罚：把「没写日期」当成「很旧」是没有依据的推定。"""
        self.assertEqual(recency_bonus("", NOW, 30), 0.0)
        self.assertEqual(recency_bonus(None, NOW, 30), 0.0)

    def test_future_date_is_clamped(self):
        self.assertAlmostEqual(recency_bonus("2027-01-01", NOW, 30), 1.0, places=2)

    def test_bad_date_is_zero(self):
        self.assertEqual(recency_bonus("not-a-date", NOW, 30), 0.0)


class ApplyTest(unittest.TestCase):
    def test_zero_weight_is_noop(self):
        hits = [hit("a#0", 0.5, 400), hit("b#0", 0.6, 1)]
        result = apply_recency(hits, 0, now=NOW)
        self.assertEqual([h["chunk_id"] for h in result], ["a#0", "b#0"])
        self.assertEqual(result[0]["score"], 0.5, "weight=0 不该改分数")

    def test_fresh_document_overtakes_similar_stale_one(self):
        hits = [hit("stale#0", 0.60, 400), hit("fresh#0", 0.58, 1)]
        result = apply_recency(hits, 0.05, now=NOW)
        self.assertEqual(result[0]["chunk_id"], "fresh#0")

    def test_strong_relevance_still_wins(self):
        """加性奖励不能翻盘：0.75 的旧文档不该被 0.40 的新文档顶掉。"""
        hits = [hit("stale#0", 0.75, 400), hit("fresh#0", 0.40, 1)]
        result = apply_recency(hits, 0.05, now=NOW)
        self.assertEqual(result[0]["chunk_id"], "stale#0")

    def test_undated_document_keeps_score(self):
        hits = [hit("nodate#0", 0.50, None), hit("fresh#0", 0.49, 1)]
        result = apply_recency(hits, 0.05, now=NOW)
        nodate = next(h for h in result if h["chunk_id"] == "nodate#0")
        self.assertEqual(nodate["score"], 0.5)

    def test_supports_search_hit_objects(self):
        objects = [SearchHit(chunk_id="a#0", document_id="a", app_id=1, scope="game_specific",
                             chunk_index=0, content="x", score=0.60, distance=0.40,
                             publish_date="2025-01-01"),
                   SearchHit(chunk_id="b#0", document_id="b", app_id=1, scope="game_specific",
                             chunk_index=0, content="y", score=0.58, distance=0.42,
                             publish_date="2026-11-17")]
        result = apply_recency(objects, 0.05, now=NOW)
        self.assertEqual(result[0].chunk_id, "b#0")
        self.assertIsInstance(result[0], SearchHit, "类型不能被降级成 dict")


class RetrieverIntegrationTest(unittest.TestCase):
    """权重关时行为必须与改前完全一致；开时才重排。"""

    def _service(self, weight):
        from ai.config import RAGConfig
        cfg = RAGConfig()
        cfg.recency_weight = weight
        cfg.recency_tau_days = 30
        cfg.retrieval_min_score = None
        cfg.top_k = 5

        class FakeStore:
            def search(self, vector, app_id, top_k, include_global=True):
                return [SearchHit(chunk_id="old#0", document_id="old", app_id=app_id,
                                  scope="game_specific", chunk_index=0, content="old",
                                  score=0.60, distance=0.40, publish_date="2025-01-01",
                                  title="旧公告"),
                        SearchHit(chunk_id="new#0", document_id="new", app_id=app_id,
                                  scope="game_specific", chunk_index=0, content="new",
                                  score=0.58, distance=0.42, publish_date="2026-11-17",
                                  title="新公告")]

            def health(self):
                return {"available": True}

        class FakeEmbedder:
            def encode(self, texts):
                return [[0.0] * 4 for _ in texts]

        from ai.rag.retriever import Retriever
        return Retriever(FakeStore(), FakeEmbedder(), cfg)

    def test_disabled_keeps_vector_order(self):
        result = self._service(0.0).search(1, "更新")
        self.assertFalse(result["recency_applied"])
        self.assertEqual(result["hits"][0]["chunk_id"], "old#0")

    def test_enabled_promotes_fresh(self):
        result = self._service(0.05).search(1, "更新")
        self.assertTrue(result["recency_applied"])
        self.assertEqual(result["hits"][0]["chunk_id"], "new#0")


class ActionCountConstraintTest(unittest.TestCase):
    """措施数属于业务口径，不该写死在 Schema 里，也不能配了不用。"""

    def _document(self, actions):
        return {
            "app_id": 1, "execution_status": "suggested", "summary": "s",
            "risk_analysis": {"risk_level": "medium"},
            "data_evidence": [], "knowledge_evidence": [],
            "recall_actions": [{"name": "a%d" % i, "target_group": "g", "objective": "o",
                                "action_type": "t", "priority": "high",
                                "description": "d", "expected_effect": "e",
                                "measurement": ["m"]} for i in range(actions)],
        }

    def test_too_few_actions_rejected_with_count(self):
        from ai.recall.validation import SchemaMismatch, validate_strategy
        with self.assertRaises(SchemaMismatch) as ctx:
            validate_strategy(self._document(1), min_actions=3)
        self.assertIn("至少 3 条", ctx.exception.message)
        self.assertIn("实际 1 条", ctx.exception.message)

    def test_enough_actions_pass(self):
        from ai.recall.validation import validate_strategy
        strategy = validate_strategy(self._document(3), min_actions=3)
        self.assertEqual(len(strategy.recall_actions), 3)

    def test_too_many_actions_rejected(self):
        from ai.recall.validation import SchemaMismatch, validate_strategy
        with self.assertRaises(SchemaMismatch):
            validate_strategy(self._document(3), min_actions=1, max_actions=2)

    def test_without_constraint_keeps_old_behavior(self):
        from ai.recall.validation import validate_strategy
        self.assertEqual(len(validate_strategy(self._document(1)).recall_actions), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
