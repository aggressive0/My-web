"""
动态业务指标（Phase 6.5）。

策略里的 `data_evidence` 只能来自这里——**不能由模型编，也不能由 RAG 猜**。

三条硬约束，都是看了真实数据之后定的：

1. **窗口按数据实际可得范围算，不按「最近 7 天」硬套。**
   实测 `ccu` 只有约 48 小时、评论最新一条可能是 9 天前（NARAKA 11-09）
   甚至两个月前（PUBG 09-18）。硬套 7 天会得到 0 条，然后把「没数据」
   伪装成「没有负面评论」。

2. **算不出来的指标进 `missing` 并说明原因**，不用 0 填充。
   0 是个具体的数值，会被下游当成事实。

3. **每条都带 formula 与 as_of**。
   同一个「差评率」在不同窗口下能差一倍，不给公式和截止时间就没法核对。

窗口锚点：评论用「该游戏最新一条评论的时间」而不是 now()——否则数据一停更，
所有指标瞬间归零，看起来像突发事件。
"""

import sqlite3
from datetime import datetime, timedelta, timezone


def _to_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class MetricsCollector:
    def __init__(self, db_path):
        self.db_path = db_path

    def _session(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ CCU
    def ccu_stats(self, app_id):
        conn = self._session()
        try:
            rows = conn.execute(
                "SELECT ts_utc, players FROM ccu WHERE appid = ? ORDER BY ts_utc",
                (app_id,)).fetchall()
        finally:
            conn.close()

        if not rows:
            return [], [{"name": "ccu", "reason": "该游戏没有任何 CCU 采集记录"}]

        points = [(_to_dt(r["ts_utc"]), int(r["players"])) for r in rows]
        points = [(ts, p) for ts, p in points if ts]
        latest_ts, latest = points[-1]
        window_start = points[0][0]
        span_hours = (latest_ts - window_start).total_seconds() / 3600.0

        evidence = [{
            "name": "ccu_latest",
            "value": float(latest),
            "description": "最新一次采集的同时在线人数",
            "source": "sqlite:ccu",
            "formula": "SELECT players ORDER BY ts_utc DESC LIMIT 1",
            "as_of": latest_ts.isoformat(timespec="seconds"),
            "window": "最新快照",
        }]
        missing = []

        if span_hours >= 20:
            # 取 24 小时前最近的一个点（不要求严格等距，采集间隔是 5 分钟）
            target = latest_ts - timedelta(hours=24)
            before = [(abs((ts - target).total_seconds()), ts, p)
                      for ts, p in points if ts <= target + timedelta(hours=2)]
            if before:
                _, base_ts, base = min(before)
                change = (latest - base) / float(base) * 100 if base else None
                if change is not None:
                    evidence.append({
                        "name": "ccu_change_24h_pct",
                        "value": round(change, 2),
                        "description": "较 24 小时前的同时在线人数变化百分比",
                        "source": "sqlite:ccu",
                        "formula": "(latest - 24h前) / 24h前 × 100",
                        "as_of": latest_ts.isoformat(timespec="seconds"),
                        "window": "24h",
                    })
            peak = max(p for ts, p in points if ts >= target)
            evidence.append({
                "name": "ccu_peak_24h",
                "value": float(peak),
                "description": "24 小时内同时在线峰值",
                "source": "sqlite:ccu",
                "formula": "MAX(players) WHERE ts_utc >= latest - 24h",
                "as_of": latest_ts.isoformat(timespec="seconds"),
                "window": "24h",
            })
        else:
            missing.append({"name": "ccu_change_24h_pct",
                            "reason": "CCU 数据仅覆盖 %.1f 小时，不足以计算 24 小时变化" % span_hours})

        return evidence, missing

    # --------------------------------------------------------------- 评论
    def review_stats(self, app_id, days=30):
        conn = self._session()
        try:
            row = conn.execute(
                "SELECT MAX(created_ts) AS latest, COUNT(*) AS total "
                "FROM steam_reviews WHERE appid = ?", (app_id,)).fetchone()
            if not row or not row["total"]:
                return [], [{"name": "reviews", "reason": "该游戏没有任何评论记录"}]

            latest_ts = _to_dt(row["latest"])
            start = (latest_ts - timedelta(days=days)).isoformat(timespec="seconds")
            stats = conn.execute(
                """SELECT COUNT(*) AS n,
                          SUM(voted_up = 0) AS neg,
                          SUM(refunded = 1) AS refunded,
                          AVG(CASE WHEN voted_up = 0 THEN playtime_minutes END) AS neg_playtime
                   FROM steam_reviews
                   WHERE appid = ? AND created_ts >= ? AND created_ts <= ?""",
                (app_id, start, latest_ts.isoformat(timespec="seconds"))).fetchone()
        finally:
            conn.close()

        if not stats or not stats["n"]:
            return [], [{"name": "reviews_recent",
                         "reason": "最近 %d 天内没有评论数据（最新一条 %s）"
                                   % (days, latest_ts.date())}]

        count = int(stats["n"])
        neg = int(stats["neg"] or 0)
        as_of = latest_ts.isoformat(timespec="seconds")
        evidence = [
            {"name": "reviews_window_count", "value": float(count),
             "description": "窗口内评论条数",
             "source": "sqlite:steam_reviews",
             "formula": "COUNT(*) WHERE created_ts >= latest_ts - %dd" % days,
             "as_of": as_of, "window": "%dd（锚点为最新一条评论）" % days},
            {"name": "negative_ratio_window", "value": round(neg / float(count), 4),
             "description": "窗口内差评占比",
             "source": "sqlite:steam_reviews",
             "formula": "SUM(voted_up=0) / COUNT(*)",
             "as_of": as_of, "window": "%dd" % days},
        ]
        refunded = int(stats["refunded"] or 0)
        evidence.append({
            "name": "refunded_ratio_window", "value": round(refunded / float(count), 4),
            "description": "窗口内已退款评论占比",
            "source": "sqlite:steam_reviews",
            "formula": "SUM(refunded=1) / COUNT(*)",
            "as_of": as_of, "window": "%dd" % days})

        if stats["neg_playtime"] is not None:
            evidence.append({
                "name": "negative_avg_playtime_minutes",
                "value": round(float(stats["neg_playtime"]), 1),
                "description": "差评玩家的平均游戏时长（分钟）",
                "source": "sqlite:steam_reviews",
                "formula": "AVG(playtime_minutes) WHERE voted_up=0",
                "as_of": as_of, "window": "%dd" % days})

        return evidence, []

    def game_name(self, app_id):
        """从 games 表取游戏名。取不到返回 None，不编一个占位名。"""
        conn = self._session()
        try:
            row = conn.execute("SELECT name FROM games WHERE appid = ?",
                               (app_id,)).fetchone()
        except sqlite3.Error:                   # 表不存在等，按未知处理
            return None
        finally:
            conn.close()
        return row["name"] if row else None

    def recent_negative_texts(self, app_id, days=30, limit=200):
        """取窗口内的差评原文，供信号提取用。

        锚点同 review_stats：以「该游戏最新一条评论」为基准，不是 now()。
        数据停更时不至于返回空列表，把「没有新评论」误判成「没有差评」。
        """
        conn = self._session()
        try:
            head = conn.execute(
                "SELECT MAX(created_ts) AS latest FROM steam_reviews WHERE appid = ?",
                (app_id,)).fetchone()
            latest = _to_dt(head["latest"]) if head else None
            if latest is None:
                return []
            start = (latest - timedelta(days=days)).isoformat(timespec="seconds")
            rows = conn.execute(
                "SELECT text FROM steam_reviews WHERE appid = ? AND voted_up = 0"
                " AND created_ts >= ? AND created_ts <= ?"
                " ORDER BY created_ts DESC LIMIT ?",
                (app_id, start, latest.isoformat(timespec="seconds"), int(limit))).fetchall()
        finally:
            conn.close()
        return [row["text"] or "" for row in rows]

    # ------------------------------------------------------------------ 汇总
    def collect(self, app_id):
        ccu_evidence, ccu_missing = self.ccu_stats(app_id)
        review_evidence, review_missing = self.review_stats(app_id)
        evidence = ccu_evidence + review_evidence
        missing = ccu_missing + review_missing
        return {
            "app_id": app_id,
            "evidence": evidence,
            "missing": missing,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # 只报「有几条可用指标」，不报「数据完整」——缺了什么在 missing 里
            "available_count": len(evidence),
        }
