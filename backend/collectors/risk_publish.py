"""
把风险指数写入 data 分支（Phase 4）。

输入（全部来自 data 分支，读最新版）：
    sentiment-daily.json   → N 负面评论比例
    topics-daily.json      → G 主题集中度
    steam-ccu.json         → C 在线人数变化
    steam-funnels.json     → A 成就衰减（暂不可用）

输出：
    risk-daily.json        → 当日各游戏指数 + 分量明细 + 历史序列

设计要点：
  - 分量缺失按剩余权重重归一化，**绝不把缺失当 0**——那会让没数据的游戏
    显得「很健康」；
  - 每个分量带 status / 来源 / 样本量，前端可以逐项展开；
  - 历史按日去重（同日重跑覆盖当天点），保留 90 天。

用法：
  GITHUB_TOKEN=... python backend/collectors/risk_publish.py
  python backend/collectors/risk_publish.py --dry-run --from-dir <含四个 json 的目录>
"""

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "analysis"))

import ci_publish as P   # noqa: E402
import risk as R         # noqa: E402

OUT = "risk-daily.json"
INPUTS = ("sentiment-daily.json", "topics-daily.json", "steam-ccu.json", "steam-funnels.json")


def load_inputs(repo, token):
    docs = {}
    for name in INPUTS:
        doc, _ = P.get_remote(name, repo, token)
        docs[name] = doc or {}
        if doc is None:
            print("  ! %s 不存在，相关分量将标 unavailable" % name)
    return docs


def main():
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    from_dir = None
    for i, a in enumerate(sys.argv):
        if a == "--from-dir" and i + 1 < len(sys.argv):
            from_dir = sys.argv[i + 1]

    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not from_dir:
        raise SystemExit("缺少 GITHUB_TOKEN（本地验证请用 --from-dir）")

    import game_config
    games_cfg = [g for g in game_config.load_games()["games"] if g["enabled"]]

    if from_dir:
        docs = {}
        for name in INPUTS:
            path = os.path.join(from_dir, name)
            docs[name] = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
            if not docs[name]:
                print("  ! %s 不存在" % path)
        prev_doc = {}
        prev_path = os.path.join(from_dir, OUT)
        if os.path.exists(prev_path):
            prev_doc = json.load(open(prev_path, encoding="utf-8"))
        print("输入来源：本地目录 %s" % from_dir)
    else:
        docs = load_inputs(repo, token)
        prev_doc, _ = P.get_remote(OUT, repo, token)
        prev_doc = prev_doc or {}

    sent = docs.get("sentiment-daily.json") or {}
    tops = docs.get("topics-daily.json") or {}
    ccu = docs.get("steam-ccu.json") or {}

    sent_by = {g.get("appid"): g for g in sent.get("games", [])}
    tops_by = {g.get("appid"): g for g in tops.get("games", [])}
    ccu_by = {g.get("appid"): g for g in ccu.get("games", [])}

    today = datetime.now(timezone.utc).date().isoformat()
    entries = []
    for g in games_cfg:
        appid = g["appid"]
        entry = R.build_game(
            appid,
            (sent_by.get(appid) or {}).get("totals"),
            tops_by.get(appid),
            ccu_by.get(appid),
        )
        entry["slug"] = g["slug"]
        entry["name"] = g["name"]
        entry["color"] = g["color"]
        entries.append(entry)

    history = R.merge_history(
        prev_doc.get("history") or {},
        [{"appid": e["appid"], "risk_index": e["risk_index"]} for e in entries],
        today,
    )

    doc = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_type": "derived",
        "definition": {
            "name": "游戏级公开信号风险指数",
            "formula": R.FORMULA_TEXT,
            "weights": R.WEIGHTS,
            "components": {
                "N": "模型判为负面（neg 带）的评论占比 × 100，分母=有文本评论数",
                "G": "最大主题占有文本评论的比例（主题集中度）",
                "C": "近 24h 在线均值 vs 前 24h 均值，clamp(-降幅%,0,50)×2",
                "A": "成就完成率时间序列衰减（时间序列未建立前不可用）",
            },
            "missing_policy": "缺失分量按剩余权重重归一化，缺失不当 0",
            "caveat": "自定义研判指标，不是流失概率；样本为接口 recent 流，非全体玩家随机样本",
        },
        # 排序：先按数据充分性（ok → low_sample → insufficient），再按风险值降序。
        # 不这样排的话，2 条样本的 50.0 会压过 300 条样本的 26.6 排到第一位 ——
        # 小样本噪声被排成了「最高风险」，这正是要避免的误导。
        "games": sorted(entries, key=lambda e: (
            {"ok": 0, "low_sample": 1, "insufficient": 2}.get(e["status"], 3),
            -(e["risk_index"] or 0),
        )),
        "history": history,
    }

    size_kb = len(json.dumps(doc)) / 1024
    if dry_run:
        print("[dry-run] 已生成 %.1f KB，未写入" % size_kb)
    else:
        _, sha = P.get_remote(OUT, repo, token)
        P.put_remote(OUT, doc, sha, repo, token, "data: risk %s" % doc["updated_at"])
        print("已写入 %s（%.1f KB）" % (OUT, size_kb))

    for e in doc["games"]:
        print("  %-16s 风险 %s  [%s]  分量 %s" % (
            e["name"],
            e["risk_index"] if e["risk_index"] is not None else "—",
            e["status"],
            " ".join("%s:%s" % (k, e["components"][k]["score"] if e["components"][k]["score"] is not None else "NA")
                     for k in "NGCA")))


if __name__ == "__main__":
    main()
