"""
模型输出 → 结构体（T3/T4/T8 的落点）。

判定顺序是刻意的，顺序错了就会把不同故障混成一类：

    空文本 ─┬─ done_reason=length → LLM_TRUNCATED（预算被思考烧光）
            └─ 其他             → INVALID_OUTPUT（模型什么都没说）
    JSON   ─┬─ 解析失败 + length → LLM_TRUNCATED
            ├─ 解析失败 + 其他    → INVALID_JSON（模型没按 JSON 输出）
            └─ 解析成功          → Schema 校验
    Schema ─┬─ 失败              → SCHEMA_MISMATCH（带字段路径）
            └─ 成功              → app_id 校验 → 证据校验 → 通过

**`done_reason=length` 本身不是失败判据。** 实测 `num_predict=16` 时
done_reason=length 而 JSON 仍然完整合法；按字面拒绝会误杀合法结果。
真正判据是「截断 **且** 解析失败」。

但 `truncated_but_valid=True` 也不是「没事」：它意味着内容刚好闭合，
可能缺了后半段。调用方应当记录并告警，而不是当成功一样处理。
"""

import json

from pydantic import ValidationError

from ..llm.base import InvalidOutput, LLMError, LLMTruncated
from .schema import SCHEMA_VERSION, RecallStrategy


class InvalidJSON(LLMError):
    code = "INVALID_JSON"


class SchemaMismatch(LLMError):
    code = "SCHEMA_MISMATCH"


class AppIdMismatch(LLMError):
    code = "APP_ID_MISMATCH"


class EvidenceInvalid(LLMError):
    code = "EVIDENCE_INVALID"


def _summarize_pydantic(error):
    """把 Pydantic 的报错压成「字段: 原因」，最多三条。

    完整的 errors() 可能有几十条，回给前端没有意义；但只回「校验失败」
    又没法排查。折中成前三条带路径的摘要。
    """
    parts = []
    for item in error.errors()[:3]:
        loc = ".".join(str(x) for x in item.get("loc") or []) or "(root)"
        parts.append("%s: %s" % (loc, item.get("msg")))
    more = len(error.errors()) - 3
    if more > 0:
        parts.append("另有 %d 处错误" % more)
    return "；".join(parts)


def parse_output(text, done_reason=None):
    """只解析 JSON，不校验业务结构。失败抛 LLMTruncated / InvalidJSON / InvalidOutput。"""
    text = (text or "").strip()
    if not text:
        if done_reason == "length":
            raise LLMTruncated(
                "输出被截断：num_predict 预算耗尽且未产出任何内容"
                "（若开启 thinking，思考会先吃掉预算）")
        raise InvalidOutput("模型返回空内容")

    try:
        return json.loads(text)
    except ValueError as exc:
        if done_reason == "length":
            raise LLMTruncated(
                "输出被截断且无法解析为 JSON（num_predict 预算耗尽）",
                "parse_error=%s" % str(exc)[:160]) from exc
        raise InvalidJSON("模型输出不是合法 JSON", "parse_error=%s" % str(exc)[:160]) from exc


def validate_strategy(document, expected_app_id=None, allowed_chunk_ids=None,
                      min_actions=None, max_actions=None):
    """结构 + app_id + 证据三重校验。详情见模块 docstring 的判定顺序。

    `min_actions` / `max_actions` 是**业务约束**，来自 `STRATEGY_MIN_ACTIONS` 等配置，
    不写死在 Schema 里：Schema 只管「结构成立」，阈值属于策略口径，改口径不该改模型。
    实测模型只给 1 条措施时会「结构合法但不达标」，这两件事必须分开报。
    """
    if not isinstance(document, dict):
        raise SchemaMismatch("策略根节点必须是 JSON 对象，实际是 %s"
                             % type(document).__name__)
    try:
        strategy = RecallStrategy.model_validate(document)
    except ValidationError as exc:
        raise SchemaMismatch("输出不符合召回策略 Schema：%s"
                             % _summarize_pydantic(exc)) from exc

    count = len(strategy.recall_actions)
    if min_actions is not None and count < int(min_actions):
        raise SchemaMismatch("措施数量不足：要求至少 %d 条，实际 %d 条"
                             % (int(min_actions), count))
    if max_actions is not None and count > int(max_actions):
        raise SchemaMismatch("措施数量超限：要求最多 %d 条，实际 %d 条"
                             % (int(max_actions), count))

    if expected_app_id is not None and strategy.app_id != expected_app_id:
        raise AppIdMismatch("策略的 app_id=%s 与请求的 app_id=%s 不一致"
                            % (strategy.app_id, expected_app_id))

    if allowed_chunk_ids is not None:
        allowed = set(allowed_chunk_ids)
        unknown = [item.chunk_id for item in strategy.knowledge_evidence
                   if item.chunk_id not in allowed]
        if unknown:
            raise EvidenceInvalid(
                "策略引用了检索结果中不存在的 chunk：%s" % ", ".join(unknown[:5]),
                "allowed=%d 条" % len(allowed))

    return strategy


def parse_and_validate(text, done_reason=None, expected_app_id=None,
                       allowed_chunk_ids=None):
    """解析 + 校验一次做完，返回 (strategy, report)。

    report 里保留 `truncated_but_valid`：即使校验通过，也要让调用方知道
    这次结果是「贴着截断线」产出的，不应当与干净结果等价对待。
    """
    document = parse_output(text, done_reason=done_reason)
    strategy = validate_strategy(document, expected_app_id=expected_app_id,
                                 allowed_chunk_ids=allowed_chunk_ids)
    report = {
        "schema_version": SCHEMA_VERSION,
        "done_reason": done_reason,
        "truncated_but_valid": done_reason == "length",
        "evidence_count": len(strategy.knowledge_evidence),
        "action_count": len(strategy.recall_actions),
    }
    return strategy, report
