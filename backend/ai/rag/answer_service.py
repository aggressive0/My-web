"""
问答编排（Phase 6.3 P1）：路由 → 检索 → 证据门 → 生成 → 引用校验。

为什么单独一个模块，而不是塞进 KnowledgeService：
检索必须能在 Ollama 挂掉时照常工作（embedding 跑在 CPU）。把生成塞进检索服务，
「检索可用」和「生成可用」会重新耦合，6.2 特意拆开的东西又粘回去。

状态集合（前六个来自 Phase 6.3 文档，后两个是补充，理由写在下面）：
    answered                  证据充分且生成了带引用的回答
    insufficient_evidence     证据门判定缺必要元素
    ambiguous_query           问题不明确，应追问
    out_of_scope              超出系统能力
    route_to_sql              需要动态数据查询
    retrieval_failed          检索侧异常
    strategy_generation_pending  ← 补充：路由到策略，但生成器属 6.5
    generation_failed          ← 补充：证据够但生成失败

补充状态的理由：用 `strategy_generated` 表示「还没生成」是谎报；
把生成失败归到 `retrieval_failed` 会把人引去查检索，排查方向直接错。
"""

import logging
import re

from pydantic import ValidationError

from ..llm.base import LLMError
from .answer_schema import GeneratedAnswer
from .evidence_gate import STATUS_SUFFICIENT, EvidenceGate
from .intent import (AMBIGUOUS, OUT_OF_SCOPE, SQL_ANALYTICS, STRATEGY,
                     route)

logger = logging.getLogger(__name__)

STATUS_ANSWERED = "answered"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_CONFLICTING = "conflicting_evidence"
STATUS_AMBIGUOUS = "ambiguous_query"
STATUS_OUT_OF_SCOPE = "out_of_scope"
STATUS_ROUTE_TO_SQL = "route_to_sql"
STATUS_RETRIEVAL_FAILED = "retrieval_failed"
STATUS_STRATEGY_PENDING = "strategy_generation_pending"
STATUS_STRATEGY_GENERATED = "strategy_generated"
STATUS_GENERATION_FAILED = "generation_failed"

SYSTEM_PROMPT = (
    "你是游戏运营知识助手。只能依据给定证据回答，不得使用自身知识补足证据里没有的事实。"
    "answer 不得为空：证据不足时也必须写一句说明缺什么，并把 insufficient 设为 true。"
    "citations 只能填证据编号（如 E1），不得编造；没有可用证据时留空数组。"
    "只输出 JSON，不要输出解释或代码块："
    "{\"answer\": \"...\", \"citations\": [\"E1\"], \"insufficient\": false}"
)


def build_prompt(question, hits):
    """把候选拼成带编号的证据块。编号由我们生成，模型只能引用编号。"""
    lines = ["问题：%s" % question, "", "证据："]
    for index, hit in enumerate(hits, start=1):
        lines.append("[E%d] 标题：%s" % (index, (hit.get("title") or "")[:60]))
        lines.append((hit.get("content") or "")[:600])
        lines.append("")
    return "\n".join(lines)


class AnswerService:
    def __init__(self, rag, provider=None, gate=None, max_evidence=5, checker=None,
                 strategy=None):
        self.rag = rag
        self.provider = provider
        self.gate = gate or EvidenceGate()
        # 策略生成器（6.5）。注入而不是在这里 new：装配逻辑统一在 recall.factory，
        # 否则 /answer 与 /api/recall/generate 会各写一遍参数，迟早分叉。
        self.strategy = strategy
        # 送给模型的证据条数，与召回深度（RAG_TOP_K=50）是两件事：
        # 召回给够让证据门有得挑，上下文只放 5 条——实测 4B 模型在 10 条长证据下
        # 会退化成输出 "..."，5 条是能稳住的上限（配置项 LLM_MAX_EVIDENCE）。
        self.max_evidence = max(1, int(max_evidence))
        # 第二层：只有 provider 可用时才建；不可用就跳过，并在 evidence 里如实标记
        self.checker = checker

    # ------------------------------------------------------------------ 入口
    def answer(self, app_id, question, top_k=None):
        route_result = route(question)
        intent = route_result.intent
        base = {"intent": intent, "route": route_result.to_dict()}

        if intent == OUT_OF_SCOPE:
            return dict(base, status=STATUS_OUT_OF_SCOPE,
                        answer="这个问题超出本系统的能力范围（只处理 Steam 公开数据与游戏运营知识）。",
                        reason="out_of_scope_question")
        if intent == AMBIGUOUS:
            return dict(base, status=STATUS_AMBIGUOUS,
                        answer="问题里缺少明确对象或指标。请补充：哪款游戏、看什么指标、时间范围。",
                        reason="ambiguous_query")
        if intent == SQL_ANALYTICS:
            return dict(base, status=STATUS_ROUTE_TO_SQL, answer=None,
                        reason="needs_dynamic_metrics_not_knowledge_base")
        if intent == STRATEGY:
            return self._generate_strategy(base, app_id)

        try:
            retrieval = self.rag.search(app_id, question, top_k=top_k)
        except Exception as exc:
            return dict(base, status=STATUS_RETRIEVAL_FAILED, answer=None,
                        reason="retrieval_error", detail=type(exc).__name__)

        hits = retrieval.get("hits") or []
        gate_result = self.gate.evaluate(question, hits)
        evidence = gate_result.to_dict()

        # 只有「缺必要元素」才拒答。
        # undetermined（mixed 形态）表示程序层没有可判定的约束，**不等于证据不足**——
        # 把它当拒答会把一半的开放问题直接砍掉。这种情况放行到生成，
        # 由模型自报 insufficient + 引用校验来兜底。
        if gate_result.status == STATUS_INSUFFICIENT:
            return dict(base, status=STATUS_INSUFFICIENT, answer=None,
                        evidence=evidence,
                        citations=[],
                        retrieval_summary=_summary(retrieval),
                        reason=gate_result.reason or "insufficient_evidence")

        # 第二层：程序层拦不住的「形态齐备但答非所问」交给模型复核。
        # 只收紧不放宽——程序层已经判 insufficient 的分支在上面就返回了。
        if self.checker is not None:
            # 复核自己决定看几条（默认 5 条×300 字）；生成那边仍用 max_evidence=3。
            semantic = self.checker.check(question, hits)
            evidence["semantic"] = semantic
            evidence["layer"] = "program+semantic" if semantic.get("available") else "program"
            if semantic.get("verdict") == "insufficient":
                return dict(base, status=STATUS_INSUFFICIENT, answer=None,
                            evidence=evidence, citations=[],
                            retrieval_summary=_summary(retrieval),
                            reason="semantic_insufficient",
                            detail=";".join(semantic.get("missing") or [])[:200]
                            or semantic.get("reason"))
            if semantic.get("verdict") == "conflicting":
                return dict(base, status=STATUS_CONFLICTING, answer=None,
                            evidence=evidence, citations=[],
                            retrieval_summary=_summary(retrieval),
                            reason="evidence_conflict", detail=semantic.get("reason"))

        return self._generate(base, question, hits, evidence, retrieval)

    # ------------------------------------------------------------ 策略生成
    def _generate_strategy(self, base, app_id):
        """策略走 6.5 的生成器：真实指标 + 知识证据 + 双校验 + 落库。

        生成器不可用时保持 `strategy_generation_pending`——它和
        `strategy_generated` 是两件事，混用会让调用方以为方案已经存在。
        """
        if self.strategy is None:
            return dict(base, status=STATUS_STRATEGY_PENDING, answer=None,
                        reason="strategy_generator_unavailable")

        result = self.strategy.generate(app_id, persist=True)
        status = result.get("status")

        if status == "generated":
            payload = result.get("strategy") or {}
            return dict(base, status=STATUS_STRATEGY_GENERATED,
                        answer=payload.get("summary"), strategy=payload,
                        strategy_id=result.get("strategy_id"),
                        evidence_summary=result.get("evidence_summary"),
                        knowledge_query=result.get("knowledge_query"),
                        reason="")
        if status == "insufficient_evidence":
            return dict(base, status=STATUS_INSUFFICIENT, answer=None,
                        reason=result.get("reason") or "no_dynamic_metrics",
                        data_metrics=result.get("data_metrics"))
        return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                    reason=result.get("reason") or "strategy_generation_failed",
                    detail=result.get("detail"))

    # ------------------------------------------------------------------ 生成
    def _generate(self, base, question, hits, evidence, retrieval):
        summary = _summary(retrieval)
        if self.provider is None:
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="llm_provider_unavailable")

        used = hits[:self.max_evidence]
        labels = {"E%d" % (i + 1): h for i, h in enumerate(used)}
        try:
            text = self.provider.generate(build_prompt(question, used),
                                          system_prompt=SYSTEM_PROMPT)
        except LLMError as exc:
            # 模型不可用（Ollama 未启动/超时/模型缺失）必须与「调用过程出异常」分开：
            # 都报 llm_call_failed 会让人去查请求参数，而真正的原因是服务没起。
            logger.warning("问答生成不可用 code=%s detail=%s", exc.code, exc.detail)
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="llm_error", detail=exc.code)
        except Exception as exc:
            logger.exception("问答生成异常：%r", exc)
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="llm_call_failed", detail=type(exc).__name__)

        from ..recall.validation import parse_output

        raw_preview = (text or "")[:400].replace("\n", " ")
        try:
            document = parse_output(
                text, done_reason=(self.provider.last_metrics or {}).get("done_reason"))
        except LLMError as exc:
            # 原始输出必须留在服务端：只回「generation_invalid」等于把故障变成黑箱
            logger.warning("问答输出解析失败 code=%s detail=%s raw=%r",
                           exc.code, exc.detail, raw_preview)
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="generation_invalid", detail=exc.code)

        try:
            generated = GeneratedAnswer.model_validate(document)
        except ValidationError as exc:
            logger.warning("问答输出不符合 Schema：%s | raw=%r",
                           exc.errors()[:3], raw_preview)
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="generation_schema_mismatch", detail="schema")

        # 先校验引用、再校验内容：伪造证据编号比空答案更严重，
        # 两者同时出现时必须报引用问题，否则会把完整性问题掩盖成内容问题。
        unknown = [label for label in generated.citations if label.upper() not in labels]
        if unknown:
            # 模型编了个不存在的证据编号：宁可失败，也不返回无法追溯的答案
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="citation_not_in_candidates", detail=",".join(unknown[:3]))

        # 占位符也算没答：实测模型会输出 "..."，若只判非空就会把垃圾当成功返回。
        # 去掉空白与标点后少于 6 个字符，不足以构成一句有效的回答。
        substance = re.sub(r"[\s\.．。…·,，;；:：!！?？\-—_/\\|]+", "", generated.answer)
        if len(substance) < 6:
            logger.warning("问答输出无实质内容 insufficient=%s raw=%r",
                           generated.insufficient, raw_preview)
            return dict(base, status=STATUS_GENERATION_FAILED, answer=None,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="empty_answer")

        if generated.insufficient:
            return dict(base, status=STATUS_INSUFFICIENT, answer=generated.answer,
                        evidence=evidence, citations=[], retrieval_summary=summary,
                        reason="model_declared_insufficient")

        citations = [_cite(labels[label.upper()], label.upper())
                     for label in generated.citations]
        return dict(base, status=STATUS_ANSWERED, answer=generated.answer,
                    evidence=evidence, citations=citations,
                    retrieval_summary=summary, reason="")


def _cite(hit, label):
    return {"label": label, "chunk_id": hit.get("chunk_id"),
            "document_id": hit.get("document_id"), "title": hit.get("title"),
            "score": hit.get("score")}


def _summary(retrieval):
    stats = retrieval.get("stats") or {}
    return {"hits": len(retrieval.get("hits") or []),
            "top_score": round((retrieval.get("hits") or [{}])[0].get("score") or 0, 4)
            if retrieval.get("hits") else None,
            "kept": stats.get("kept"), "candidates": stats.get("candidates"),
            "timings_ms": retrieval.get("timings_ms")}
