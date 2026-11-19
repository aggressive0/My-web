"""
检索器（Phase 6.2）。

职责边界：只做「查询 → 证据」，不生成任何自然语言结论。
生成留给 Phase 6.5，因为「检索到什么」和「模型据此说了什么」必须能分开追责。

三件必须留痕的事（方案 §6.1 明确要求记录）：
    查询文本、app_id、命中的 chunk、相似度、耗时、被过滤掉的原因。

还有一个容易被忽略的一致性检查：**孤儿向量**。
如果删文档时 Chroma 成功而 SQLite 失败（或反过来），检索会捞出
一个没有元数据的 chunk。这里逐条核对文档状态，把孤儿丢掉并计数，
而不是让它悄悄进入证据列表。
"""

import time

from . import ranking
from .vector_store import GLOBAL_APP_ID

DEFAULT_THRESHOLD = 0.35


class Retriever:
    def __init__(self, store, embedder, config, repository=None):
        self.store = store
        self.embedder = embedder
        self.config = config
        self.repository = repository

    def search(self, app_id, query, top_k=None, threshold=None, include_global=True):
        query = (query or "").strip()
        if not query:
            raise ValueError("查询文本不能为空")

        top_k = int(top_k or self.config.top_k)
        # None = 不做召回过滤。Phase 6.3 起「能不能回答」不再由分数决定，
        # 阈值只剩「过滤明显不相关的候选」这一个用途，且默认不启用。
        min_score = self.config.retrieval_min_score if threshold is None else float(threshold)

        started = time.time()
        vectors = self.embedder.encode([query])
        embed_ms = int((time.time() - started) * 1000)
        if not vectors:
            raise ValueError("查询向量化失败")

        search_started = time.time()
        hits = self.store.search(vectors[0], app_id=app_id, top_k=top_k,
                                 include_global=include_global)
        search_ms = int((time.time() - search_started) * 1000)

        # 新近度重排（6.3 遗留）：publish_date 之前在元数据里但从不参与排序，
        # 导致「最近更新」类问题排上来的是旧公告。权重默认 0 = 不改变行为。
        recency_applied = bool(self.config.recency_weight)
        if recency_applied:
            hits = ranking.apply_recency(hits, self.config.recency_weight,
                                         self.config.recency_tau_days)

        if min_score is None:
            kept, dropped_by_score = list(hits), []
        else:
            kept = [h for h in hits if h.score >= min_score]
            dropped_by_score = [h for h in hits if h.score < min_score]

        dropped_orphan = 0
        if kept and self.repository is not None:
            alive = self.repository.active_document_ids({h.document_id for h in kept})
            before = len(kept)
            kept = [h for h in kept if h.document_id in alive]
            dropped_orphan = before - len(kept)

        # 空结果的三种成因必须分开报：把「全被孤儿过滤掉」说成「低于阈值」，
        # 会让排查方向彻底跑偏（而且 min_score 可能是 None，直接格式化会崩）。
        if kept:
            empty_reason = None
        elif not hits:
            empty_reason = "没有检索到任何 chunk"
        elif dropped_orphan:
            empty_reason = "命中的结果全部是孤儿向量（文档已被删除或状态非 active）"
        else:
            empty_reason = "命中的结果全部低于召回下限 %.2f" % min_score

        return {
            "app_id": app_id,
            "query": query,
            "top_k": top_k,
            "threshold": min_score,
            "include_global": include_global,
            "hits": [h.to_dict() for h in kept],
            "recency_applied": recency_applied,
            "stats": {
                "candidates": len(hits),
                "kept": len(kept),
                "dropped_by_score": len(dropped_by_score),
                "dropped_orphan": dropped_orphan,
                "lowest_kept_score": (round(kept[-1].score, 4) if kept else None),
                "highest_dropped_score": (round(max(h.score for h in dropped_by_score), 4)
                                          if dropped_by_score else None),
            },
            "timings_ms": {
                "embedding": embed_ms,
                "vector_search": search_ms,
                "total": int((time.time() - started) * 1000),
            },
            # 检索不再回答「有没有证据」这个问题——那是 EvidenceGate 的职责。
            # 保留这个字段会让调用方继续用「有结果 = 可回答」做判断，正是 6.3 要拆掉的东西。
            "retrieval_empty": not kept,
            "empty_reason": empty_reason,
            "global_app_id_marker": GLOBAL_APP_ID,
        }
