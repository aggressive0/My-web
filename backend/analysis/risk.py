"""
游戏级公开信号风险指数（Phase 4）。

公式（迁移计划 §Phase 4 与 AGENTS.md §7.3）：
    risk = 0.35·N + 0.30·G + 0.20·C + 0.15·A
    缺失分量按剩余权重重归一化：risk = Σ(w_i·s_i) / Σ(w_i)

四个分量与数据来源（全部来自 data 分支，无新数据源）：
    N 负面评论比例   sentiment-daily.json   模型判为 neg 的占比（分母=有文本评论数）
    G 主题集中度     topics-daily.json      最大主题占全部有文本评论的比例
    C 在线人数变化   steam-ccu.json         近 24h 均值 vs 之前 24h 均值的降幅
    A 成就推进衰减   steam-funnels.json     需要成就完成率的时间序列，当前只有单点快照

三条不可妥协的口径（AGENTS.md §7.3）：
    1. 名字必须叫「游戏级公开信号风险指数」，不是流失概率；
    2. 所有输入归一化、权重、时间窗口、缺失值处理必须可复现——本文件即定义；
    3. 不输出任何玩家级名单或个体风险。

评分映射（全部显式，可复现）：
    N: score = 负面占比 × 100                    （0% 负面 → 0；100% 负面 → 100）
    G: score = 最大主题占比                      （解释为「近三分之一的人在同一话题上」）
    C: score = clamp(-在线降幅%, 0, 50) × 2       （跌 50% 及以上 → 100；上涨 → 0）
    A: 暂不可用                                   （时间序列未建立）
"""

WEIGHTS = {"N": 0.35, "G": 0.30, "C": 0.20, "A": 0.15}

LABELS = {
    "N": "负面评论比例",
    "G": "主题集中度",
    "C": "在线人数变化",
    "A": "成就推进衰减",
}

LOW_SAMPLE_REVIEWS = 30     # 评论数低于此值，N/G 标记 low_sample
MIN_CCU_POINTS = 3           # 每侧至少 3 个采样点才计算 C
HISTORY_DAYS = 90            # 快照保留天数

FORMULA_TEXT = ("risk = (0.35·N + 0.30·G + 0.20·C + 0.15·A) / Σ(可用分量权重)；"
                "每个分量先归一化到 0–100")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def score_N(totals):
    """负面评论比例。入参：sentiment-daily.json 的某游戏 totals。"""
    n = (totals or {}).get("n")
    if not n:
        return None, {"status": "unavailable", "reason": "无评论样本",
                      "source": "sentiment-daily.json", "weight": WEIGHTS["N"],
                      "label": LABELS["N"]}
    neg_rate = totals["neg"] / n
    return neg_rate * 100, {
        "status": "low_sample" if n < LOW_SAMPLE_REVIEWS else "ok",
        "source": "sentiment-daily.json",
        "n": n,
        "neg": totals["neg"],
        "neg_rate": round(neg_rate * 100, 1),
        "weight": WEIGHTS["N"],
        "label": LABELS["N"],
    }


def score_G(game_topics):
    """主题集中度。入参：topics-daily.json 的某游戏对象。"""
    n = (game_topics or {}).get("n_reviews")
    topics = (game_topics or {}).get("topics") or []
    if not n or not topics:
        return None, {"status": "unavailable",
                      "reason": "无主题命中",
                      "source": "topics-daily.json", "weight": WEIGHTS["G"],
                      "label": LABELS["G"]}
    top = topics[0]
    return top["share"], {
        "status": "low_sample" if n < LOW_SAMPLE_REVIEWS else "ok",
        "source": "topics-daily.json",
        "n": n,
        "top_topic": top["slug"],
        "top_label": top["label"],
        "top_share": top["share"],
        "coverage": game_topics.get("coverage"),
        "weight": WEIGHTS["G"],
        "label": LABELS["G"],
    }


def score_C(ccu_game, now=None):
    """在线人数变化。近 24h 均值 vs 之前 24h 均值。

    为什么只用 48h 而不是整窗：steam-ccu.json 只有 72h 滚动窗，
    用全窗对比会把「小时级波动」当成趋势；48h 分成两个等长半窗，
    两侧各有足够点数时才算。
    """
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)

    points = (ccu_game or {}).get("points") or []
    recent, base = [], []
    cutoff = now - timedelta(hours=24)
    for p in points:
        try:
            ts = datetime.fromisoformat(p["ts"])
        except (KeyError, ValueError):
            continue
        (recent if ts >= cutoff else base).append(p["p"])

    if len(recent) < MIN_CCU_POINTS or len(base) < MIN_CCU_POINTS:
        return None, {"status": "unavailable",
                      "reason": "CCU 采样不足（每侧需 %d 点，现有 recent=%d base=%d）"
                                % (MIN_CCU_POINTS, len(recent), len(base)),
                      "source": "steam-ccu.json", "weight": WEIGHTS["C"],
                      "label": LABELS["C"]}

    recent_avg = sum(recent) / len(recent)
    base_avg = sum(base) / len(base)
    if base_avg <= 0:
        return None, {"status": "unavailable", "reason": "基期均值为 0",
                      "source": "steam-ccu.json", "weight": WEIGHTS["C"],
                      "label": LABELS["C"]}

    pct = (recent_avg - base_avg) / base_avg * 100
    score = _clamp(-pct, 0, 50) * 2
    return score, {
        "status": "ok",
        "source": "steam-ccu.json",
        "recent_avg": round(recent_avg, 1),
        "base_avg": round(base_avg, 1),
        "change_pct": round(pct, 1),
        "recent_points": len(recent),
        "base_points": len(base),
        "weight": WEIGHTS["C"],
        "label": LABELS["C"],
    }


def score_A(funnels_game=None):
    """成就推进衰减。需要完成率时间序列；当前 funnels 只有单点快照。"""
    return None, {
        "status": "unavailable",
        "reason": "成就完成率只有单点快照，时间序列未建立（需要连续多日快照）",
        "source": "steam-funnels.json",
        "weight": WEIGHTS["A"],
        "label": LABELS["A"],
    }


def combine(parts):
    """重归一化合成。parts = {N: (score, meta), ...}，score 为 None 表示缺失。"""
    num = den = 0.0
    used = []
    for key, (score, meta) in parts.items():
        if score is None:
            continue
        num += meta["weight"] * score
        den += meta["weight"]
        used.append(key)

    if not used or den <= 0:
        return None, used

    risk = num / den
    low_sample = any(parts[k][1]["status"] == "low_sample" for k in used)
    return round(risk, 1), used


def build_game(appid, sentiment_totals, topics_game, ccu_game, funnels_game=None):
    """单个游戏：各分量打分 → 合成。返回可直接序列化的对象。"""
    parts = {
        "N": score_N(sentiment_totals),
        "G": score_G(topics_game),
        "C": score_C(ccu_game),
        "A": score_A(funnels_game),
    }
    risk, used = combine(parts)

    low_sample = any(parts[k][1]["status"] == "low_sample" for k in used)
    if risk is None:
        status = "insufficient"
    elif low_sample:
        status = "low_sample"
    else:
        status = "ok"

    return {
        "appid": appid,
        "risk_index": risk,
        "status": status,
        "components_used": used,
        "weights_applied": {k: parts[k][1]["weight"] for k in used},
        "components": {k: {"score": (round(parts[k][0], 1) if parts[k][0] is not None else None),
                           **parts[k][1]} for k in parts},
    }


def merge_history(previous, entries, today, cap=HISTORY_DAYS):
    """把当日快照并入历史。previous 是旧文件的 {appid: history} 映射。

    同一天重复运行覆盖当天的点（不是追加），否则手动 dispatch 一次
    历史里就多一个同日副本。
    """
    merged = {}
    for appid, points in (previous or {}).items():
        merged[int(appid)] = [p for p in points if p.get("date") != today]
    for e in entries:
        if e["risk_index"] is None:
            continue                       # 没算出指数就不建历史条目，避免出现空序列
        merged.setdefault(int(e["appid"]), []).append(
            {"date": today, "risk_index": e["risk_index"]})
    return {a: sorted(pts[-cap:], key=lambda p: p["date"])
            for a, pts in merged.items() if pts}
