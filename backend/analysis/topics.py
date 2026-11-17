"""
主题识别（Phase 3 剩余部分）：规则候选 + 可追溯。

为什么是规则而不是聚类或 LLM：
  - 聚类（kmeans on embeddings）产出的簇无法命名，也无法解释「为什么这条评论属于这个主题」；
  - 逐条调 LLM 成本高、不可复现，而且无法在 CI 里稳定重跑；
  - 关键词命中虽然粗糙，但**可审计**：每条计数都能回指到具体评论 id 与命中的词。

因此本模块的输出一律标注为「候选主题信号」（candidate），
不叫「主题分类结论」——AGENTS.md §7.2 明确要求关键词共现只能作为候选信号。

已知局限（必须写进文档与页面）：
  1. 词典覆盖率决定召回率，未收录的表达直接漏判，不报错、不猜测；
  2. 反讽、否定（"not laggy at all"）会被误判，规则层不做否定消解；
  3. 一条评论可命中多个主题，所以各主题占比之和会超过 100%；
  4. 词典是中英双语，其他语言只能命中少量英文词，覆盖率显著偏低。
"""

import re
from datetime import datetime, timedelta, timezone

LEXICON_VERSION = "v1"

# 每个主题：slug / 中文标签 / 关键词（英文用词边界+词尾扩展，中文用子串）
# 刻意避开 'op'、'cc'、'ping' 这类过短或歧义的词，宁缺勿滥。
TOPICS = [
    {
        "slug": "performance",
        "label": "性能与优化",
        "en": ["fps", "lag", "laggy", "stutter", "optimization", "optimized",
               "optimize", "crash", "bug", "glitch", "freeze", "framerate"],
        "zh": ["卡顿", "掉帧", "优化", "崩溃", "闪退", "卡死", "帧数", "报错"],
    },
    {
        "slug": "server",
        "label": "服务器与连接",
        "en": ["server", "disconnect", "matchmaking", "queue", "latency", "timeout"],
        "zh": ["服务器", "断线", "匹配", "排队", "延迟", "掉线", "炸服"],
    },
    {
        "slug": "cheating",
        "label": "外挂与作弊",
        "en": ["cheater", "hacker", "hacking", "aimbot", "wallhack", "cheat", "exploit"],
        "zh": ["外挂", "作弊", "透视", "自瞄", "挂哥", "开挂"],
    },
    {
        "slug": "monetization",
        "label": "付费与内购",
        "en": ["microtransaction", "pay to win", "p2w", "battle pass", "loot box",
               "refund", "overpriced", "expensive", "dlc"],
        "zh": ["内购", "氪金", "付费", "抽卡", "价格", "退款", "皮肤", "礼包"],
    },
    {
        "slug": "balance",
        "label": "平衡性",
        "en": ["balance", "unbalanced", "nerf", "buff", "overpowered", "meta"],
        "zh": ["平衡", "削弱", "增强", "强度", "数值"],
    },
    {
        "slug": "content",
        "label": "内容与更新",
        "en": ["content", "update", "patch", "season", "new map", "expansion"],
        "zh": ["内容", "更新", "补丁", "赛季", "新地图", "版本"],
    },
    {
        "slug": "story",
        "label": "剧情与角色",
        "en": ["story", "character", "lore", "narrative", "plot"],
        "zh": ["剧情", "角色", "故事", "世界观", "人设"],
    },
    {
        "slug": "social",
        "label": "社交与组队",
        "en": ["friends", "party", "co-op", "coop", "multiplayer", "solo"],
        "zh": ["组队", "好友", "联机", "单人", "开黑"],
    },
    {
        "slug": "onboarding",
        "label": "新手体验",
        "en": ["tutorial", "beginner", "new player", "learning curve", "difficulty"],
        "zh": ["新手", "教程", "引导", "上手", "难度"],
    },
    {
        "slug": "grind",
        "label": "重复与肝度",
        "en": ["grind", "grinding", "repetitive", "boring", "tedious"],
        "zh": ["肝", "重复", "无聊", "枯燥", "刷"],
    },
]

LABEL = {t["slug"]: t["label"] for t in TOPICS}


def _compile(topics=None):
    """预编译正则：英文用 \\b词\\w*\\b（容忍复数/时态），中文用子串。"""
    compiled = {}
    for t in (topics or TOPICS):
        pats = []
        for kw in t["en"]:
            pats.append(re.compile(r"\b" + re.escape(kw) + r"\w*\b", re.IGNORECASE))
        compiled[t["slug"]] = {"en": pats, "zh": t["zh"]}
    return compiled


_COMPILED = _compile()


def match_topics(text, compiled=None):
    """返回 [(slug, 命中词)]。同一主题只记一次（取第一个命中的词）。"""
    if not text:
        return []
    c = compiled or _COMPILED
    low = text.lower()
    hits, seen = [], set()
    for slug, rule in c.items():
        if slug in seen:
            continue
        for pat in rule["en"]:
            m = pat.search(low)
            if m:
                hits.append((slug, m.group(0)))
                seen.add(slug)
                break
        else:
            for kw in rule["zh"]:
                if kw in text:            # 中文不做词边界，避免漏掉「很卡顿啊」
                    hits.append((slug, kw))
                    seen.add(slug)
                    break
    return hits


def tag_rows(rows, compiled=None):
    """给每条评论打主题。返回 rows（附加 topics 字段）与统计。"""
    out, matched = [], 0
    for r in rows:
        hits = match_topics(r.get("text"), compiled)
        item = dict(r)
        item["topics"] = [s for s, _ in hits]
        item["topic_terms"] = {s: w for s, w in hits}
        out.append(item)
        if hits:
            matched += 1
    return out, matched


def _daily_counts(rows, topic_slugs):
    """按日 × 主题计数，同时记录每天的分母（当天有文本的评论数）。"""
    days = {}
    for r in rows:
        d = days.setdefault(r["date"], {"n": 0, "matched": 0,
                                        "counts": {s: 0 for s in topic_slugs}})
        d["n"] += 1
        if r["topics"]:
            d["matched"] += 1
        for s in r["topics"]:
            d["counts"][s] += 1
    return days


def _trend(days, slug, window_days=7, today=None):
    """近 N 个自然日 vs 前 N 个自然日。

    为什么按日历窗口而不是「最近 N 个有数据的日期」：
        PUBG / PoE 这类游戏的数据不是连续的，按日期条数取窗口会把几个月前的
        评论算进「最近 7 天」，增长率就成了时间错觉。日历窗口 + 固定分母
        （window_days）能保证「日均」二字名副其实。
    基期为 0 时不算增长率，返回 status=base_zero —— 除零会产出 inf，
    页面拿到 inf 只会显示成一个荒谬的数字。
    """
    today = today or datetime.now(timezone.utc).date()

    def collect(offset_days):
        """offset_days=0 当前窗口；=window_days 上一个窗口。"""
        start = today - timedelta(days=offset_days + window_days - 1)
        end = today - timedelta(days=offset_days)
        count = reviews = 0
        for d, v in days.items():
            try:
                day = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue
            if start <= day <= end:
                count += v["counts"][slug]
                reviews += v["n"]
        return count, reviews

    cur_count, cur_reviews = collect(0)
    prev_count, prev_reviews = collect(window_days)

    if cur_reviews == 0 and prev_reviews == 0:
        return {"window_days": window_days, "status": "insufficient",
                "current_daily_avg": None, "previous_daily_avg": None,
                "growth_pct": None, "current_count": 0, "previous_count": 0,
                "current_window_reviews": 0, "previous_window_reviews": 0}

    cur_avg = cur_count / window_days
    prev_avg = prev_count / window_days

    if prev_count == 0:
        return {"window_days": window_days, "status": "base_zero",
                "current_daily_avg": round(cur_avg, 2), "previous_daily_avg": 0.0,
                "growth_pct": None, "current_count": cur_count, "previous_count": 0,
                "current_window_reviews": cur_reviews,
                "previous_window_reviews": prev_reviews}

    return {"window_days": window_days, "status": "ok",
            "current_daily_avg": round(cur_avg, 2),
            "previous_daily_avg": round(prev_avg, 2),
            "growth_pct": round((cur_avg - prev_avg) / prev_avg * 100, 1),
            "current_count": cur_count, "previous_count": prev_count,
            "current_window_reviews": cur_reviews,
            "previous_window_reviews": prev_reviews}


def analyze(rows, **kwargs):
    """一步到位：打标签 + 聚合。供发布脚本使用，避免调用方漏掉 tag_rows。"""
    tagged, _ = tag_rows(rows)
    return aggregate(tagged, **kwargs)


def aggregate(rows, min_sample=30, window_days=7, examples_per_topic=3):
    """按游戏聚合主题。返回 games 列表（含分母、占比、趋势、可追溯 id）。

    入参必须是 tag_rows() 的输出；未打标签的行直接报错，
    而不是静默当成「零命中」——那样会让覆盖率看起来像 0 却没有任何提示。
    """
    if rows and "topics" not in rows[0]:
        raise ValueError("aggregate() 需要 tag_rows() 的输出；请改用 analyze(rows)")

    games = {}
    for r in rows:
        g = games.setdefault(r["appid"], {"rows": [], "ids": {}})
        g["rows"].append(r)
        for s in r["topics"]:
            ids = g["ids"].setdefault(s, [])
            if len(ids) < examples_per_topic:
                ids.append(r["id"])

    out = []
    for appid, g in games.items():
        rs = g["rows"]
        n = len(rs)
        matched = sum(1 for r in rs if r["topics"])
        days = _daily_counts(rs, [t["slug"] for t in TOPICS])

        topics = []
        for t in TOPICS:
            slug = t["slug"]
            count = sum(d["counts"][slug] for d in days.values())
            if count == 0:
                continue
            topics.append({
                "slug": slug,
                "label": t["label"],
                "count": count,
                # 分母是「该游戏有文本的评论数」，不是全站评论，也不是玩家数
                "share": round(100.0 * count / n, 1) if n else None,
                "low_sample": count < min_sample,
                "sample_ids": g["ids"].get(slug, []),
                "trend": _trend(days, slug, window_days),
            })
        topics.sort(key=lambda x: -x["count"])

        out.append({
            "appid": appid,
            "n_reviews": n,
            "n_matched": matched,
            "coverage": round(matched / n, 3) if n else None,
            "topics": topics,
            "days": [{"date": d,
                      "n": days[d]["n"],
                      "matched": days[d]["matched"],
                      "top": sorted([{"slug": s, "count": c}
                                     for s, c in days[d]["counts"].items() if c],
                                    key=lambda x: -x["count"])[:3]}
                     for d in sorted(days)],
        })
    return sorted(out, key=lambda x: x["appid"])
