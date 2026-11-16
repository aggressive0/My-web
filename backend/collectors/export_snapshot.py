"""
生成站点内的离线快照（front/static_html/json/*.json）。

用途有两个：
  1. 本地开发验证——不必等 CI，也不必联网到 raw.githubusercontent.com；
  2. 刷新兜底数据——断网演示时页面会退回这些文件，保证图表不空。

数据来源与线上完全一致（同样的接口、同样的多游戏列表），
区别只是写到本地文件而不是 data 分支。

用法：
  python backend/collectors/export_snapshot.py
  python backend/collectors/export_snapshot.py --hours 72
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_publish as P  # noqa: E402  复用同一套取数与结构，避免两份逻辑漂移

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSON_DIR = os.path.join(ROOT, "front", "static_html", "json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--out", default=JSON_DIR)
    args = ap.parse_args()

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(timespec="seconds")

    ccu_path = os.path.join(args.out, "steam-ccu.json")
    prev_games = {}
    if os.path.exists(ccu_path):
        try:
            with open(ccu_path, encoding="utf-8") as f:
                old = json.load(f)
            for g in old.get("games", []):
                prev_games[int(g.get("appid"))] = g
            # 兼容早期的单游戏结构，避免升级时丢掉已有历史点
            if not prev_games and old.get("points"):
                prev_games[int(old.get("appid", 0))] = {
                    "points": old["points"],
                    "summary": old.get("summary", {}),
                }
        except (ValueError, OSError):
            prev_games = {}

    games = []
    for appid, name, color in P.GAMES:
        prev = prev_games.get(appid, {})
        points = list(prev.get("points") or [])
        try:
            players = P.fetch_ccu(appid)
        except RuntimeError as e:
            print("跳过 %s：%s" % (name, e))
            players = None

        if players is not None:
            if points and points[-1].get("ts", "")[:16] == now[:16]:
                points[-1]["p"] = players
            else:
                points.append({"ts": now, "p": players})

        points = sorted([p for p in points if p.get("ts", "") >= cutoff], key=lambda p: p["ts"])
        vals = [p["p"] for p in points] or [0]
        games.append({
            "appid": appid, "name": name, "color": color, "points": points,
            "summary": {
                "latest": vals[-1], "high": max(vals), "low": min(vals),
                "mean": round(sum(vals) / len(vals), 1) if points else 0,
                "count": len(points),
            },
        })

    os.makedirs(args.out, exist_ok=True)
    with open(ccu_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now, "window_hours": args.hours,
            "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
            "unit": "players", "games": games,
        }, f, ensure_ascii=False, separators=(",", ":"))

    funnels = []
    for appid, name, color in P.GAMES:
        try:
            achs = P.fetch_achievements(appid)
        except RuntimeError as e:
            print("成就跳过 %s：%s" % (name, e))
            continue
        if not achs:
            continue
        funnels.append({
            "appid": appid, "name": name, "color": color, "count": len(achs),
            "first": achs[0]["p"], "last": achs[-1]["p"],
            "span": round(achs[0]["p"] - achs[-1]["p"], 1),
            "achievements": achs,
        })

    with open(os.path.join(args.out, "steam-funnels.json"), "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now,
            "source": "Steam ISteamUserStats/GetGlobalAchievementPercentagesForApp",
            "note": "完成率为全量玩家累计值；按完成率降序近似推进顺序",
            "games": funnels,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print("CCU   : " + " | ".join("%s %s" % (g["name"], format(g["summary"]["latest"], ",")) for g in games))
    print("Funnel: " + " | ".join("%s %d 个 跨度 %.1fpp" % (g["name"], g["count"], g["span"]) for g in funnels))


if __name__ == "__main__":
    main()
