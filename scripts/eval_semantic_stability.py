"""
第二层语义复核的稳定性测量（Phase 6.3）。

「稳定」必须有定义，否则改完只是换个说法。这里用的定义是：

- **翻转率**：同一题、同一批候选，多次调用得到不同 verdict 的比例
- **拒答准确率**：应拒答题被判 insufficient 的比例（每轮都算）
- **误拦率**：可答题被判 insufficient 的比例（每轮都算）
- **自相矛盾率**：verdict=sufficient 但 reason 说「没有提到」的比例

同一次运行里建一次索引、每个问题只检索一次、然后对**同一批候选**重复调用 N 次。
这样翻转只可能来自模型采样，不会混入检索波动。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv                                  # noqa: E402

# 与其他脚本同样的坑：load_dotenv 只写在 app.py 里，脚本必须自己加载，
# 否则 AI_ENABLED=false，语义复核会被静默跳过。
load_dotenv(os.path.join(ROOT, "backend", ".env"))

from ai.config import AIConfig                                  # noqa: E402
from ai.llm.provider_factory import create_llm_provider         # noqa: E402
from ai.rag.semantic_check import SemanticChecker               # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import importlib.util                                           # noqa: E402

spec = importlib.util.spec_from_file_location(
    "evalret", os.path.join(ROOT, "scripts", "evaluate_retrieval.py"))
evalret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evalret)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3, help="每题重复次数")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试用）")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    cfg = AIConfig()
    if not cfg.enabled:
        print("AI 未启用（AI_ENABLED=false），无法测量语义复核")
        return 2

    questions_doc = evalret.load_questions()
    by_app, corpus_by_gid = evalret.load_corpus(
        os.path.join(ROOT, questions_doc["corpus_dir"]))
    qs = evalret.validate_annotations(questions_doc, corpus_by_gid)
    kb = [q for q in qs if q.get("intent", "kb_factual_qa") == "kb_factual_qa"]
    if args.limit:
        kb = kb[:args.limit]

    tmpdir = tempfile.mkdtemp(prefix="semantic_stability_")
    try:
        print("构建临时索引…（约 6 分钟）")
        service, _gid_to_doc, _stats = evalret.build_index(tmpdir, questions_doc)

        provider = create_llm_provider(cfg)
        checker = SemanticChecker(provider)

        rows = []
        started = time.time()
        for index, q in enumerate(kb, start=1):
            result = service.search(q["app_id"], q["query"],
                                    top_k=args.top_k, threshold=0.0)
            hits = result["hits"]
            verdicts, reasons = [], []
            for _ in range(args.repeats):
                outcome = checker.check(q["query"], hits)
                verdicts.append(outcome["verdict"])
                reasons.append(outcome.get("reason") or "")
            distinct = len(set(verdicts))
            rows.append({
                "id": q["id"], "answerable": bool(q.get("answerable", True)),
                "shape": q.get("answer_shape"), "query": q["query"],
                "verdicts": verdicts, "reasons": reasons,
                "flipped": distinct > 1,
                "contradiction": reasons.count("semantic_self_contradiction"),
                "unavailable": verdicts.count("undetermined"),
            })
            flag = "翻转" if distinct > 1 else ""
            print("  %2d/%d %-5s %-8s %s %s"
                  % (index, len(kb), q["id"], q.get("answer_shape"),
                     ",".join(v[0].upper() for v in verdicts), flag))

        def majority(verdicts):
            """保守口径：只要有 insufficient 就按 insufficient 算。

            这是实际接线时的策略（宁可拒答不可误答），所以指标也按它统计。
            """
            return "insufficient" if "insufficient" in verdicts else verdicts[0]

        answerable = [r for r in rows if r["answerable"]]
        unanswerable = [r for r in rows if not r["answerable"]]
        per_round = []
        for i in range(args.repeats):
            bad = [r["id"] for r in unanswerable if r["verdicts"][i] != "insufficient"]
            blocked = [r["id"] for r in answerable if r["verdicts"][i] == "insufficient"]
            per_round.append({
                "round": i + 1,
                "abstention": round(1 - len(bad) / len(unanswerable), 3) if unanswerable else None,
                "false_block": round(len(blocked) / len(answerable), 3) if answerable else None,
                "missed_unanswerable": bad,
                "blocked_answerable": blocked,
            })

        # 全部不可用时，「0% 翻转」是假成功：模型压根没回答，当然一致。
        # 必须直接失败，不能产出可被误读的指标。
        total_calls = len(rows) * args.repeats
        unavailable = sum(r["unavailable"] for r in rows)
        if unavailable == total_calls:
            print("\n错误：%d 次调用全部不可用（Ollama 未启动？），本次测量无效。"
                  % total_calls)
            return 3
        if unavailable > total_calls * 0.2:
            print("\n错误：不可用占比 %d/%d 过高，指标不可信。"
                  % (unavailable, total_calls))
            return 3

        flipped = [r["id"] for r in rows if r["flipped"]]
        summary = {
            "repeats": args.repeats,
            "questions": len(rows),
            "flip_count": len(flipped),
            "flip_rate": round(len(flipped) / len(rows), 3),
            "flipped_ids": flipped,
            "contradiction_hits": sum(r["contradiction"] for r in rows),
            "unavailable_hits": sum(r["unavailable"] for r in rows),
            "per_round": per_round,
            "elapsed_s": round(time.time() - started, 1),
        }

        print("\n=== 稳定性 ===")
        print("  翻转题数 = %d/%d（%.1f%%）%s"
              % (summary["flip_count"], summary["questions"],
                 summary["flip_rate"] * 100, flipped or ""))
        for item in per_round:
            print("  第 %d 轮：拒答 %s / 误拦 %s | 漏判 %s | 误拦 %s"
                  % (item["round"], item["abstention"], item["false_block"],
                     item["missed_unanswerable"] or "-",
                     item["blocked_answerable"] or "-"))
        print("  自相矛盾拦截 = %d 次 | 不可用降级 = %d 次"
              % (summary["contradiction_hits"], summary["unavailable_hits"]))

        out = args.out or os.path.join(
            ROOT, "tests", "artifacts",
            "semantic_stability_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        payload = {"summary": summary, "rows": rows,
                   "params": {"top_k": args.top_k,
                              "temperature": 0,
                              "snippet": checker.snippet,
                              "max_evidence": checker.max_evidence},
                   "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("  明细写入：%s" % out)
        return 0
    finally:
        shutil.rmtree(tmpdir, True)


if __name__ == "__main__":
    sys.exit(main())
