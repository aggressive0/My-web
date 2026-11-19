"""
Phase 6.3 P0：答案形态与证据门的测试。

四道失败问题的回归是这组测试的核心：它们用的是**真实语料里被真实检索出来的
那几篇公告**，不是编造的假数据。规则改动如果让 Q1–Q3 重新变成「可回答」，
这里必须立刻失败。
"""

import glob
import json
import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from ai.rag.answer_shapes import (detect_count_family, detect_shape,   # noqa: E402
                                  resolve_requirements)
from ai.rag.evidence_gate import (EvidenceGate, STATUS_INSUFFICIENT,   # noqa: E402
                                  STATUS_SUFFICIENT, STATUS_UNDETERMINED)

CORPUS_DIR = os.path.join(ROOT, "tests", "fixtures", "rag_corpus")


def load_hits(app_id, titles):
    """按标题从真实语料里取出候选，模拟检索结果。

    用整篇正文而不是 chunk：包含的元素更多，更难被拦。整篇都拦得住，
    chunk 只会更容易——这是保守方向的验证。
    """
    wanted = [t.lower() for t in titles]
    hits = []
    for path in glob.glob(os.path.join(CORPUS_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if doc["appid"] != app_id:
            continue
        for item in doc["items"]:
            title = (item["title"] or "").lower()
            if any(w in title for w in wanted):
                hits.append({"document_id": "doc_" + item["gid"], "app_id": app_id,
                             "title": item["title"], "content": item["contents"]})
    return hits


class ShapeTest(unittest.TestCase):
    def test_detects_each_shape(self):
        cases = {
            "游戏退款政策是什么？": "policy",
            "开发团队有多少人？": "count",
            "11月5日的维护什么时候开始？": "date",
            "机房具体位于哪里？": "location",
            "Clampdown 支线任务怎么解锁？": "procedure",
        }
        for question, expected in cases.items():
            self.assertEqual(detect_shape(question), expected, question)

    def test_policy_wins_over_generic_keywords(self):
        """同时含「政策」与「什么」，应判 policy 而不是 mixed。"""
        self.assertEqual(detect_shape("退款政策具体是怎么规定的？"), "policy")

    def test_open_question_is_mixed(self):
        self.assertEqual(detect_shape("介绍一下最近的更新"), "mixed")

    def test_how_it_is_worded_is_not_a_procedure_question(self):
        """「是怎么说的」问的是原文表述，不是操作步骤。

        第二例判成 policy 是对的（问的是规则），关键是**不能**判成 procedure：
        一旦判成方法题，就会去要求「可执行步骤」，然后误拦。
        """
        self.assertEqual(detect_shape("ExileCon 的直播安排是怎么说的？"), "mixed")
        self.assertNotEqual(detect_shape("规则是怎么规定的？"), "procedure")

    def test_count_family_split_by_subject(self):
        self.assertEqual(detect_count_family("开发团队有多少人？"), "staff")
        self.assertEqual(detect_count_family("有多少玩家在玩？"), "players")
        self.assertEqual(detect_count_family("这个活动持续多少天？"), "time")
        self.assertEqual(detect_count_family("每阶段有多少个任务？"), "amount")

    def test_count_requirements_follow_the_question(self):
        """问人数与问年数需要不同的单位——固定清单会把「11 years」判成不合格。"""
        staff = [r.name for r in resolve_requirements("团队有多少人？", "count")]
        time = [r.name for r in resolve_requirements("已经多少年了？", "count")]
        self.assertEqual(staff, time)                    # 元素名相同
        self.assertNotEqual(
            resolve_requirements("团队有多少人？", "count")[1].detect("11 entire years"),
            resolve_requirements("已经多少年了？", "count")[1].detect("11 entire years"))


class LocationShapeTest(unittest.TestCase):
    """设施位置与游戏内地点是两种问题，要求必须不同。"""

    def names(self, question):
        return [r.name for r in resolve_requirements(question, "location")]

    def test_facility_question_requires_facility_word(self):
        self.assertIn("facility_relation", self.names("游戏机房具体位于哪里？"))
        self.assertIn("facility_relation", self.names("服务器部署在哪个数据中心？"))

    def test_gameplace_question_does_not_require_facility(self):
        names = self.names("Icebind 这个模式在哪里挑战？")
        self.assertNotIn("facility_relation", names)
        self.assertIn("place_expression", names)

    def test_region_name_is_not_a_facility(self):
        """赛区/地区不是机房——这是 Q3 的核心区分。"""
        from ai.rag.answer_shapes import _FACILITY
        self.assertIsNone(_FACILITY.search("EMEA will be divided into Europe and MENA."))


class GateUnitTest(unittest.TestCase):
    def setUp(self):
        self.gate = EvidenceGate()

    def test_no_candidates_is_insufficient(self):
        result = self.gate.evaluate("团队有多少人？", [])
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertEqual(result.reason, "no_candidates_retrieved")
        self.assertEqual(result.candidate_count, 0)

    def test_mixed_shape_is_undetermined_not_guessed(self):
        """mixed 没有可程序化的约束，必须说「定不了」，不能猜成充分或不充分。"""
        hits = [{"document_id": "d1", "content": "随便一段没有形态的文本"}]
        result = self.gate.evaluate("介绍一下最近的更新", hits)
        self.assertEqual(result.status, STATUS_UNDETERMINED)
        self.assertEqual(result.reason, "answer_shape_has_no_programmatic_rule")

    def test_elements_can_come_from_different_documents(self):
        """证据分散在多篇也算齐备，但必须留痕（不假装有一篇单独的强证据）。"""
        # d1 只有日期、d2 只有事件词，任一单篇都不完整——这才叫「证据分散」
        hits = [
            {"document_id": "d1", "content": "活动将于 November 13 举行"},
            {"document_id": "d2", "content": "本次更新加入新模式"},
        ]
        result = self.gate.evaluate("什么时候开始？", hits)
        self.assertEqual(result.status, STATUS_SUFFICIENT)
        self.assertEqual(result.valid_count, 0)
        self.assertEqual(result.reason, "required_elements_split_across_documents")


class SemanticJudgementTest(unittest.TestCase):
    def test_accepts_valid(self):
        verdict = EvidenceGate.validate_semantic_judgement(
            {"verdict": "sufficient", "evidence_ids": ["c1"]}, {"c1"})
        self.assertEqual(verdict, "sufficient")

    def test_rejects_unknown_chunk(self):
        with self.assertRaises(ValueError):
            EvidenceGate.validate_semantic_judgement(
                {"verdict": "sufficient", "evidence_ids": ["ghost"]}, {"c1"})

    def test_rejects_bad_verdict(self):
        with self.assertRaises(ValueError):
            EvidenceGate.validate_semantic_judgement({"verdict": "maybe"}, {"c1"})


class FailureCaseRegressionTest(unittest.TestCase):
    """四道失败问题的回归。候选就是当时真实检索出来的那几篇公告。"""

    def setUp(self):
        self.gate = EvidenceGate()

    def test_q1_refund_is_blocked(self):
        hits = load_hits(1203220, ["Banned Players List September 15th",
                                   "Banned Players List August 25th",
                                   "Banned Players List September 29th"])
        result = self.gate.evaluate("游戏退款政策是什么？", hits)
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertEqual(result.reason, "retrieved_documents_do_not_support_required_fact")
        self.assertIn("policy_statement", result.missing_elements)

    def test_q2_headcount_is_blocked(self):
        """候选里有 "Six-player Squads"，那是游戏模式，不是团队编制。"""
        hits = load_hits(230410, ["The Icebind is live now!", "October in Warframe",
                                  "Devstream 198 Overview", "TennoGen Sale 2026"])
        result = self.gate.evaluate("游戏开发团队有多少人？", hits)
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertIn("count_unit", result.missing_elements)

    def test_q3_datacenter_is_blocked(self):
        """候选里有 Thailand / EMEA 这类赛区地名，但没有机房设施。"""
        hits = load_hits(578080, ["2027 PUBG Esports: What’s Changing",
                                  "Twisted Minds crowned as PGS 12 Champions"])
        result = self.gate.evaluate("游戏机房具体位于哪里？", hits)
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertIn("facility_relation", result.missing_elements)

    def test_q4_passes_gate_hence_router_must_come_first(self):
        """Q4 是策略题，证据门会放行。

        这不是门的缺陷，而是职责边界：版本更新公告里确实有「步骤/目标」，
        形态上齐备；能不能据此产出召回方案，是路由与策略流程要判断的事。
        这条测试钉住结论——**门不能替代路由**。
        """
        hits = load_hits(1203220, ["BLADEPOINT Update –October 22nd, 2026",
                                   "BLADEPOINT Update – August 13th, 2026"])
        result = self.gate.evaluate("如何设计召回活动？", hits)
        self.assertEqual(result.status, STATUS_SUFFICIENT)

    def test_answerable_cases_are_not_blocked(self):
        """反向护栏：门不能把可回答问题一起拦掉，否则「全拒答」也能拿满分。"""
        cases = [
            ("11月5日更新维护时间窗口是几点？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
            ("Vanquisher's Trials 每个阶段有多少个挑战任务？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
            ("Vanquisher's Trials 一共有几种难度？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
            ("TennoGen 已经多少年了？", 230410, ["TennoGen Sale 2026"]),
            ("Crypto 无人机召回时间调整成多少秒？", 1172470,
             ["Apex Legends: Latest Update 11/4/26"]),
            ("Clampdown 支线任务怎么解锁？", 1091500,
             ["Check out what's coming to Cyberpunk 2077 in the Edgerunners 2 Update!"]),
            # 以下四例曾因「形态要求不跟问题主体」被误拦，加进来防止回退
            ("Vanquisher's Trials 每个阶段有多少个挑战任务？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
            ("Vanquisher's Trials 一共有几种难度？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
            ("Icebind 这个模式在哪里挑战？", 230410, ["The Icebind is live now!"]),
            ("本次新增的水下武器在哪里才能获得？", 1203220,
             ["BLADEPOINT Update – November 5th, 2026"]),
        ]
        for question, app_id, titles in cases:
            with self.subTest(question=question):
                hits = load_hits(app_id, titles)
                self.assertTrue(hits, "语料里没找到 %s" % titles)
                result = self.gate.evaluate(question, hits)
                self.assertEqual(result.status, STATUS_SUFFICIENT,
                                 "%s 被误拦，缺失 %s" % (question, result.missing_elements))


class RetrievalConfigTest(unittest.TestCase):
    """阈值改名的回归：它不再决定能否回答，旧名必须仍能读到。"""

    KEYS = ("RAG_TOP_K", "RAG_RETRIEVAL_MIN_SCORE", "RAG_SCORE_THRESHOLD")

    def _cfg(self, **env):
        from ai.config import RAGConfig
        saved = {k: os.environ.get(k) for k in self.KEYS}
        try:
            for k in self.KEYS:
                os.environ.pop(k, None)
            for k, v in env.items():
                os.environ[k] = str(v)
            return RAGConfig()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_defaults(self):
        cfg = self._cfg()
        self.assertEqual(cfg.top_k, 10)
        self.assertIsNone(cfg.retrieval_min_score, "默认不启用召回过滤")
        self.assertEqual(cfg.deprecations, [])

    def test_legacy_name_still_readable_with_warning(self):
        cfg = self._cfg(RAG_SCORE_THRESHOLD=0.35)
        self.assertEqual(cfg.retrieval_min_score, 0.35)
        self.assertTrue(any("RAG_SCORE_THRESHOLD" in d for d in cfg.deprecations))
        self.assertTrue(any("不再作为回答许可条件" in d for d in cfg.deprecations))

    def test_new_name_wins(self):
        cfg = self._cfg(RAG_RETRIEVAL_MIN_SCORE=0.2, RAG_SCORE_THRESHOLD=0.9)
        self.assertEqual(cfg.retrieval_min_score, 0.2)

    def test_old_attribute_is_kept_as_alias(self):
        self.assertEqual(self._cfg(RAG_RETRIEVAL_MIN_SCORE=0.42).score_threshold, 0.42)
