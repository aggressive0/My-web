"""
Phase 6.2 T3/T4/T8：召回策略 Schema 与输出校验的测试。

这些测试保护的是「失败必须被分类正确」：
截断、非法 JSON、Schema 不符、app_id 不符、证据非法，五种故障的处置完全不同，
混成一句「生成失败」等于放弃排查能力。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase6_recall -v
"""

import json
import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.llm.base import InvalidOutput, LLMTruncated       # noqa: E402
from ai.recall.schema import RecallStrategy              # noqa: E402
from ai.recall.validation import (AppIdMismatch,         # noqa: E402
                                  EvidenceInvalid, InvalidJSON,
                                  SchemaMismatch, parse_and_validate,
                                  parse_output, validate_strategy)


def sample_strategy(**overrides):
    base = {
        "app_id": 1203220,
        "game_name": "永劫无间",
        "execution_status": "suggested",
        "summary": "近期匹配相关负面反馈集中，建议优先做沟通与说明",
        "risk_analysis": {
            "risk_level": "high",
            "observed_signals": [{"metric": "negative_review_ratio", "value": 0.42,
                                  "source": "sqlite"}],
            "possible_causes": ["近期更新可能影响匹配体验"],
        },
        "data_evidence": [{"name": "negative_review_ratio", "value": 0.42,
                           "description": "近 7 天差评比例", "source": "sqlite"}],
        "knowledge_evidence": [{
            "document_id": "doc_abc", "chunk_id": "doc_abc#0001",
            "title": "11 月 5 日更新公告", "source_name": "Steam 官方公告",
            "source_url": None, "claim": "公告说明修复了跨服匹配超时",
        }],
        "ai_inference": [{"reasoning": "负面评价与更新时间接近，仅相关不构成因果",
                          "confidence": "medium", "limitations": "公开数据不足以证明因果"}],
        "recall_actions": [{
            "name": "发布匹配机制说明", "target_group": "近期评论中表达匹配不满的群体",
            "objective": "降低误解", "action_type": "community_operation",
            "priority": "high", "description": "在官方社区说明匹配逻辑",
            "expected_effect": "负面评论增速放缓",
            "measurement": ["负面评论比例", "同时在线人数"],
        }],
        "limitations": ["本方案基于公开数据，不具备个人玩家识别能力"],
    }
    base.update(overrides)
    return base


class SchemaTest(unittest.TestCase):
    def test_valid_strategy_passes(self):
        strategy = RecallStrategy.model_validate(sample_strategy())
        self.assertEqual(strategy.execution_status, "suggested")
        self.assertEqual(strategy.app_id, 1203220)

    def test_execution_status_cannot_pretend_executed(self):
        """只允许 suggested：本系统没有触达通道，其他取值等于谎称已执行。"""
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(sample_strategy(execution_status="executed"))

    def test_extra_field_is_rejected(self):
        """模型自己加字段说明没按契约输出，应当失败。"""
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(sample_strategy(unexpected_field="x"))

    def test_negative_app_id_rejected(self):
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(sample_strategy(app_id=-1))

    def test_actions_cannot_be_empty_or_unbounded(self):
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(sample_strategy(recall_actions=[]))
        many = [sample_strategy()["recall_actions"][0]] * 6
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(sample_strategy(recall_actions=many))

    def test_knowledge_evidence_requires_chunk_id(self):
        """没有 chunk_id 的引用无法回溯原文，证据链就断了。"""
        broken = sample_strategy()
        del broken["knowledge_evidence"][0]["chunk_id"]
        with self.assertRaises(Exception):
            RecallStrategy.model_validate(broken)


class ParseTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_output('{"ok": true}', done_reason="stop"), {"ok": True})

    def test_empty_without_length_is_invalid_output(self):
        with self.assertRaises(InvalidOutput):
            parse_output("   ", done_reason="stop")

    def test_empty_with_length_is_truncated(self):
        with self.assertRaises(LLMTruncated):
            parse_output("", done_reason="length")

    def test_bad_json_without_length_is_invalid_json(self):
        with self.assertRaises(InvalidJSON):
            parse_output("这不是 JSON", done_reason="stop")

    def test_bad_json_with_length_is_truncated(self):
        """用户确认的判据：length **且** 解析失败才算截断。"""
        with self.assertRaises(LLMTruncated):
            parse_output('{"app_id": 1203220, "summary": "截', done_reason="length")

    def test_length_but_valid_json_is_accepted(self):
        doc = parse_output('{"ok": true}', done_reason="length")
        self.assertEqual(doc, {"ok": True})


class ValidateTest(unittest.TestCase):
    def test_schema_mismatch_reports_field_path(self):
        broken = sample_strategy()
        del broken["summary"]
        with self.assertRaises(SchemaMismatch) as ctx:
            validate_strategy(broken)
        self.assertIn("summary", str(ctx.exception))

    def test_app_id_mismatch(self):
        with self.assertRaises(AppIdMismatch):
            validate_strategy(sample_strategy(), expected_app_id=570)

    def test_unknown_chunk_is_rejected(self):
        with self.assertRaises(EvidenceInvalid) as ctx:
            validate_strategy(sample_strategy(),
                              allowed_chunk_ids={"doc_other#0001"})
        self.assertIn("doc_abc#0001", str(ctx.exception))

    def test_known_chunk_passes(self):
        strategy = validate_strategy(sample_strategy(),
                                     allowed_chunk_ids={"doc_abc#0001"})
        self.assertEqual(len(strategy.knowledge_evidence), 1)

    def test_no_allowed_set_skips_evidence_check(self):
        """未提供检索结果时不做证据校验——不能假装校验过了。"""
        strategy = validate_strategy(sample_strategy(), allowed_chunk_ids=None)
        self.assertEqual(strategy.app_id, 1203220)


class EndToEndTest(unittest.TestCase):
    def test_clean_run(self):
        text = json.dumps(sample_strategy(), ensure_ascii=False)
        strategy, report = parse_and_validate(
            text, done_reason="stop", expected_app_id=1203220,
            allowed_chunk_ids={"doc_abc#0001"})
        self.assertEqual(strategy.summary[:2], "近期")
        self.assertFalse(report["truncated_but_valid"])
        self.assertEqual(report["evidence_count"], 1)
        self.assertEqual(report["schema_version"], "recall_strategy/v1")

    def test_truncated_but_valid_is_flagged(self):
        """贴着截断线产出但恰好合法：可用，但必须留痕，不能与干净结果等价。"""
        text = json.dumps(sample_strategy(), ensure_ascii=False)
        _, report = parse_and_validate(text, done_reason="length")
        self.assertTrue(report["truncated_but_valid"])

    def test_truncated_invalid_raises(self):
        with self.assertRaises(LLMTruncated):
            parse_and_validate('{"app_id": 1203220, "summ', done_reason="length")


if __name__ == "__main__":
    unittest.main(verbosity=2)
