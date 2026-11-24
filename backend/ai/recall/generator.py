"""
召回策略生成（Phase 6.5）。

流程：动态指标 → 知识证据 → Prompt → 生成 → Schema 校验 → **数据与引用双校验** → 落库。

两条不能被绕过的校验：

1. **数据证据必须与本地算出的值一致。** 模型可以复述我们给的数字，不能自己造。
   实测生成模型很爱把「负面占比」写成整数百分比或四舍五入到 0.5，
   看起来合理，但那是它编的，不是统计结果。名字对不上或数值不等一律判失败。

2. **知识证据的 chunk_id 必须来自本次检索。** 复用 6.2 的 `allowed_chunk_ids`。

失败不落库：空策略进历史列表，会让人误以为「生成过」。
"""

import json

from ..llm.base import LLMError
from .schema import RecallStrategy
from .signals import build_knowledge_query
from .validation import (AppIdMismatch, EvidenceInvalid, InvalidJSON,
                         SchemaMismatch, parse_output, validate_strategy)

STATUS_GENERATED = "generated"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_FAILED = "generation_failed"

# 检索查询不再用常量：由指标方向 + 近期差评高频词生成（见 signals.py）。
# 常量会导致「CCU 在跌，却去检索两个月前的活动设计公告」。

SYSTEM_PROMPT = (
    "你是游戏运营分析师。只能使用「动态数据」里给出的数字和「知识证据」里的内容。"
    "不得编造任何数值、公告标题或链接。"
    "data_evidence 只能原样复述动态数据里的条目（name 与 value 必须完全一致）。"
    "knowledge_evidence 的 chunk_id 只能取自证据编号对应的条目。"
    "execution_status 必须固定为 suggested（本系统只给建议，不执行）。"
    "只输出 JSON，不要解释或代码块。"
)

SCHEMA_HINT = """{
 "app_id": <整数，必须等于给定的 app_id>,
 "execution_status": "suggested",
 "summary": "一句话结论",
 "risk_analysis": {"risk_level": "high|medium|low|unknown",
                   "observed_signals": [{"metric": "指标名", "value": <数值>, "source": "sqlite"}],
                   "possible_causes": ["可能原因"]},
 "data_evidence": [{"name": "必须与动态数据里的 name 完全一致", "value": <同一个数值>,
                    "description": "", "source": "sqlite"}],
 "knowledge_evidence": [{"chunk_id": "只填证据编号，例如 E1（不要填正文或标题）",
                         "claim": "该证据支持了什么"}],
 "ai_inference": [{"reasoning": "", "confidence": "low|medium|high", "limitations": ""}],
 "recall_actions": [{"name": "", "target_group": "", "objective": "", "action_type": "",
                     "priority": "high|medium|low", "description": "",
                     "expected_effect": "", "measurement": ["监测指标"]}],
 "limitations": ["本次分析的限制"]
}"""


def _fmt_metric(item):
    return "- %s = %s（%s；截止 %s）" % (
        item.get("name"), item.get("value"), item.get("description") or "",
        item.get("as_of") or "-")


def build_prompt(app_id, game_name, metrics, hits, min_actions=1, max_actions=5):
    lines = [
        "游戏：%s（app_id=%d）" % (game_name or "未知", app_id),
        "",
        "动态数据（由本地 SQLite 统计得出，禁止改写数值）：",
    ]
    for item in metrics.get("evidence") or []:
        lines.append(_fmt_metric(item))
    for item in metrics.get("missing") or []:
        lines.append("- 不可用指标：%s（%s）" % (item.get("name"), item.get("reason")))

    lines += ["", "知识证据："]
    if hits:
        for index, hit in enumerate(hits, start=1):
            lines.append("[E%d] %s" % (index, (hit.get("title") or "")[:60]))
            lines.append((hit.get("content") or "")[:600])
            lines.append("")
    else:
        lines.append("（本次没有检索到可用知识证据，必须在 limitations 里说明）")

    lines += [
        "",
        # 数量要求必须写进提示词：只放在校验里，模型会稳定地只给 1 条，
        # 然后每次都被判失败——那是把「没说清」当成「模型不行」。
        "recall_actions 必须给 %d–%d 条，且彼此不同（不要同一件事拆成多条）。"
        % (min_actions, max_actions),
        "",
        "输出结构（严格遵守键名）：", SCHEMA_HINT,
    ]
    return "\n".join(lines)


def resolve_evidence_refs(document, hits):
    """把模型写的证据编号（E1…）换成真实 chunk_id / document_id / 标题。

    不让模型直接填 chunk_id：实测它会填正文片段（"五、已知问题"），
    而编号只有一位字母加数字，写错的概率低得多；映射与补全由程序做，
    模型也就无法伪造 document_id 或标题。
    """
    labels = {"E%d" % (i + 1): hit for i, hit in enumerate(hits)}
    for item in document.get("knowledge_evidence") or []:
        ref = str(item.get("chunk_id") or "").strip().upper()
        hit = labels.get(ref)
        if not hit:
            continue
        item["chunk_id"] = hit.get("chunk_id")
        item["document_id"] = hit.get("document_id")
        item["title"] = hit.get("title") or ""
        item["source_name"] = hit.get("source_name") or "本地知识库"
        item["source_url"] = hit.get("source_url")
    return document


def verify_data_evidence(model_items, computed_items):
    """模型复述的数值必须与本地统计完全一致。返回问题列表（空=通过）。"""
    known = {item["name"]: item.get("value") for item in computed_items}
    problems = []
    for item in model_items or []:
        name = item.get("name")
        if name not in known:
            problems.append("unknown_metric:%s" % name)
            continue
        expected, actual = known[name], item.get("value")
        if expected is None and actual is None:
            continue
        try:
            if abs(float(expected) - float(actual)) > max(1e-6, abs(float(expected)) * 1e-6):
                problems.append("value_mismatch:%s(%s!=%s)" % (name, actual, expected))
        except (TypeError, ValueError):
            problems.append("value_not_numeric:%s" % name)
    return problems


class StrategyGenerator:
    def __init__(self, provider, rag, collector, store=None, max_knowledge=3,
                 min_actions=1, max_actions=5):
        self.provider = provider
        self.rag = rag
        self.collector = collector
        self.store = store
        self.max_knowledge = max(1, int(max_knowledge))
        # 业务约束（STRATEGY_MIN_ACTIONS / STRATEGY_MAX_ACTIONS）。
        # 之前只写在 config 里、没有任何调用方，等于没配；现在接进校验与提示词。
        self.min_actions = max(1, int(min_actions))
        self.max_actions = max(self.min_actions, int(max_actions))

    def generate(self, app_id, game_name=None, top_k=5, persist=True):
        # 调用方不传就自己查，不要让「未知」这个占位词进入提示词——
        # 模型看到「游戏：未知」会开始编一个泛化的方案。
        if not game_name:
            game_name = self.collector.game_name(app_id)
        metrics = self.collector.collect(app_id)
        base = {"app_id": app_id, "game_name": game_name,
                "data_metrics": metrics}

        if not metrics["evidence"]:
            return dict(base, status=STATUS_INSUFFICIENT, strategy=None,
                        reason="no_dynamic_metrics",
                        detail="该游戏没有任何可用的 CCU 或评论数据")

        # 查询词跟着信号走：差评高频词 + 指标方向
        texts, window_days = [], 30
        try:
            texts = self.collector.recent_negative_texts(app_id, days=30)
            # 30 天内差评太少时放宽到 90 天，并如实记录用了哪个窗口。
            # 实测 NARAKA 30 天内只有 1 条差评，硬用会让整个信号机制空转。
            if len(texts) < 2:
                widened = self.collector.recent_negative_texts(app_id, days=90)
                if len(widened) > len(texts):
                    texts, window_days = widened, 90
        except Exception:                       # 取不到评论不是致命错误
            texts, window_days = [], 0
        query, basis = build_knowledge_query(metrics, texts)
        basis["review_window_days"] = window_days
        basis["review_samples"] = len(texts)
        base["knowledge_query"] = query
        base["knowledge_query_basis"] = basis

        try:
            retrieval = self.rag.search(app_id, query, top_k=top_k)
            hits = (retrieval.get("hits") or [])[:self.max_knowledge]
        except Exception as exc:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="retrieval_error", detail=type(exc).__name__)

        allowed = [h.get("chunk_id") for h in hits if h.get("chunk_id")]
        base["knowledge_hits"] = len(hits)
        # 把「模型看到的材料」原样带回并落库：前端要展示 RAG 命中了哪几篇、
        # 相似度多少、原文片段是什么。只给一个数量，等于让用户相信而不核对。
        base["retrieved_evidence"] = [
            {"chunk_id": h.get("chunk_id"), "document_id": h.get("document_id"),
             "title": h.get("title") or "", "score": round(float(h.get("score") or 0), 4),
             "publish_date": h.get("publish_date") or "",
             "source_url": h.get("source_url") or "",
             "excerpt": (h.get("content") or "")[:180]}
            for h in hits
        ]

        if self.provider is None:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="llm_provider_unavailable")

        try:
            text = self.provider.generate(
                build_prompt(app_id, game_name, metrics, hits,
                             min_actions=self.min_actions, max_actions=self.max_actions),
                system_prompt=SYSTEM_PROMPT)
        except LLMError as exc:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="llm_error", detail=exc.code)
        except Exception as exc:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="llm_call_failed", detail=type(exc).__name__)

        done_reason = (self.provider.last_metrics or {}).get("done_reason")
        try:
            document = parse_output(text, done_reason=done_reason)
        except LLMError as exc:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="output_unusable", detail=exc.code)
        if isinstance(document, dict):
            document = resolve_evidence_refs(document, hits)
        try:
            strategy = validate_strategy(document, expected_app_id=app_id,
                                        allowed_chunk_ids=allowed,
                                        min_actions=self.min_actions,
                                        max_actions=self.max_actions)
        except (SchemaMismatch, AppIdMismatch, InvalidJSON, EvidenceInvalid) as exc:
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="validation_failed", detail="%s: %s" % (exc.code, exc.message))

        problems = verify_data_evidence(strategy.model_dump().get("data_evidence"),
                                        metrics["evidence"])
        if problems:
            # 数值对不上就是编的，不能存
            return dict(base, status=STATUS_FAILED, strategy=None,
                        reason="data_evidence_mismatch", detail="; ".join(problems[:5]))

        payload = strategy.model_dump()
        strategy_id = None
        if persist and self.store is not None:
            strategy_id = self.store.save(
                payload, model=type(self.provider).__name__, game_name=game_name,
                # 查询词与命中一起存：只看命中、不知道「为什么是这几篇」，没法复核
                retrieved={"query": query,
                           "hits": base.get("retrieved_evidence") or []})

        return dict(base, status=STATUS_GENERATED, strategy=payload,
                    strategy_id=strategy_id,
                    evidence_summary={
                        "data_evidence": len(payload.get("data_evidence") or []),
                        "knowledge_evidence": len(payload.get("knowledge_evidence") or []),
                        "actions": len(payload.get("recall_actions") or []),
                        "knowledge_available": len(allowed),
                    })
