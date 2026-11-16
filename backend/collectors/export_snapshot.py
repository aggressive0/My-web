"""
生成站点内的离线快照（front/static_html/json/*.json）。

用途有两个：
  1. 本地开发验证——不必等 CI，也不必联网到 raw.githubusercontent.com；
  2. 刷新兜底数据——断网演示时页面会退回这些文件，保证图表不空。

数据来源与线上完全一致：直接复用 ci_publish 的 build_ccu / build_funnels，
游戏列表来自 backend/config/games.yaml。这样「合并旧点、失败保留、窗口裁剪」
这些容易写错的逻辑只有一份实现，不会本地与 CI 各写一遍然后行为不一致。

用法：
  python backend/collectors/export_snapshot.py
  python backend/collectors/export_snapshot.py --hours 72
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_publish as P          # noqa: E402  复用取数与合并逻辑
import game_config              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSON_DIR = os.path.join(ROOT, "front", "static_html", "json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=None, help="窗口小时数，默认取配置")
    ap.add_argument("--out", default=JSON_DIR)
    args = ap.parse_args()

    cfg = game_config.load_games()
    games = cfg["games"]
    hours = args.hours or cfg["defaults"]["window_hours"]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    os.makedirs(args.out, exist_ok=True)

    # ---------- CCU ----------
    ccu_path = os.path.join(args.out, "steam-ccu.json")
    old = _load(ccu_path) or {}
    prev_games = {}
    for g in old.get("games", []):
        try:
            prev_games[int(g.get("appid"))] = g
        except (TypeError, ValueError):
            continue
    # 兼容早期的单游戏结构，避免升级时丢掉已有历史点
    if not prev_games and old.get("points"):
        prev_games[int(old.get("appid", 0))] = {
            "points": old["points"], "summary": old.get("summary", {}),
        }

    ccu_games, _ = P.build_ccu(games, prev_games, now, cutoff)
    _save(ccu_path, {
        "updated_at": now, "window_hours": hours,
        "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
        "unit": "players", "games": ccu_games,
    })

    # ---------- 成就漏斗 ----------
    funnel_path = os.path.join(args.out, "steam-funnels.json")
    prev_funnels = {}
    for g in (_load(funnel_path) or {}).get("games", []):
        try:
            prev_funnels[int(g.get("appid"))] = g
        except (TypeError, ValueError):
            continue

    funnels = P.build_funnels(games, prev_funnels)
    _save(funnel_path, {
        "updated_at": now,
        "source": "Steam ISteamUserStats/GetGlobalAchievementPercentagesForApp",
        "note": "完成率为全量玩家累计值；按完成率降序近似推进顺序",
        "games": funnels,
    })

    print("CCU   : " + " | ".join(
        "%s %s" % (g["name"], format(g["summary"]["latest"], ",")) for g in ccu_games))
    print("Funnel: " + " | ".join(
        "%s %d 个 跨度 %.1fpp" % (g["name"], g["count"], g["span"]) for g in funnels))
    print("已写入 %s" % args.out)


if __name__ == "__main__":
    main()
