"""
成就漏斗分析：把「成就完成率」还原成「进度流失曲线」。

重要前提（必须写进报告，否则结论站不住）：
  1. Steam 的成就完成率是「全量玩家中的累计完成百分比」，没有时间维度；
  2. 接口不返回成就的先后顺序，本脚本用「完成率降序」作为推进顺序的近似
     —— 这是合理启发式（绝大多数玩家先解锁易得成就），但不是官方顺序；
  3. 因此「第 N 个成就处的流失」应表述为「推进程度约到 N/M 时离开的玩家比例」，
     不能表述为「第 N 天留存」。

用法：
  python funnel.py --appid 578080
  python funnel.py --appid 1203220 --top 12
"""

import argparse
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "steam.db")


def load(appid, db):
    with sqlite3.connect(db) as c:
        ts = c.execute(
            "SELECT MAX(snapshot_ts) FROM achievements WHERE appid=?", (appid,)
        ).fetchone()[0]
        if not ts:
            return None, []
        rows = c.execute(
            "SELECT ach_name, percent FROM achievements WHERE appid=? AND snapshot_ts=? "
            "AND percent IS NOT NULL ORDER BY percent DESC",
            (appid, ts),
        ).fetchall()
    return ts, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appid", type=int, required=True)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    ts, rows = load(args.appid, args.db)
    if not rows:
        print("没有该 appid 的成就数据，先跑 steam_collector.py --once")
        return

    n = len(rows)
    first, last = rows[0][1], rows[-1][1]
    print("appid %d | 成就 %d 个 | 快照 %s" % (args.appid, n, ts))
    print("最高完成率 %.1f%% → 最低 %.1f%%   全程衰减 %.1fpp" % (first, last, first - last))
    print()

    print("%-4s %-34s %8s %10s" % ("#", "achievement", "完成率", "较上一档"))
    print("-" * 60)
    prev = None
    for i, (name, pct) in enumerate(rows[: args.top], 1):
        delta = "" if prev is None else "%+.1fpp" % (pct - prev)
        print("%-4d %-34s %7.1f%% %10s" % (i, name[:34], pct, delta))
        prev = pct

    print("...")
    # 关键拐点：还留存多少玩家推进到某个比例
    print()
    print("推进程度与仍在该阶段的玩家比例：")
    for q in (0.25, 0.5, 0.75, 1.0):
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        print("  推进到 %3.0f%% 的玩家中，完成该档成就的占 %.1f%%" % (q * 100, rows[idx][1]))

    # 最大单步跌幅，通常对应最难/最劝退的一关
    worst_i, worst = None, 0
    for i in range(1, n):
        d = rows[i - 1][1] - rows[i][1]
        if d > worst:
            worst_i, worst = i, d
    if worst_i:
        print()
        print("最大单步流失：%.1fpp，发生在「%s」(%.1f%%) → 「%s」(%.1f%%)"
              % (worst, rows[worst_i - 1][0], rows[worst_i - 1][1],
                 rows[worst_i][0], rows[worst_i][1]))


if __name__ == "__main__":
    main()
