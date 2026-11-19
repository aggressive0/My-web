"""Phase 6.3 检索评估：Recall@5 / Precision@5 / MRR / AppID 隔离 / 拒答准确率。

设计上的四条硬规则：

1. **标注先校验，再评估**。每个 expected_gid 的 `must_contain` 必须在公告原文里
   真的出现，否则脚本直接失败退出——宁可不出数字，也不出基于错标注的数字。
2. **在独立索引上评估**。语料导入临时目录（temp Chroma + temp SQLite），
   不污染开发用的知识库，也不用担心重复运行互相干扰。
3. **一次查询，多用阈值**。先取不带阈值的前 K 个候选，再按不同阈值重算指标。
   这样阈值扫描不重复烧 CPU，也不会因为 top_k 被截断而失真。
4. **拒答题不参与召回平均**。把「无答案题」按 0 计入召回，会让人误以为
   检索质量下降，然后去调阈值「修好」它。

用法：
    python scripts/evaluate_retrieval.py                  # 默认阈值 + 扫描
    python scripts/evaluate_retrieval.py --no-persist     # 不写评估记录
    python scripts/evaluate_retrieval.py --top-k 5 --sweep 0.3,0.35,0.4,0.45
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv                                     # noqa: E402

# load_dotenv 只写在 backend/app.py 里。脚本不自己加载就会拿到代码默认值
# （AI_ENABLED=false → 第二层语义复核被静默跳过）。这个坑已经踩过三次。
load_dotenv(os.path.join(ROOT, "backend", ".env"))

from ai.config import AIConfig                                    # noqa: E402
from ai.rag import evaluation, ranking                            # noqa: E402
from ai.rag.evidence_gate import (STATUS_INSUFFICIENT,            # noqa: E402
                                  STATUS_UNDETERMINED, EvidenceGate)
from ai.rag.intent import route as route_intent                   # noqa: E402
from ai.rag.knowledge_repository import KnowledgeRepository       # noqa: E402
from ai.rag.service import KnowledgeService                       # noqa: E402

QUESTIONS_PATH = os.path.join(ROOT, "tests", "fixtures", "rag_questions.json")
ARTIFACT_DIR = os.path.join(ROOT, "tests", "artifacts")


def load_questions(path=QUESTIONS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_corpus(corpus_dir):
    """返回 {appid: [item, ...]}，以及 gid -> item 的索引。"""
    by_app, by_gid = {}, {}
    for path in sorted(glob.glob(os.path.join(corpus_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        appid = doc["appid"]
        by_app[appid] = doc["items"]
        for item in doc["items"]:
            by_gid[str(item["gid"])] = dict(item, appid=appid)
    return by_app, by_gid


def validate_annotations(questions, corpus_by_gid):
    """机械校验标注。返回问题列表（直接抛异常，不吞）。"""
    problems = []
    for q in questions["questions"]:
        intent = q.get("intent", "kb_factual_qa")
        if intent != "kb_factual_qa":
            # SQL / 策略 / 越界 / 歧义 题不查知识库，没有可标注的文档
            if q.get("expected_gids"):
                problems.append("%s：非知识库题不应有 expected_gids（intent=%s）"
                                % (q["id"], intent))
            continue
        if not q.get("answerable", True):
            if q.get("expected_gids"):
                problems.append("%s：不可回答题不应有 expected_gids" % q["id"])
            continue
        if not q.get("expected_gids"):
            problems.append("%s：可回答题缺少 expected_gids" % q["id"])
            continue
        existing = []
        for gid in q["expected_gids"]:
            item = corpus_by_gid.get(gid)
            if not item:
                problems.append("%s：语料中不存在 gid=%s" % (q["id"], gid))
                continue
            if item["appid"] != q["app_id"]:
                problems.append("%s：gid=%s 属于 appid=%s，与题目 appid=%s 不符"
                                % (q["id"], gid, item["appid"], q["app_id"]))
            existing.append(item)
        # 多标签题的语义：每个 must_contain 至少命中**一份**标注文档即可。
        # 要求「每份文档都含每句证据」会把「一题多文档」逼成不可能满足的条件。
        for needle in q.get("must_contain") or []:
            if not any(needle in item["contents"] for item in existing):
                problems.append("%s：没有任何标注文档包含「%s」" % (q["id"], needle))
    if problems:
        raise SystemExit("标注校验失败（%d 条）：\n  - %s"
                         % (len(problems), "\n  - ".join(problems)))
    return questions["questions"]


def corpus_fingerprint(by_app):
    parts = []
    for appid in sorted(by_app):
        for item in by_app[appid]:
            parts.append("%s:%s" % (appid, item["gid"]))
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def build_index(tmpdir, questions):
    cfg = AIConfig().rag
    cfg.chroma_dir = os.path.join(tmpdir, "chroma")
    cfg.knowledge_db = os.path.join(tmpdir, "knowledge.db")
    cfg.chunk_size = questions.get("chunk_size", cfg.chunk_size)
    cfg.chunk_overlap = questions.get("chunk_overlap", cfg.chunk_overlap)
    cfg.retrieval_min_score = None      # 评估自己按阈值扫描，检索侧不做过滤
    # 关键：关掉检索侧的新近度重排。否则扫描时会在「已经加过权」的分数上再加一次，
    # 权重 0 那一行也就不再是真正的基线。
    cfg.recency_weight = 0.0

    service = KnowledgeService(cfg)
    by_app, _ = load_corpus(os.path.join(ROOT, questions["corpus_dir"]))

    gid_to_doc, created, collapsed = {}, 0, 0
    started = time.time()
    for appid, items in by_app.items():
        for item in items:
            result = service.import_document(
                app_id=appid,
                title=item["title"],
                content=item["contents"],
                source_type="announcement",
                source_name="Steam 官方公告",
                source_url=item["url"],
                publish_date=(item["date_utc"] or "")[:10],
                force=True,
            )
            gid_to_doc[str(item["gid"])] = result["document_id"]
            if result.get("created"):
                created += 1
            else:
                collapsed += 1
    elapsed = time.time() - started
    return service, gid_to_doc, {"created": created, "collapsed": collapsed,
                                "elapsed_s": round(elapsed, 1),
                                "chunks": service.store.count()}


def run_queries(service, questions, top_k):
    raw = {}
    for q in questions:
        started = time.time()
        result = service.search(q["app_id"], q["query"], top_k=top_k, threshold=0.0)
        raw[q["id"]] = {"hits": result["hits"],
                        "elapsed_ms": int((time.time() - started) * 1000)}
    return raw


def score_at(raw, questions, gid_to_doc, threshold, k):
    rows = []
    for q in questions:
        hits = evaluation.apply_threshold(raw[q["id"]]["hits"], threshold)
        expected_docs = {gid_to_doc[g] for g in q.get("expected_gids") or []
                         if g in gid_to_doc}
        ranked = evaluation.rank_documents(hits)
        answerable = bool(q.get("answerable", True))
        rows.append({
            "id": q["id"],
            "app_id": q["app_id"],
            "category": q.get("category"),
            "query": q["query"],
            "answerable": answerable,
            "expected_docs": sorted(expected_docs),
            "ranked_docs": ranked,
            "top_score": round(hits[0]["score"], 4) if hits else None,
            "kept": len(hits),
            "recall": evaluation.recall_at_k(expected_docs, ranked, k,
                                             match=q.get("match", "fraction"))
            if answerable else None,
            "precision": evaluation.precision_at_k(expected_docs, ranked, k) if answerable else None,
            "rr": evaluation.reciprocal_rank(expected_docs, ranked) if answerable else None,
            "violation": evaluation.isolation_violations(hits, q["app_id"]),
            "refused": (len(hits) == 0) if not answerable else None,
            "elapsed_ms": raw[q["id"]]["elapsed_ms"],
            # 前 5 条命中的可读快照：失败分析必须能说清「排上来的到底是什么」，
            # 而不是只留一串 document_id 让人猜。
            "top_hits": [
                {"document_id": h["document_id"], "app_id": h.get("app_id"),
                 "score": h["score"], "chunk_index": h.get("chunk_index"),
                 "title": (h.get("title") or "")[:70],
                 "excerpt": (h.get("content") or "")[:90].replace("\n", " ")}
                for h in hits[:k]
            ],
        })
    return rows


def by_category(rows, k):
    groups = {}
    for row in rows:
        groups.setdefault(row["category"], []).append(row)
    out = {}
    for name, items in groups.items():
        stats = evaluation.summarize(items, k)
        out[name] = {
            "n": len(items),
            "recall_at_k": stats["recall_at_k"],
            "mrr": stats["mrr"],
            "refusal_accuracy": stats["refusal_accuracy"],
            "isolation_violations": stats["isolation_violations"],
        }
    return out


def persist_run(config, payload):
    repo = KnowledgeRepository(config.knowledge_db)
    repo.init_schema()
    with repo.session() as conn:
        conn.execute(
            """INSERT INTO rag_evaluation_runs(
                 evaluation_id, test_set_version, corpus_fingerprint, total_questions,
                 answerable_count, unanswerable_count, recall_at_5, precision_at_5,
                 mrr, appid_isolation_pass, refusal_accuracy, chunk_count,
                 embedding_model, params_json, details_json, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload["evaluation_id"], payload["test_set_version"],
             payload["corpus_fingerprint"], payload["total_questions"],
             payload["answerable_count"], payload["unanswerable_count"],
             payload["recall_at_5"], payload["precision_at_5"], payload["mrr"],
             1 if payload["isolation_pass"] else 0, payload["refusal_accuracy"],
             payload["chunk_count"], payload["embedding_model"],
             json.dumps(payload["params"], ensure_ascii=False),
             json.dumps(payload["details"], ensure_ascii=False),
             payload["created_at"]))
    return repo


def main():
    parser = argparse.ArgumentParser()
    # 检索深度与指标截断点分开：top_k 要跟生产一致（10），
    # 但 Recall@K 的 K 保持 5，才能与历史基线对比。
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--k", type=int, default=5, help="Recall/Precision 的截断点")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--sweep", default="0.30,0.35,0.40,0.45,0.50,0.55,0.60")
    parser.add_argument("--recency-sweep", default="",
                        help="新近度权重扫描，逗号分隔，如 0.03,0.05,0.08")
    parser.add_argument("--recency-tau", type=float, default=30.0,
                        help="新近度衰减时间常数（天）")
    parser.add_argument("--semantic", action="store_true",
                        help="启用第二层语义复核（需要本地 LLM，约多花 1 分钟）")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--keep-index", action="store_true", help="保留临时索引便于排查")
    args = parser.parse_args()

    questions_doc = load_questions()
    by_app, corpus_by_gid = load_corpus(os.path.join(ROOT, questions_doc["corpus_dir"]))
    qs = validate_annotations(questions_doc, corpus_by_gid)

    kb_questions = [q for q in qs if q.get("intent", "kb_factual_qa") == "kb_factual_qa"]
    route_only = [q for q in qs if q.get("intent", "kb_factual_qa") != "kb_factual_qa"]
    route_counts = {}
    for q in route_only:
        route_counts[q["intent"]] = route_counts.get(q["intent"], 0) + 1

    print("标注校验通过：%d 题（知识库问答 %d / 路由类 %d）"
          % (len(qs), len(kb_questions), len(route_only)))
    print("  知识库问答里：可回答 %d / 应拒答 %d"
          % (sum(1 for q in kb_questions if q.get("answerable", True)),
             sum(1 for q in kb_questions if not q.get("answerable", True))))
    if route_counts:
        print("  路由类（本报告不计召回，等 Intent Router 落地后按路由准确率评估）：%s"
              % ", ".join("%s=%d" % kv for kv in sorted(route_counts.items())))

    tmpdir = tempfile.mkdtemp(prefix="rag_eval_")
    try:
        print("构建临时索引（语料 %d 款游戏 / %d 条公告）…"
              % (len(by_app), len(corpus_by_gid)))
        service, gid_to_doc, index_stats = build_index(tmpdir, questions_doc)
        print("索引完成：chunks=%d 新建文档=%d 内容重复折叠=%d 用时=%.1fs"
              % (index_stats["chunks"], index_stats["created"],
                 index_stats["collapsed"], index_stats["elapsed_s"]))

        missing = {g for q in kb_questions for g in q.get("expected_gids") or []} - set(gid_to_doc)
        if missing:
            raise SystemExit("有标注 gid 没有进入索引：%s" % sorted(missing))

        print("执行 %d 次检索（仅知识库问答）…" % len(kb_questions))
        raw = run_queries(service, kb_questions, args.top_k)

        threshold = args.threshold
        if threshold is None:
            threshold = questions_doc.get("threshold", 0.35)

        sweep = [float(x) for x in args.sweep.split(",") if x.strip()]
        if threshold not in sweep:
            sweep = sorted(sweep + [threshold])

        print("\n阈值扫描（top_k=%d，文档级口径）" % args.top_k)
        print("  %-8s %-9s %-9s %-8s %-9s %-9s %s"
              % ("阈值", "Recall@K", "Prec@K", "MRR", "拒答准确", "越界命中", "kept/题"))
        sweep_results = {}
        for t in sweep:
            rows = score_at(raw, kb_questions, gid_to_doc, t, args.k)
            stats = evaluation.summarize(rows, args.k)
            sweep_results[t] = stats
            avg_kept = sum(r["kept"] for r in rows) / float(len(rows))
            print("  %-8.2f %-9s %-9s %-8s %-9s %-9d %.2f"
                  % (t,
                     "-" if stats["recall_at_k"] is None else "%.3f" % stats["recall_at_k"],
                     "-" if stats["precision_at_k"] is None else "%.3f" % stats["precision_at_k"],
                     "-" if stats["mrr"] is None else "%.3f" % stats["mrr"],
                     "-" if stats["refusal_accuracy"] is None else "%.3f" % stats["refusal_accuracy"],
                     stats["isolation_violations"], avg_kept))

        rows = score_at(raw, kb_questions, gid_to_doc, threshold, args.k)
        stats = evaluation.summarize(rows, args.k)

        print("\n分类表现（阈值 %.2f）" % threshold)
        for name, item in sorted(by_category(rows, args.k).items()):
            print("  %-13s n=%-3d recall=%s mrr=%s refusal=%s 越界=%d"
                  % (name, item["n"],
                     "-" if item["recall_at_k"] is None else "%.3f" % item["recall_at_k"],
                     "-" if item["mrr"] is None else "%.3f" % item["mrr"],
                     "-" if item["refusal_accuracy"] is None else "%.3f" % item["refusal_accuracy"],
                     item["isolation_violations"]))

        failures = [r for r in rows
                    if (r["answerable"] and (r["recall"] or 0) < 1.0)
                    or (not r["answerable"] and not r["refused"])
                    or r["violation"]]
        print("\n未达标题目 %d / %d" % (len(failures), len(rows)))
        for row in failures:
            if row["answerable"]:
                print("  [%-11s] %s  recall=%.2f rr=%.2f top=%s | %s"
                      % (row["category"], row["id"], row["recall"] or 0,
                         row["rr"] or 0, row["top_score"], row["query"][:44]))
            else:
                print("  [%-11s] %s  拒答失败 kept=%d top=%s | %s"
                      % (row["category"], row["id"], row["kept"],
                         row["top_score"], row["query"][:44]))

        # ---------------- 新近度权重扫描
        # 索引构建是唯一的大头（约 6 分钟），检索本身只要 200ms/题，
        # 所以一次建索引、多次打分，比反复跑脚本划算得多。
        recency_results = {}
        weights = [float(x) for x in (args.recency_sweep or "").split(",") if x.strip()]
        if weights:
            print("\n新近度权重扫描（加性奖励 score + w·exp(-age/%g)，阈值 %.2f）"
                  % (args.recency_tau, threshold))
            print("  %-8s %-10s %-8s %-8s %-10s" % ("weight", "Recall@K", "MRR", "拒答", "q06名次"))
            for weight in weights:
                adjusted = {
                    qid: dict(payload,
                              hits=ranking.apply_recency(payload["hits"], weight,
                                                         args.recency_tau))
                    for qid, payload in raw.items()
                }
                rows_w = score_at(adjusted, kb_questions, gid_to_doc, threshold, args.k)
                stats_w = evaluation.summarize(rows_w, args.k)
                q06 = next((r for r in rows_w if r["id"] == "q06"), {})
                rr = q06.get("rr")
                rank = round(1.0 / rr) if rr else None
                # 逐题对比：只看均值会掩盖「整体变好、个别变差」，
                # 而排序改动的风险恰恰在个别题上（问旧内容的题会被压下去）。
                worse = []
                for base_row, adj_row in zip(rows, rows_w):
                    if base_row["rr"] and adj_row["rr"] and adj_row["rr"] < base_row["rr"]:
                        worse.append({"id": base_row["id"],
                                      "rr_before": round(base_row["rr"], 3),
                                      "rr_after": round(adj_row["rr"], 3)})
                recency_results[str(weight)] = {
                    "recall": stats_w["recall_at_k"], "mrr": stats_w["mrr"],
                    "refusal": stats_w["refusal_accuracy"],
                    "q06_rank": rank,
                    "regressed": worse,
                }
                print("  %-8s %-10.3f %-8.3f %-8.3f %-10s"
                      % (weight, stats_w["recall_at_k"], stats_w["mrr"],
                         stats_w["refusal_accuracy"], rank))

        # ---------------- 证据门（P0）与路由（P1）
        # 用不带阈值过滤的原始候选，按生产配置（top_k 深度）判断 —— 这才是线上行为。
        gate = EvidenceGate()
        checker = None
        if args.semantic:
            # 不要在这里 `from ai.config import AIConfig`：函数内 import 会把该名字
            # 变成局部变量，未走本分支时调用方那一行就 UnboundLocalError。
            from ai.llm.provider_factory import create_llm_provider
            from ai.rag.semantic_check import SemanticChecker
            llm_cfg = AIConfig()
            if llm_cfg.enabled:
                checker = SemanticChecker(create_llm_provider(llm_cfg))
                print("\n第二层语义复核已启用（%s / %s）"
                      % (llm_cfg.provider, llm_cfg.ollama_model
                         if llm_cfg.provider == "ollama" else ""))
            else:
                print("\n警告：AI_ENABLED=false，第二层语义复核被跳过")

        gate_rows = []
        for q in kb_questions:
            hits = raw[q["id"]]["hits"]
            result = gate.evaluate(q["query"], hits)
            row = {"id": q["id"], "answerable": bool(q.get("answerable", True)),
                   "shape": result.answer_shape, "status": result.status,
                   "missing": result.missing_elements, "query": q["query"],
                   "layer": "program"}
            # 只有程序层没拒答时才问模型：程序层已判缺元素，再问就是给它「求情」的机会
            if checker is not None and result.status != STATUS_INSUFFICIENT:
                semantic = checker.check(q["query"], hits)
                row["semantic"] = semantic
                if semantic.get("available"):
                    row["layer"] = "program+semantic"
                if semantic.get("verdict") == "insufficient":
                    row["status"] = STATUS_INSUFFICIENT
                    row["missing"] = semantic.get("missing") or row["missing"]
                elif semantic.get("verdict") == "conflicting":
                    row["status"] = "conflicting_evidence"
            gate_rows.append(row)

        answerable_rows = [r for r in gate_rows if r["answerable"]]
        unanswerable_rows = [r for r in gate_rows if not r["answerable"]]
        # 拒答 = 没给答案。conflicting 同样不算回答了问题，一起计入拒答
        refused = [r for r in unanswerable_rows
                   if r["status"] in (STATUS_INSUFFICIENT, "conflicting_evidence")]
        blocked = [r for r in answerable_rows if r["status"] == STATUS_INSUFFICIENT]
        undetermined = [r for r in gate_rows if r["status"] == STATUS_UNDETERMINED]
        gate_stats = {
            "answerable": len(answerable_rows),
            "unanswerable": len(unanswerable_rows),
            "refused_correctly": len(refused),
            "abstention_accuracy": round(len(refused) / len(unanswerable_rows), 3)
            if unanswerable_rows else None,
            "false_block": len(blocked),
            "false_block_rate": round(len(blocked) / len(answerable_rows), 3)
            if answerable_rows else None,
            "undetermined": len(undetermined),
        }
        print("\n证据门（原始候选 %d 条，不按阈值裁剪）" % args.top_k)
        print("  拒答准确率 = %s（%d/%d 道应拒答题被判 insufficient）"
              % (gate_stats["abstention_accuracy"], len(refused), len(unanswerable_rows)))
        print("  误拦率     = %s（%d/%d 道可答题被拦）"
              % (gate_stats["false_block_rate"], len(blocked), len(answerable_rows)))
        print("  定不了     = %d 题（mixed 形态，程序层无约束）" % len(undetermined))
        for row in blocked:
            print("    [误拦] %-6s %-9s 缺 %s | %s"
                  % (row["id"], row["shape"], row["missing"], row["query"][:36]))

        route_wrong = [(q["id"], q["intent"], route_intent(q["query"]).intent, q["query"])
                       for q in qs if route_intent(q["query"]).intent != q["intent"]]
        print("\n路由（全部 %d 题）准确率 = %.3f%s"
              % (len(qs), (len(qs) - len(route_wrong)) / float(len(qs)),
                 "" if not route_wrong else "，误判：%s" % route_wrong[:3]))

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        evaluation_id = "rag_eval_%s" % stamp
        payload = {
            "evaluation_id": evaluation_id,
            "test_set_version": questions_doc.get("version"),
            "corpus_fingerprint": corpus_fingerprint(by_app),
            "total_questions": stats["total"],
            "answerable_count": stats["answerable"],
            "unanswerable_count": stats["unanswerable"],
            "recall_at_5": stats["recall_at_k"],
            "precision_at_5": stats["precision_at_k"],
            "mrr": stats["mrr"],
            "refusal_accuracy": stats["refusal_accuracy"],
            "isolation_pass": stats["isolation_violations"] == 0,
            "isolation_violations": stats["isolation_violations"],
            "chunk_count": index_stats["chunks"],
            "embedding_model": service.config.embedding_model,
            "params": {"top_k": args.top_k, "threshold": threshold,
                       "chunk_size": service.config.chunk_size,
                       "chunk_overlap": service.config.chunk_overlap,
                       "scan": {str(t): sweep_results[t] for t in sweep_results}},
            "details": {
                "index": index_stats,
                "questions": rows,
                "evidence_gate": gate_stats,
                "recency_sweep": {"tau_days": args.recency_tau,
                                  "results": recency_results},
                "gate_rows": gate_rows,
                "router_accuracy": round((len(qs) - len(route_wrong)) / float(len(qs)), 3),
                # 路由类题目不进召回统计：它们根本不该查知识库。
                # 等 Intent Router 落地后，用这张表算路由准确率与误路由率。
                "route_only": {
                    "counts": route_counts,
                    "total": len(route_only),
                    "questions": [{"id": q["id"], "intent": q["intent"],
                                   "category": q.get("category"),
                                   "query": q["query"]} for q in route_only],
                },
            },
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        artifact = os.path.join(ARTIFACT_DIR, "%s.json" % evaluation_id)
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("\n明细已写入：%s" % os.path.relpath(artifact, ROOT))

        if not args.no_persist:
            persist_run(AIConfig().rag, payload)
            print("评估记录已写入 rag_evaluation_runs：%s" % evaluation_id)
        return 0
    finally:
        if not args.keep_index:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
