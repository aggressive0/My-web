"""
检索结果重排：时间新近度（Phase 6.3 遗留问题）。

问题很具体：`publish_date` 一直在元数据里，但从不参与排序。玩家问
「最近一次更新改了什么」，向量相似度会把 8 月、10 月的公告排在 11 月前面
（实测 q06 的 MRR 只有 0.20，正确答案排第 5）。

做法是**加性奖励**，不是乘性衰减：
- 乘性衰减 `score × decay` 会把手里的分数整体压小，越旧压得越狠，
  原本 0.62 的相关文档可能被压到阈值以下，等于用时间惩罚替用户做减法。
- 加性奖励 `score + w × exp(-age/τ)` 只往上抬新鲜文档，旧文档的相对次序不变，
  阈值语义也不被破坏。

`weight=0` 时完全不做任何事（默认关闭）——排序权重和阈值一样，
必须由评估集定，不能凭直觉给一个数就上身。

无发布日期的文档奖励为 0：不奖励也不惩罚。把「没写日期」当成「很旧」
是一种没有依据的推定。
"""

import math
from dataclasses import replace
from datetime import datetime, timezone


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def recency_bonus(publish_date, now=None, tau_days=30.0):
    """指数衰减的新近度权重，取值 (0, 1]。无日期或无解析结果返回 0。"""
    published = _parse_date(publish_date)
    if published is None or tau_days <= 0:
        return 0.0
    now = now or datetime.now(timezone.utc)
    age_days = (now - published).total_seconds() / 86400.0
    if age_days < 0:            # 未来日期：按「刚发布」处理，不额外奖励
        age_days = 0.0
    return math.exp(-age_days / float(tau_days))


def _get(hit, name, default=None):
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)


def _with_score(hit, score):
    if isinstance(hit, dict):
        return dict(hit, score=round(score, 4))
    return replace(hit, score=round(score, 4))


def apply_recency(hits, weight, tau_days=30.0, now=None):
    """按新近度加分并重排。weight<=0 时原样返回（不复制、不重排）。

    同时支持 `SearchHit` 与 `to_dict()` 后的字典：调用方（检索器 / 评估脚本）
    拿到的形态不同，重排逻辑不该因此有两份实现。
    """
    hits = list(hits or [])
    if not weight or weight <= 0:
        return hits
    now = now or datetime.now(timezone.utc)
    adjusted = []
    for hit in hits:
        bonus = float(weight) * recency_bonus(_get(hit, "publish_date"), now, tau_days)
        adjusted.append(_with_score(hit, float(_get(hit, "score", 0.0) or 0.0) + bonus))
    adjusted.sort(key=lambda h: float(_get(h, "score", 0.0) or 0.0), reverse=True)
    return adjusted
