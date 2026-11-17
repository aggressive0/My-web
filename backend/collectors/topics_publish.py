"""
把主题候选信号聚合后发布到 data 分支（Phase 3 剩余部分）。

输入：data 分支 reviews-samples.jsonl（同情感管线）
输出：data 分支 topics-daily.json

设计要点：
  - 复用 sentiment_publish.parse_samples —— 样本解析（跳过坏行、按 id 去重、日期截取）
    只能有一份实现，两处各写一遍必然漂移；
  - 主题是**规则候选**，输出字段名一律 candidate/命中词，不叫「分类结论」；
  - 每个占比都带分母（该游戏有文本的评论数），并标 low_sample；
  - 趋势在基期为 0 时返回 status=base_zero 而不是 inf。

用法：
  python backend/collectors/topics_publish.py --dry-run --samples-file samples.jsonl
  GITHUB_TOKEN=... python backend/collectors/topics_publish.py
"""

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "analysis"))

import ci_publish as P           # noqa: E402
import sentiment_publish as SP   # noqa: E402  复用样本解析
import topics as T               # noqa: E402

OUT = "topics-daily.json"
TOP_N_HOTSPOTS = 3


def build_doc(rows, cfg_games=None):
    games = T.analyze(rows)
    meta = {g["appid"]: g for g in (cfg_games or [])}
    for g in games:
        m = meta.get(g["appid"], {})
        g["slug"] = m.get("slug")
        g["name"] = m.get("name") or str(g["appid"])
        g["color"] = m.get("color")
        # 热点榜：数量最高的若干主题，附带趋势状态，便于前端直接渲染
        g["hotspots"] = [
            {
                "slug": t["slug"],
                "label": t["label"],
                "count": t["count"],
                "share": t["share"],
                "low_sample": t["low_sample"],
                "trend": t["trend"],
                "sample_ids": t["sample_ids"],
            }
            for t in g["topics"][:TOP_N_HOTSPOTS]
        ]

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SP.SAMPLES,
        "sample_size": len(rows),
        "method": {
            "type": "rule_keyword_candidate",
            "lexicon_version": T.LEXICON_VERSION,
            "data_type": "derived",
            "note": "关键词命中是候选信号，不是语义结论；一条评论可命中多个主题，"
                    "故各主题占比之和会超过 100%",
            "limitations": [
                "词典未覆盖的表达会漏判，不做猜测",
                "不处理否定与反讽",
                "中英词典为主，其他语言覆盖率显著偏低",
                "样本来自接口的 recent 流，偏向近期评论，不能当作全体评论的随机样本；"
                "趋势反映的是『我们采到的样本』而非全体玩家",
            ],
            "topics": [{"slug": t["slug"], "label": t["label"]} for t in T.TOPICS],
        },
        "games": games,
    }


def main():
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not dry_run:
        raise SystemExit("缺少 GITHUB_TOKEN")

    local = None
    for i, a in enumerate(sys.argv):
        if a == "--samples-file" and i + 1 < len(sys.argv):
            local = sys.argv[i + 1]

    if local:
        with open(local, encoding="utf-8") as f:
            text = f.read()
        print("样本来源：本地文件 %s" % local)
    else:
        text, _ = SP.read_text(SP.SAMPLES, repo, token)

    rows, skipped = SP.parse_samples(text)
    print("样本：%d 条可用；跳过 %s" % (len(rows), dict(skipped) or "无"))
    if not rows:
        raise SystemExit("没有可用样本，退出（不写空结果覆盖线上数据）")

    cfg_games = []
    try:
        import game_config
        cfg_games = game_config.load_games()["games"]
    except Exception:
        pass

    doc = build_doc(rows, cfg_games)
    size_kb = len(json.dumps(doc)) / 1024

    if dry_run:
        print("[dry-run] 已生成，%.1f KB，未写入" % size_kb)
    else:
        _, sha = P.get_remote(OUT, repo, token)
        P.put_remote(OUT, doc, sha, repo, token, "data: topics %s" % doc["updated_at"])
        print("已写入 %s（%.1f KB）" % (OUT, size_kb))

    for g in doc["games"]:
        print("  %-16s 评论 %3d 条，命中主题 %3d 条（覆盖率 %s）"
              % (g["name"], g["n_reviews"], g["n_matched"], g["coverage"]))
        for h in g["hotspots"]:
            tr = h["trend"]
            print("      · %-8s %3d 次 占比 %4s%% 趋势 %s%s"
                  % (h["label"], h["count"], h["share"], tr["status"],
                     (" %.1f%%" % tr["growth_pct"]) if tr["growth_pct"] is not None else ""))


if __name__ == "__main__":
    main()
