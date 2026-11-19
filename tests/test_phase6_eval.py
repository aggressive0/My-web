"""
Phase 6.3 评估指标与标注校验的测试。

这些测试保护的是「数字的可信度」：指标算错，评估结论就全是错的，
而且错得很像真的。所以指标函数必须被独立验证，不能只靠跑一次评估看着顺眼。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase6_eval -v
"""

import importlib.util
import json
import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from ai.rag import evaluation  # noqa: E402


def hit(doc_id, score, app_id=100):
    return {"document_id": doc_id, "score": score, "app_id": app_id}


class RankingTest(unittest.TestCase):
    def test_rank_dedupes_and_orders(self):
        hits = [hit("a", 0.5), hit("b", 0.9), hit("a", 0.8), hit("c", 0.7)]
        self.assertEqual(evaluation.rank_documents(hits), ["b", "a", "c"])

    def test_rank_ignores_empty_doc(self):
        self.assertEqual(evaluation.rank_documents([hit("", 0.9), hit("x", 0.5)]), ["x"])


class RecallTest(unittest.TestCase):
    def test_single_label_hit(self):
        self.assertEqual(evaluation.recall_at_k({"a"}, ["b", "a"], 5), 1.0)

    def test_multi_label_partial(self):
        """标注 2 份命中 1 份记 0.5——一份公告拆成两篇发不该被判全错。"""
        self.assertAlmostEqual(evaluation.recall_at_k({"a", "b"}, ["a"], 5), 0.5)

    def test_miss(self):
        self.assertEqual(evaluation.recall_at_k({"z"}, ["a", "b"], 5), 0.0)

    def test_k_truncates(self):
        self.assertEqual(evaluation.recall_at_k({"z"}, ["a", "b", "c", "d", "e", "z"], 5), 0.0)

    def test_no_expected_returns_none(self):
        """无标注（应拒答题）不参与召回，返回 None 而不是 0。"""
        self.assertIsNone(evaluation.recall_at_k(set(), ["a"], 5))

    def test_any_mode_single_hit_of_family(self):
        """等价文档族：14 份同模板公告，命中 1 份即算召回。"""
        family = {"d%d" % i for i in range(14)}
        self.assertEqual(
            evaluation.recall_at_k(family, ["d3"], 5, match="any"), 1.0)

    def test_any_mode_miss(self):
        family = {"d%d" % i for i in range(14)}
        self.assertEqual(
            evaluation.recall_at_k(family, ["x", "y"], 5, match="any"), 0.0)

    def test_fraction_mode_is_still_default(self):
        self.assertAlmostEqual(evaluation.recall_at_k({"a", "b"}, ["a"], 5), 0.5)


class PrecisionTest(unittest.TestCase):
    def test_denominator_is_k_not_returned(self):
        """返回 1 条且正确，精度是 1/5 而不是 1/1——否则少返几条就能刷高分。"""
        self.assertAlmostEqual(evaluation.precision_at_k({"a"}, ["a"], 5), 0.2)

    def test_two_of_five(self):
        self.assertAlmostEqual(evaluation.precision_at_k({"a", "b"}, ["a", "x", "b"], 5), 0.4)


class MrrTest(unittest.TestCase):
    def test_first_hit_rank_one(self):
        self.assertEqual(evaluation.reciprocal_rank({"a"}, ["a", "b"]), 1.0)

    def test_first_hit_rank_three(self):
        self.assertAlmostEqual(evaluation.reciprocal_rank({"a"}, ["x", "y", "a"]), 1 / 3.0)

    def test_miss(self):
        self.assertEqual(evaluation.reciprocal_rank({"z"}, ["a"]), 0.0)


class IsolationTest(unittest.TestCase):
    def test_same_app_and_global_are_allowed(self):
        hits = [hit("a", 0.9, 100), hit("g", 0.8, evaluation.GLOBAL_APP_ID)]
        self.assertEqual(evaluation.isolation_violations(hits, 100), 0)

    def test_other_app_is_violation(self):
        hits = [hit("a", 0.9, 100), hit("b", 0.8, 570)]
        self.assertEqual(evaluation.isolation_violations(hits, 100), 1)


class ThresholdTest(unittest.TestCase):
    def test_apply_threshold(self):
        hits = [hit("a", 0.6), hit("b", 0.3)]
        self.assertEqual([h["document_id"] for h in evaluation.apply_threshold(hits, 0.5)],
                         ["a"])

    def test_boundary_is_inclusive(self):
        self.assertEqual(len(evaluation.apply_threshold([hit("a", 0.5)], 0.5)), 1)


class SummarizeTest(unittest.TestCase):
    def rows(self):
        return [
            {"answerable": True, "recall": 1.0, "precision": 0.2, "rr": 1.0,
             "violation": 0, "refused": None},
            {"answerable": True, "recall": 0.5, "precision": 0.1, "rr": 0.5,
             "violation": 0, "refused": None},
            {"answerable": False, "recall": None, "precision": None, "rr": None,
             "violation": 0, "refused": True},
            {"answerable": False, "recall": None, "precision": None, "rr": None,
             "violation": 0, "refused": False},
        ]

    def test_unanswerable_excluded_from_recall(self):
        stats = evaluation.summarize(self.rows(), k=5)
        self.assertAlmostEqual(stats["recall_at_k"], 0.75)   # (1.0 + 0.5) / 2
        self.assertAlmostEqual(stats["mrr"], 0.75)
        self.assertAlmostEqual(stats["refusal_accuracy"], 0.5)
        self.assertEqual(stats["answered_when_unanswerable"], 1)

    def test_counts(self):
        stats = evaluation.summarize(self.rows(), k=5)
        self.assertEqual((stats["total"], stats["answerable"], stats["unanswerable"]),
                         (4, 2, 2))


class ValidatorTest(unittest.TestCase):
    """标注校验器：它是「不出假数字」的第一道闸门。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "scripts", "evaluate_retrieval.py")
        spec = importlib.util.spec_from_file_location("evaluate_retrieval", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.mod = module

    def corpus(self):
        return {
            "g1": {"gid": "g1", "appid": 100, "contents": "维护窗口 Nov 5th, 2026 04：00"},
            "g2": {"gid": "g2", "appid": 100, "contents": "另一份公告，提到 Kael"},
            "g9": {"gid": "g9", "appid": 200, "contents": "别的游戏"},
        }

    def test_valid_passes(self):
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": True,
                              "expected_gids": ["g1"], "must_contain": ["Nov 5th, 2026 04"]}]}
        self.assertEqual(len(self.mod.validate_annotations(doc, self.corpus())), 1)

    def test_missing_gid_fails(self):
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": True,
                              "expected_gids": ["nope"], "must_contain": []}]}
        with self.assertRaises(SystemExit):
            self.mod.validate_annotations(doc, self.corpus())

    def test_wrong_app_id_fails(self):
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": True,
                              "expected_gids": ["g9"], "must_contain": []}]}
        with self.assertRaises(SystemExit):
            self.mod.validate_annotations(doc, self.corpus())

    def test_must_contain_missing_fails(self):
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": True,
                              "expected_gids": ["g1"], "must_contain": ["不存在的证据"]}]}
        with self.assertRaises(SystemExit):
            self.mod.validate_annotations(doc, self.corpus())

    def test_multi_label_needs_only_one_document_to_hold_evidence(self):
        """多标签题：证据只需出现在标注集合中的某一篇，不要求每篇都有。"""
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": True,
                              "expected_gids": ["g1", "g2"],
                              "must_contain": ["Kael"]}]}
        self.assertEqual(len(self.mod.validate_annotations(doc, self.corpus())), 1)

    def test_unanswerable_with_expected_fails(self):
        doc = {"questions": [{"id": "q1", "app_id": 100, "answerable": False,
                              "expected_gids": ["g1"], "must_contain": []}]}
        with self.assertRaises(SystemExit):
            self.mod.validate_annotations(doc, self.corpus())


class FixtureTest(unittest.TestCase):
    """题目集自身的完整性：不能出现重复 id、缺字段。"""

    def setUp(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "rag_questions.json"),
                  encoding="utf-8") as f:
            self.doc = json.load(f)

    def test_unique_ids(self):
        ids = [q["id"] for q in self.doc["questions"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_size_within_frozen_range(self):
        """Phase 6.3 决定：先冻结 45–50 题的小型评测集，再逐步扩到 100+。

        方案 §7.1 原来的「20–30 题」已被这套口径取代；改上限时必须同时改这里，
        否则「悄悄加题」会让历史指标失去可比性。
        """
        count = len(self.doc["questions"])
        self.assertGreaterEqual(count, 45)
        self.assertLessEqual(count, 50)

    def test_intent_and_shape_present(self):
        valid_intents = {"kb_factual_qa", "sql_analytics", "strategy_generation",
                         "out_of_scope", "ambiguous"}
        valid_shapes = {"count", "date", "location", "policy", "procedure", "entity", "mixed"}
        for q in self.doc["questions"]:
            self.assertIn(q.get("intent"), valid_intents, q["id"])
            self.assertIn(q.get("answer_shape"), valid_shapes, q["id"])

    def test_intent_coverage(self):
        """四类路由样本都必须存在，否则路由准确率无从度量。"""
        intents = {q["intent"] for q in self.doc["questions"]}
        for required in ("sql_analytics", "strategy_generation",
                         "out_of_scope", "ambiguous"):
            self.assertIn(required, intents)

    def test_answerable_kb_questions_have_evidence(self):
        for q in self.doc["questions"]:
            if q["intent"] == "kb_factual_qa" and q.get("answerable"):
                self.assertTrue(q.get("expected_gids"), q["id"])
                self.assertTrue(q.get("must_contain"), q["id"])

    def test_required_fields(self):
        for q in self.doc["questions"]:
            for key in ("id", "app_id", "category", "query", "answerable"):
                self.assertIn(key, q, q.get("id"))
            self.assertTrue(q["query"].strip())

    def test_categories_cover_spec(self):
        """§7.1 列的八类至少要覆盖到：补丁/活动/反馈/情绪类线索/运营/隔离/无答案/时间。"""
        cats = {q["category"] for q in self.doc["questions"]}
        for required in ("patch", "event", "isolation", "unanswerable", "time", "feedback"):
            self.assertIn(required, cats)

    def test_provenance_is_declared(self):
        """必须如实声明标注来源，避免被当成人工黄金标准。"""
        prov = self.doc["annotation_provenance"]
        self.assertEqual(prov["review_status"], "pending_human_review")


if __name__ == "__main__":
    unittest.main(verbosity=2)
