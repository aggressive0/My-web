"""
把 SQLite 里的 CCU 时序导出为前端可直接消费的静态 JSON。

为什么走静态 JSON 而不是 API：
  站点部署在 GitHub Pages，没有后端进程。JSON 落在 front/static_html/json/ 下，
  随站点一起发布；本地 Flask 只做分析，不参与展示链路。

用法：
  python export_ccu_json.py                 # 默认导出近 72 小时
  python export_ccu_json.py --hours 24
  python export_ccu_json.py --appid 1203220 --hours 168
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(ROOT, "backend", "data", "steam.db")
OUT = os.path.join(ROOT, "front", "static_html", "json", "steam-ccu.json")

GAMES = {1203220: "永劫无间"}


def export_json(db, out, appid=1203220, hours=72):
    """读取 SQLite，写出前端消费的 JSON；返回采样点数。可被采集器直接调用。"""
    if not os.path.exists(db):
        return 0

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT ts_utc, players FROM ccu WHERE appid=? AND ts_utc>=? ORDER BY ts_utc",
            (appid, since),
        ).fetchall()

    points = [{"ts": ts, "p": int(n)} for ts, n in rows]
    latest = points[-1]["p"] if points else None
    high = max((p["p"] for p in points), default=None)
    low = min((p["p"] for p in points), default=None)

    payload = {
        "appid": appid,
        "game": GAMES.get(appid, str(appid)),
        "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
        "unit": "players",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_hours": hours,
        "points": points,
        "summary": {"latest": latest, "high": high, "low": low, "count": len(points)},
    }

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return len(points)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appid", type=int, default=1203220)
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    n = export_json(args.db, args.out, args.appid, args.hours)
    if n == 0:
        print("没有可用数据：检查 %s 是否存在且有该 appid 的采样" % args.db)
        return

    print("导出 %d 个采样点 → %s" % (n, args.out))
    with open(args.out, encoding="utf-8") as f:
        s = json.load(f)["summary"]
    print("最新 %s | 区间 %s ~ %s" % (format(s["latest"], ","), format(s["low"], ","), format(s["high"], ",")))


if __name__ == "__main__":
    main()
