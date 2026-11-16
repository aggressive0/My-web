"""
CI 版采集：GitHub Actions 每小时运行一次。

与本地版（steam_collector.py）的差别：
  - Actions 的运行环境是一次性的，SQLite 不会跨运行保留；
  - 因此把 JSON 本身当作滚动存储：读取已有文件 → 追加本次采样 → 裁剪到窗口 → 写回；
  - 不依赖代理（GitHub Runner 在境外，可直连 api.steampowered.com）。

本地仍用 SQLite 存细粒度数据（5 分钟级）做分析，两者分工不冲突：
  SQLite = 本地分析库；JSON = 线上展示用的小时级序列。

用法：
  python3 backend/collectors/ci_collect.py
  python3 backend/collectors/ci_collect.py --appid 1203220 --hours 72
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSON_PATH = os.path.join(ROOT, "front", "static_html", "json", "steam-ccu.json")

GAMES = {1203220: "永劫无间"}


def fetch_ccu(appid, retries=3):
    last = None
    for i in range(retries):
        url = "%s?appid=%d&_=%d" % (CCU_URL, appid, int(time.time() * 1000))
        req = urllib.request.Request(url, headers={
            "User-Agent": "NEXUS-ccu-bot/1.0 (+github actions)",
            # 上游 CDN 有 max-age≈647s 的缓存，必须穿透，否则每小时会采到同一个值
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return int(json.loads(r.read().decode("utf-8"))["response"]["player_count"])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("拉取 CCU 失败：%s" % last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appid", type=int, default=1203220)
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--out", default=JSON_PATH)
    args = ap.parse_args()

    existing = {"points": []}
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
        except (ValueError, OSError):
            existing = {"points": []}

    points = existing.get("points") or []
    players = fetch_ccu(args.appid)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 同一分钟重复运行时不追加重复点
    if points and points[-1].get("ts", "")[:16] == ts[:16]:
        points[-1]["p"] = players
    else:
        points.append({"ts": ts, "p": players})

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(timespec="seconds")
    points = [p for p in points if p.get("ts", "") >= cutoff]
    points.sort(key=lambda p: p["ts"])

    payload = {
        "appid": args.appid,
        "game": GAMES.get(args.appid, str(args.appid)),
        "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
        "unit": "players",
        "generated_at": ts,
        "window_hours": args.hours,
        "points": points,
        "summary": {
            "latest": points[-1]["p"] if points else None,
            "high": max((p["p"] for p in points), default=None),
            "low": min((p["p"] for p in points), default=None),
            "count": len(points),
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    s = payload["summary"]
    print("appid=%d 本次 %s 人 | 窗口内 %d 点 | 区间 %s ~ %s"
          % (args.appid, format(players, ","), s["count"],
             format(s["low"], ",") if s["low"] else "-",
             format(s["high"], ",") if s["high"] else "-"))


if __name__ == "__main__":
    main()
