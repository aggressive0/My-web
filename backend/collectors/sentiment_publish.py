"""
把评论样本打情感分，聚合后发布到 data 分支。

输入：data 分支的 reviews-samples.jsonl（Phase 2 产出的可追溯样本）
输出：data 分支的 sentiment-daily.json

为什么在 CI 里跑而不是本机：
    评论样本本来就在 data 分支，模型权重（几十 KB）在仓库里，
    只有句向量模型需要下载 —— 用 Actions 缓存后冷启动约 30 秒。
    本机跑的结果要再提交一次，多一个「谁的机器、哪次运行」的黑箱。

三个刻意的取舍：
  1. **不做二分类硬切**：P ∈ [0.4, 0.6] 记为 neu。弱标签模型在中间区域的
     输出本来就不可信，硬分成 pos/neg 只是制造假精度。
  2. **每个百分比都带 sample_size**：不给出分母的比例在这个项目里一律不允许出现。
  3. **保留可追溯 id**：每天最多留 pos/neg 各 2 条 review_id，
     这是「热点能追到具体评论」的最小实现，也让抽检成为可能。

口径提醒（页面必须照此标注）：
    标签来自 voted_up（推荐/不推荐），混入玩法、价格、优化等非情感因素，
    因此输出是「推荐倾向」而不是「情绪极性」。
"""

import base64
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BACKEND, "ml"))

import ci_publish as P      # noqa: E402  GitHub Contents API
import embedder as E        # noqa: E402
import head as H            # noqa: E402

SAMPLES = "reviews-samples.jsonl"
OUT = "sentiment-daily.json"
EXAMPLES_PER_CLASS = 2


def read_text(path, repo, token):
    """读 data 分支上的文本文件（jsonl）。不存在返回 (None, None)。"""
    for _ in range(3):
        try:
            res = P.gh("GET", "/repos/%s/contents/%s?ref=%s" % (repo, path, P.BRANCH), token=token)
            return base64.b64decode(res["content"]).decode("utf-8", "replace"), res.get("sha")
        except RuntimeError as e:
            if " 404 " in str(e):
                return None, None
            time.sleep(2)
    return None, None


def parse_samples(text):
    """返回 (rows, skipped)。坏行记数而不是静默丢弃。"""
    rows, skipped = [], Counter()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            skipped["bad_json"] += 1
            continue

        text_body = (d.get("text") or "").strip()
        if not text_body:
            skipped["no_text"] += 1
            continue
        if not d.get("created"):
            skipped["no_date"] += 1
            continue

        rows.append({
            "appid": d.get("appid"),
            "id": d.get("id"),
            "date": d["created"][:10],
            "text": text_body,
            "voted_up": bool(d.get("voted_up")),
            "lang": d.get("lang") or "unknown",
        })

    # 同一 id 只保留最后一次（样本文件可能因重建而包含旧副本）
    dedup = {}
    for r in rows:
        dedup[(r["appid"], r["id"])] = r
    return list(dedup.values()), skipped


def aggregate(rows, probs):
    """按 游戏 × 日期 聚合。返回 games 列表（含 totals 与可追溯 id）。"""
    games = {}
    for r, p in zip(rows, probs):
        p = float(p)
        g = games.setdefault(r["appid"], {
            "appid": r["appid"],
            "days": defaultdict(lambda: {"n": 0, "pos": 0, "neg": 0, "neu": 0,
                                         "actual_up": 0,
                                         "_p": [], "low_conf": 0,
                                         "pos_ids": [], "neg_ids": []}),
            "_probs": [],
        })
        d = g["days"][r["date"]]
        b = H.band(p)
        d["n"] += 1
        d[b] += 1
        if r["voted_up"]:
            d["actual_up"] += 1
        d["_p"].append(p)
        g["_probs"].append(p)
        if H.confidence(p) < 0.2:
            d["low_conf"] += 1
        if b == "pos" and len(d["pos_ids"]) < EXAMPLES_PER_CLASS:
            d["pos_ids"].append(r["id"])
        if b == "neg" and len(d["neg_ids"]) < EXAMPLES_PER_CLASS:
            d["neg_ids"].append(r["id"])

    out = []
    for appid, g in games.items():
        days = []
        for date in sorted(g["days"]):
            d = g["days"][date]
            d["date"] = date
            ps = d.pop("_p")
            d["mean_p"] = round(float(np.mean(ps)), 4)
            d["low_conf_share"] = round(d["low_conf"] / d["n"], 3) if d["n"] else None
            d["examples"] = {"pos": d.pop("pos_ids"), "neg": d.pop("neg_ids")}
            days.append(d)

        probs_all = g.pop("_probs")
        n = len(probs_all)
        pos = sum(d["pos"] for d in days)
        neg = sum(d["neg"] for d in days)
        actual_up = sum(d["actual_up"] for d in days)
        out.append({
            "appid": appid,
            "days": days,
            "totals": {
                "n": n,
                "pos": pos,
                "neg": neg,
                "neu": n - pos - neg,
                "pos_rate": round(100.0 * pos / n, 1) if n else None,
                # 真实投票推荐率：与模型判定并列，用来看校准偏差，而不是取代它
                "voted_up_rate": round(100.0 * actual_up / n, 1) if n else None,
                "mean_p": round(float(np.mean(probs_all)), 4) if n else None,
                "low_conf_share": round(
                    sum(d["low_conf_share"] * d["n"] for d in days if d["low_conf_share"]) / n, 3
                ) if n else None,
            },
            "coverage": {"first": days[0]["date"] if days else None,
                         "last": days[-1]["date"] if days else None,
                         "days": len(days)},
        })
    return sorted(out, key=lambda g: g["appid"])


def main():
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not dry_run:
        raise SystemExit("缺少 GITHUB_TOKEN")

    if not os.path.exists(H.DEFAULT_MODEL):
        raise SystemExit("找不到分类头 %s —— 先跑 backend/ml/train_head.py 并提交模型"
                         % H.DEFAULT_MODEL)

    local_samples = None
    for i, a in enumerate(sys.argv):
        if a == "--samples-file" and i + 1 < len(sys.argv):
            local_samples = sys.argv[i + 1]
    if local_samples:
        with open(local_samples, encoding="utf-8") as f:
            text = f.read()
        print("样本来源：本地文件 %s" % local_samples)
    else:
        text, _ = read_text(SAMPLES, repo, token)

    rows, skipped = parse_samples(text)
    print("样本：%d 条可用；跳过 %s" % (len(rows), dict(skipped) or "无"))
    if not rows:
        raise SystemExit("没有可用样本，退出（不写空结果覆盖线上数据）")

    encoder = E.build_embedder(os.environ.get("ST_EMBEDDER", "st"))
    print("编码器：%s" % getattr(encoder, "model_name", "?"))
    X = encoder.encode([r["text"] for r in rows], batch_size=64)
    print("向量：%s" % (X.shape,))

    model = H.load()
    probs = H.predict_proba(model, X)

    with open(H.METRICS_PATH, encoding="utf-8") as f:
        metrics = json.load(f)

    games = aggregate(rows, probs)
    names = {}
    try:
        import game_config
        names = {g["appid"]: g for g in game_config.load_games()["games"]}
    except Exception:
        pass
    for g in games:
        meta = names.get(g["appid"], {})
        g["slug"] = meta.get("slug")
        g["name"] = meta.get("name") or str(g["appid"])
        g["color"] = meta.get("color")

    doc = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SAMPLES,
        "sample_size": len(rows),
        "skipped": dict(skipped),
        "model": {
            "embedding_model": metrics.get("embedding_model"),
            "head_version": metrics.get("model_version"),
            "head_trained_at": metrics.get("trained_at"),
            "head_samples": metrics.get("n_samples"),
            "head_cv_f1_macro": (metrics.get("cv") or {}).get("cv_f1_macro"),
            "label_definition": metrics.get("label_definition"),
            "neutral_band": [H.NEUTRAL_LOW, H.NEUTRAL_HIGH],
        },
        "games": games,
    }

    if dry_run:
        print("[dry-run] 不写入远端。文档大小 %.1f KB" % (len(json.dumps(doc)) / 1024))
    else:
        # 覆盖写需要旧文件 sha，否则 GitHub 会返回 422
        _, sha_out = P.get_remote(OUT, repo, token)
        P.put_remote(OUT, doc, sha_out, repo, token, "data: sentiment %s" % doc["updated_at"])

    print("%s %s：%d 款游戏，共 %d 条"
          % ("[dry-run] 已生成" if dry_run else "已写入", OUT, len(games), len(rows)))
    for g in games:
        t = g["totals"]
        print("  %-16s n=%4d  模型判定 %s%%  |  实测投票 %s%%  mean_p=%s  覆盖 %d 天"
              % (g["name"], t["n"], t["pos_rate"], t["voted_up_rate"],
                 t["mean_p"], g["coverage"]["days"]))


if __name__ == "__main__":
    main()
