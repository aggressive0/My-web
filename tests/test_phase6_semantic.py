"""
Phase 6.3：第二层语义复核的测试。

这一层的安全性比有效性更重要：**它只能收紧，不能放宽**。
程序层说缺必要元素时，模型说什么都不算数；模型自己不可信时，
必须降级成 undetermined，绝不能因为「问不出来」就当成「充分」。
"""

import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.rag.answer_service import AnswerService          # noqa: E402
from ai.rag.semantic_check import (VERDICT_INSUFFICIENT,  # noqa: E402
                                   VERDICT_SUFFICIENT, VERDICT_UNKNOWN,
                                   SemanticChecker)

HITS = [
    {"document_id": "d1", "chunk_id": "d1#0", "title": "封禁名单",
     "score": 0.6, "content": "Dear players, we are committed to a fair environment."},
    {"document_id": "d2", "chunk_id": "d2#0", "title": "更新公告",
     "score": 0.5, "content": "Patch notes for version 1.4.0."},
    {"document_id": "d3", "chunk_id": "d3#0", "title": "活动公告",
     "score": 0.4, "content": "Event starts soon."},
    {"document_id": "d4", "chunk_id": "d4#0", "title": "第四条",
     "score": 0.3, "content": "should not be sent to the model"},
]


class FakeProvider:
    def __init__(self, payload=None, raise_exc=False):
        self.payload = payload
        self.raise_exc = raise_exc
        self.last_metrics = {"done_reason": "stop"}
        self.prompts = []

    def generate(self, prompt, system_prompt=None):
        self.prompts.append(prompt)
        if self.raise_exc:
            raise RuntimeError("model down")
        return self.payload


class CheckerTest(unittest.TestCase):
    def test_insufficient_verdict(self):
        provider = FakeProvider('{"verdict": "insufficient", "evidence_ids": ["E1"],'
                                ' "missing": ["注册玩家总数"], "reason": "只有玩家一词"}')
        result = SemanticChecker(provider).check("有多少注册玩家？", HITS)
        self.assertEqual(result["verdict"], VERDICT_INSUFFICIENT)
        self.assertEqual(result["missing"], ["注册玩家总数"])
        self.assertTrue(result["available"])

    def test_sufficient_verdict(self):
        """判充分必须带可核验原句——这是新契约，不带就降级为不足。"""
        provider = FakeProvider('{"verdict": "sufficient", "evidence_ids": ["E1"],'
                                ' "quote": "committed to a fair environment",'
                                ' "missing": [], "reason": "直接给出"}')
        result = SemanticChecker(provider).check("维护时间？", HITS)
        self.assertEqual(result["verdict"], VERDICT_SUFFICIENT)
        self.assertIn("fair environment", result["quote"])

    def test_hallucinated_evidence_is_untrusted(self):
        provider = FakeProvider('{"verdict": "sufficient", "evidence_ids": ["E9"],'
                                ' "missing": [], "reason": "编的"}')
        result = SemanticChecker(provider).check("问题", HITS)
        self.assertEqual(result["verdict"], VERDICT_UNKNOWN)
        self.assertEqual(result["reason"], "semantic_untrusted")
        self.assertFalse(result["available"])

    def test_bad_json_is_unavailable_not_sufficient(self):
        result = SemanticChecker(FakeProvider("我觉得可以回答")).check("问题", HITS)
        self.assertEqual(result["verdict"], VERDICT_UNKNOWN)
        self.assertFalse(result["available"])

    def test_exception_is_unavailable(self):
        result = SemanticChecker(FakeProvider(raise_exc=True)).check("问题", HITS)
        self.assertFalse(result["available"])

    def test_only_top3_evidence_sent(self):
        provider = FakeProvider('{"verdict": "sufficient", "evidence_ids": [],'
                                ' "missing": [], "reason": "ok"}')
        SemanticChecker(provider, max_evidence=3).check("问题", HITS)
        prompt = provider.prompts[0]
        self.assertIn("[E3]", prompt)
        self.assertNotIn("[E4]", prompt)
        self.assertNotIn("should not be sent", prompt)

    def test_empty_candidates_is_insufficient(self):
        result = SemanticChecker(FakeProvider("{}")).check("问题", [])
        self.assertEqual(result["verdict"], VERDICT_INSUFFICIENT)


class QuoteVerificationTest(unittest.TestCase):
    """「模型说够了」必须变成可核验的断言，否则等于没校验。"""

    def test_quote_must_appear_in_cited_evidence(self):
        from ai.rag.semantic_check import verify_quote
        hits = [{"content": "The servers will be suspended from Nov 4th, 2026 23:00 (UTC)."}]
        self.assertTrue(verify_quote("Nov 4th, 2026 23:00 (UTC)", hits))
        self.assertFalse(verify_quote("Nov 9th, 2026 23:00 (UTC)", hits))

    def test_quote_is_normalized_but_not_paraphrased(self):
        from ai.rag.semantic_check import verify_quote
        hits = [{"content": "Each stage has 4 Challenge quests."}]
        self.assertTrue(verify_quote("each   stage has 4 challenge quests.", hits))
        self.assertFalse(verify_quote("每个阶段有 4 个挑战任务", hits))

    def test_too_short_quote_is_rejected(self):
        from ai.rag.semantic_check import verify_quote
        self.assertFalse(verify_quote("ok", [{"content": "ok"}]))

    def test_sufficient_without_valid_quote_becomes_insufficient(self):
        """模型声称充分但引文核验不过 → 判证据不足，而不是放行。"""
        provider = FakeProvider('{"verdict": "sufficient", "quote": "编造的一句原文",'
                                ' "evidence_ids": ["E1"], "missing": [], "reason": "ok"}')
        result = SemanticChecker(provider).check("问题", HITS)
        self.assertEqual(result["verdict"], VERDICT_INSUFFICIENT)
        self.assertEqual(result["reason"], "semantic_quote_unverified")


class FakeRag:
    def __init__(self, hits=None):
        self.hits = hits or []

    def search(self, app_id, query, top_k=None):
        return {"hits": self.hits,
                "stats": {"kept": len(self.hits), "candidates": len(self.hits)},
                "timings_ms": {"total": 1}}


class FakeChecker:
    def __init__(self, verdict="insufficient", available=True):
        self.verdict = verdict
        self.available = available
        self.calls = 0

    def check(self, question, hits):
        self.calls += 1
        return {"verdict": self.verdict, "reason": "fake", "missing": ["x"],
                "available": self.available}


DATE_HITS = [{"document_id": "doc_a", "chunk_id": "doc_a#0000", "app_id": 1203220,
              "title": "11 月 5 日更新公告", "score": 0.62,
              "content": "The servers will be suspended for maintenance from Nov 4th, "
                         "2026 23:00 (UTC) to Nov 5th, 2026 04:00 (UTC)."}]
DATE_QUESTION = "11月5日更新维护时间窗口是几点？"


class WirelessIntegrationTest(unittest.TestCase):
    def test_semantic_insufficient_blocks_generation(self):
        checker = FakeChecker("insufficient")
        service = AnswerService(FakeRag(DATE_HITS), provider=None, checker=checker)
        result = service.answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["reason"], "semantic_insufficient")
        self.assertEqual(result["evidence"]["layer"], "program+semantic")
        self.assertEqual(checker.calls, 1)

    def test_conflicting_evidence_has_its_own_status(self):
        service = AnswerService(FakeRag(DATE_HITS), provider=None,
                                checker=FakeChecker("conflicting"))
        result = service.answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "conflicting_evidence")

    def test_unavailable_checker_does_not_block(self):
        """复核不可用不能变成拒答，否则模型一挂就全站拒答。"""
        service = AnswerService(FakeRag(DATE_HITS), provider=None,
                                checker=FakeChecker("undetermined", available=False))
        result = service.answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "generation_failed")   # 仅因没有 provider
        self.assertEqual(result["evidence"]["layer"], "program")

    def test_program_layer_refusal_never_calls_model(self):
        """程序层判缺必要元素时，不该再花一次模型调用去「求情」。"""
        checker = FakeChecker("sufficient")
        hits = [{"document_id": "d", "chunk_id": "d#0", "title": "t",
                 "score": 0.5, "content": "nothing useful"}]
        service = AnswerService(FakeRag(hits), provider=None, checker=checker)
        result = service.answer(1203220, DATE_QUESTION)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(checker.calls, 0, "程序层已拒答，却又调用了模型")


if __name__ == "__main__":
    unittest.main(verbosity=2)
