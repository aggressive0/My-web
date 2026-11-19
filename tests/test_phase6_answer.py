"""
Phase 6.3 P1：问题路由与问答编排的测试。

路由用 v3 冻结题集验收（48 题）。注意：这套题是**开发集**——规则就是看着它
调出来的，1.0 不代表泛化能力。真正的泛化要看后续新增题，这里的作用是回归护栏：
以后改规则若把某类问题送错流程，必须立刻失败。
"""

import json
import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from ai.rag.answer_service import AnswerService                        # noqa: E402
from ai.rag.intent import (AMBIGUOUS, KB_FACTUAL, OUT_OF_SCOPE,        # noqa: E402
                           SQL_ANALYTICS, STRATEGY, route)

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "rag_questions.json")


class RouterTest(unittest.TestCase):
    def test_intents_on_representative_questions(self):
        cases = {
            "游戏退款政策具体是怎么规定的？": KB_FACTUAL,
            "NARAKA 最近 7 天的同时在线人数怎么变化的？": SQL_ANALYTICS,
            "六款游戏里近 30 天差评率最高的是哪一个？": SQL_ANALYTICS,
            "如何降低 Cyberpunk 2077 的负面评论占比？": STRATEGY,
            "为 Warframe 设计面向流失玩家的回归活动": STRATEGY,
            "今天北京天气怎么样？": OUT_OF_SCOPE,
            "帮我写一段 Python 快速排序代码": OUT_OF_SCOPE,
            "这个游戏最近怎么样？": AMBIGUOUS,
            "有什么变化吗？": AMBIGUOUS,
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(route(question).intent, expected)

    def test_strategy_beats_sql_when_both_words_present(self):
        """「如何降低…占比」同时含策略词与指标词，必须先判策略。"""
        self.assertEqual(route("如何降低 NARAKA 的负面评论占比？").intent, STRATEGY)

    def test_result_is_explainable(self):
        result = route("今天北京天气怎么样？")
        self.assertEqual(result.matched, "weather")
        self.assertIn("天气", result.reason)

    def test_empty_question_is_ambiguous(self):
        self.assertEqual(route("   ").intent, AMBIGUOUS)

    def test_frozen_set_accuracy(self):
        """回归护栏：48 题里不允许出现误路由。"""
        with open(FIXTURE, encoding="utf-8") as f:
            doc = json.load(f)
        wrong = [(q["id"], q["intent"], route(q["query"]).intent, q["query"])
                 for q in doc["questions"] if route(q["query"]).intent != q["intent"]]
        self.assertEqual(wrong, [], "误路由：%s" % wrong)


class FakeRag:
    """按需返回固定候选；raise_on_search 用来模拟检索异常。"""

    def __init__(self, hits=None, raise_on_search=False):
        self.hits = hits if hits is not None else []
        self.raise_on_search = raise_on_search
        self.last_query = None

    def search(self, app_id, query, top_k=None):
        self.last_query = query
        if self.raise_on_search:
            raise RuntimeError("vector store down")
        return {"hits": self.hits,
                "stats": {"kept": len(self.hits), "candidates": len(self.hits)},
                "timings_ms": {"total": 1}, "app_id": app_id, "query": query}


class FakeProvider:
    name = "fake"

    def __init__(self, payload=None, raise_on_generate=False):
        self.payload = payload
        self.raise_on_generate = raise_on_generate
        self.last_metrics = {"done_reason": "stop"}
        self.last_prompt = None

    def generate(self, prompt, system_prompt=None):
        self.last_prompt = prompt
        if self.raise_on_generate:
            raise RuntimeError("llm down")
        return self.payload


DATE_HITS = [{"document_id": "doc_a", "chunk_id": "doc_a#0000", "app_id": 1203220,
              "title": "11 月 5 日更新公告", "score": 0.62,
              "content": "The servers will be suspended for maintenance from Nov 4th, 2026 23:00 (UTC) to Nov 5th, 2026 04:00 (UTC)."}]
DATE_QUESTION = "11月5日更新维护时间窗口是几点？"


class AnswerServiceTest(unittest.TestCase):
    def service(self, hits=None, provider=None, raise_on_search=False):
        return AnswerService(FakeRag(hits=hits, raise_on_search=raise_on_search),
                             provider=provider)

    def test_non_kb_intents_never_touch_retrieval(self):
        """路由错一次后面全错——所以这条要钉死：非知识库问题不许查库。"""
        for question in ("今天北京天气怎么样？", "有什么变化吗？",
                         "PUBG 过去一周新增了多少条评论？",
                         "为 Warframe 设计回归活动"):
            with self.subTest(question=question):
                rag = FakeRag(hits=DATE_HITS)
                result = AnswerService(rag).answer(1203220, question)
                self.assertIsNone(rag.last_query, "不该检索却检索了：%s" % question)
                self.assertIn(result["status"], ("out_of_scope", "ambiguous_query",
                                                 "route_to_sql",
                                                 "strategy_generation_pending"))

    def test_out_of_scope_message(self):
        result = self.service().answer(1203220, "今天北京天气怎么样？")
        self.assertEqual(result["status"], "out_of_scope")
        self.assertTrue(result["answer"])

    def test_ambiguous_asks_for_clarification(self):
        result = self.service().answer(1203220, "有什么变化吗？")
        self.assertEqual(result["status"], "ambiguous_query")
        self.assertIn("请补充", result["answer"])

    def test_sql_routes_without_answer(self):
        result = self.service().answer(1203220, "最近 7 天同时在线人数怎么变化？")
        self.assertEqual(result["status"], "route_to_sql")
        self.assertIsNone(result["answer"], "不能凭空编造动态指标")

    def test_strategy_is_pending_when_generator_missing(self):
        """生成器不可用时必须报 pending；返回 strategy_generated 就是谎报。"""
        result = self.service().answer(1203220, "设计一套召回活动方案")
        self.assertEqual(result["status"], "strategy_generation_pending")
        self.assertIsNone(result["answer"])

    def test_strategy_generated_passes_payload_through(self):
        class FakeStrategy:
            def generate(self, app_id, persist=True):
                return {"status": "generated", "strategy_id": "s1",
                        "strategy": {"summary": "先沟通再评估活动", "recall_actions": []},
                        "evidence_summary": {"actions": 3},
                        "knowledge_query": "版本更新 修复"}

        service = AnswerService(FakeRag(DATE_HITS), strategy=FakeStrategy())
        result = service.answer(1203220, "设计一套召回活动方案")
        self.assertEqual(result["status"], "strategy_generated")
        self.assertEqual(result["strategy_id"], "s1")
        self.assertEqual(result["answer"], "先沟通再评估活动")
        self.assertEqual(result["knowledge_query"], "版本更新 修复")

    def test_strategy_without_metrics_is_insufficient(self):
        class FakeStrategy:
            def generate(self, app_id, persist=True):
                return {"status": "insufficient_evidence", "reason": "no_dynamic_metrics"}

        service = AnswerService(FakeRag(DATE_HITS), strategy=FakeStrategy())
        result = service.answer(1203220, "设计一套召回活动方案")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["reason"], "no_dynamic_metrics")

    def test_strategy_failure_is_generation_failed(self):
        class FakeStrategy:
            def generate(self, app_id, persist=True):
                return {"status": "generation_failed", "reason": "data_evidence_mismatch"}

        service = AnswerService(FakeRag(DATE_HITS), strategy=FakeStrategy())
        result = service.answer(1203220, "设计一套召回活动方案")
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "data_evidence_mismatch")

    def test_undetermined_shape_proceeds_to_generation(self):
        """mixed 形态 = 程序层没有约束，不等于证据不足。

        把它当拒答，会把开放类问题（占题库一半）全部砍掉。
        """
        provider = FakeProvider('{"answer": "更新包含维护与平衡性调整。",'
                                ' "citations": ["E1"], "insufficient": false}')
        result = self.service(hits=DATE_HITS, provider=provider).answer(
            1203220, "最近的版本更新内容有哪些？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["evidence"]["status"], "undetermined")

    def test_undetermined_without_provider_reports_generation_failed(self):
        result = self.service(hits=DATE_HITS, provider=None).answer(
            1203220, "最近的版本更新内容有哪些？")
        self.assertEqual(result["status"], "generation_failed")

    def test_retrieval_error_is_reported(self):
        result = self.service(raise_on_search=True).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "retrieval_failed")
        self.assertEqual(result["reason"], "retrieval_error")

    def test_insufficient_when_shape_missing(self):
        hits = [{"document_id": "d", "chunk_id": "d#0", "app_id": 1203220,
                 "title": "封禁名单", "score": 0.5,
                 "content": "Dear players, we are committed to a fair environment."}]
        result = self.service(hits=hits).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("date_expression", result["evidence"]["missing_elements"])
        self.assertEqual(result["citations"], [])

    def test_answered_with_mapped_citations(self):
        provider = FakeProvider('{"answer": "维护窗口为 11-04 23:00 至 11-05 04:00 UTC。",'
                                ' "citations": ["E1"], "insufficient": false}')
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(result["citations"][0]["chunk_id"], "doc_a#0000")
        self.assertEqual(result["citations"][0]["label"], "E1")

    def test_hallucinated_citation_is_rejected(self):
        provider = FakeProvider('{"answer": "见证据。", "citations": ["E9"],'
                                ' "insufficient": false}')
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "citation_not_in_candidates")
        self.assertIsNone(result["answer"], "无法追溯的答案不许返回")

    def test_placeholder_answer_is_not_accepted(self):
        """实测模型会返回 "..."。只判非空就会把占位符当成功——必须按内容长度拦。"""
        provider = FakeProvider('{"answer": "...", "citations": ["E1"],'
                                ' "insufficient": false}')
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "empty_answer")

    def test_evidence_sent_to_model_is_capped(self):
        """4B 模型在 10 条长证据下会退化（实测输出 "..."），只送前 N 条。

        默认 N=5：召回深度是 50，但上下文只放 5 条——Top-K 与上下文预算是两件事。
        """
        hits = [dict(DATE_HITS[0], chunk_id="c%d" % i, document_id="d%d" % i)
                for i in range(10)]
        provider = FakeProvider('{"answer": "维护时间为 11-04 23:00 至 11-05 04:00 UTC。",'
                                ' "citations": ["E1"], "insufficient": false}')
        result = self.service(hits=hits, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "answered")
        self.assertIn("E5", provider.last_prompt)
        self.assertNotIn("E6", provider.last_prompt)

    def test_max_evidence_is_configurable(self):
        hits = [dict(DATE_HITS[0], chunk_id="c%d" % i, document_id="d%d" % i)
                for i in range(10)]
        provider = FakeProvider('{"answer": "维护时间为 11-04 23:00 至 11-05 04:00 UTC。",'
                                ' "citations": ["E1"], "insufficient": false}')
        service = AnswerService(FakeRag(hits=hits), provider=provider, max_evidence=2)
        service.answer(1203220, DATE_QUESTION)
        self.assertIn("E2", provider.last_prompt)
        self.assertNotIn("E3", provider.last_prompt)

    def test_invalid_json_is_generation_failed(self):
        provider = FakeProvider("这不是 JSON")
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "generation_invalid")

    def test_missing_provider_is_explicit(self):
        result = self.service(hits=DATE_HITS, provider=None).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "llm_provider_unavailable")

    def test_llm_unavailable_is_reported_as_llm_error(self):
        """模型服务没起 ≠ 调用出异常。两者混报会把排查引向错误方向。"""
        from ai.llm.base import LLMUnavailable

        class DownProvider:
            last_metrics = {}

            def generate(self, prompt, system_prompt=None):
                raise LLMUnavailable("无法连接本地模型服务", "ConnectionRefusedError")

        result = self.service(hits=DATE_HITS, provider=DownProvider()).answer(
            1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "llm_error")
        self.assertEqual(result["detail"], "LLM_UNAVAILABLE")

    def test_llm_failure_is_caught(self):
        provider = FakeProvider(raise_on_generate=True)
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")
        self.assertEqual(result["reason"], "llm_call_failed")

    def test_model_can_declare_insufficient(self):
        """形态齐备但内容没答到问题：模型自己说不充分，必须尊重。"""
        provider = FakeProvider('{"answer": "资料里只有维护时间，没有退款条款。",'
                                ' "citations": ["E1"], "insufficient": true}')
        result = self.service(hits=DATE_HITS, provider=provider).answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["reason"], "model_declared_insufficient")


class AnswerApiValidationTest(unittest.TestCase):
    """路由层只测参数校验：它不碰检索，也不该碰。"""

    @classmethod
    def setUpClass(cls):
        import app as A
        cls.client = A.app.test_client()

    def test_rejects_missing_app_id(self):
        response = self.client.post("/api/knowledge/answer", json={"query": "退款政策"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_APP_ID")

    def test_rejects_empty_query(self):
        response = self.client.post("/api/knowledge/answer",
                                    json={"app_id": 1203220, "query": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_QUERY")

    def test_rejects_negative_app_id(self):
        response = self.client.post("/api/knowledge/answer",
                                    json={"app_id": -1, "query": "退款政策"})
        self.assertEqual(response.status_code, 400)
