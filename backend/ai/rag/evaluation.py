"""
检索评估指标（Phase 6.3）。

纯函数，不碰模型、不碰数据库——这样指标本身能被单测完全覆盖。
如果指标代码没有被独立测试，评估结果只是另一串没人验算过的数字。

口径（写死在这里，改口径必须改本注释并重跑历史）：

1. **文档级，不是 chunk 级**。同一文档的多个 chunk 只算一次。
   chunk 切得越细，chunk 级 Recall@5 越虚高，而用户真正问的是
   「有没有找到那份公告」。文档级更严格，也更接近业务含义。
2. **Recall@K 支持多标签**：标注 2 份文档命中 1 份记 0.5，不是 0/1。
   否则「一份公告拆成两篇发」会平白判错。
3. **Precision@K 的分母固定为 K**，不是实际返回条数。
   分母随返回条数变，会让「只返回 1 条且正确」的得分高于
   「返回 5 条命中 4 条」，方向是错的。
4. **MRR 只看第一个命中**，没有命中记 0。
5. **隔离违规**指结果里出现了既不属于该 app_id、也不是 global 的文档。
   它不计入召回，单独统计为失败项——这是硬约束，不是软指标。
"""

GLOBAL_APP_ID = -1


def _doc_id(hit):
    return hit.get("document_id") if isinstance(hit, dict) else hit.document_id


def _app_id(hit):
    return hit.get("app_id") if isinstance(hit, dict) else hit.app_id


def _score(hit):
    return hit.get("score") if isinstance(hit, dict) else hit.score


def rank_documents(hits):
    """按分数降序去重成文档序列（保持首次出现的名次）。"""
    seen, ranked = set(), []
    for hit in sorted(hits, key=_score, reverse=True):
        doc_id = _doc_id(hit)
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            ranked.append(doc_id)
    return ranked


def recall_at_k(expected_docs, ranked_docs, k=5, match="fraction"):
    """match="fraction"：多标签按命中比例；match="any"：命中任一即算 1.0。

    为什么需要 "any"：同一段内容常被当成多篇公告反复发布（Steam 上的
    封禁名单每周一份、模板完全一致）。若把 14 份等价文档都标进 expected
    再按比例算，命中 5 份只能得 5/14——这不叫严格，这叫把「重复发布」
    当成「多种正确答案」。等价族必须用 any 口径，并且要在题目里显式声明。
    """
    expected = set(expected_docs or [])
    if not expected:
        return None                     # 无标注（不可回答题）不参与召回统计
    top = set(ranked_docs[:k])
    hits = expected & top
    if match == "any":
        return 1.0 if hits else 0.0
    return len(hits) / float(len(expected))


def precision_at_k(expected_docs, ranked_docs, k=5):
    expected = set(expected_docs or [])
    if not expected:
        return None
    top = ranked_docs[:k]
    return len(expected & set(top)) / float(k)


def reciprocal_rank(expected_docs, ranked_docs):
    expected = set(expected_docs or [])
    if not expected:
        return None
    for index, doc_id in enumerate(ranked_docs, start=1):
        if doc_id in expected:
            return 1.0 / index
    return 0.0


def isolation_violations(hits, app_id):
    """返回越界命中的数量（既非本游戏、也非全局文档）。"""
    bad = 0
    for hit in hits:
        hit_app = _app_id(hit)
        if hit_app == app_id or hit_app == GLOBAL_APP_ID:
            continue
        bad += 1
    return bad


def apply_threshold(hits, threshold):
    return [h for h in hits if _score(h) >= threshold]


def summarize(rows, k=5):
    """rows: 每题一条 {recall, precision, rr, violation, refused, answerable}。

    平均值只对「有定义的行」求：不可回答题没有 recall，不能当 0 拉低均值。
    这是最容易糊弄自己的地方——把拒答题按 0 计入召回，指标会莫名其妙变差，
    然后有人去调阈值「修复」它。
    """
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def mean(values):
        values = [v for v in values if v is not None]
        return (sum(values) / len(values)) if values else None

    return {
        "total": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "recall_at_k": mean([r["recall"] for r in answerable]),
        "precision_at_k": mean([r["precision"] for r in answerable]),
        "mrr": mean([r["rr"] for r in answerable]),
        "isolation_violations": sum(r["violation"] for r in rows),
        "refusal_accuracy": (mean([1.0 if r["refused"] else 0.0 for r in unanswerable])
                             if unanswerable else None),
        "answered_when_unanswerable": sum(1 for r in unanswerable if not r["refused"]),
        "k": k,
    }
