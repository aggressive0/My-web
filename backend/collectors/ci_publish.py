"""
把多款游戏的 CCU 采样与成就漏斗发布到独立的 data 分支。

产出三个文件（均在 data 分支根目录）：
  steam-ccu.json       各游戏同时在线时序（每小时追加一点，窗口 72h）
                       内含 games[] 注册表（名称/颜色/启用状态），
                       前端据此渲染切换器，无需再读第二个文件
  steam-funnels.json   各游戏成就完成率（每次刷新，用于进度流失漏斗）

游戏列表来自 backend/config/games.yaml，新增一款游戏只需改配置，不动本文件。

为什么用 Contents API 而不是 git clone：
  - 工作流只需 checkout main 取脚本，不必再克隆一个分支；
  - 数据分支保持孤立，main 的历史保持干净；
  - 一次 PUT 完成，失败可重试，不会留下半完成的仓库状态。

失败隔离：
  - 单款游戏拉取失败 → 保留其历史序列，继续处理其余游戏；
  - 成就拉取失败 → 沿用上一版，避免页面闪成空白；
  - 全部失败 → 仍会写回原内容（内容不变），不会把线上数据清空。
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

import game_config

API = "https://api.github.com"
BRANCH = "data"
CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
ACH_URL = "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"


def gh(method, endpoint, payload=None, token=None):
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "NEXUS-ccu-bot/1.0",
        "Authorization": "Bearer %s" % token,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(API + endpoint, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("GitHub API %s %s → %s %s" % (method, endpoint, e.code, detail))


def get_remote(path, repo, token):
    """读取 data 分支上的 JSON，返回 (doc, sha)；不存在时返回 (None, None)。"""
    endpoint = "/repos/%s/contents/%s?ref=%s" % (repo, path, BRANCH)
    for i in range(3):
        try:
            res = gh("GET", endpoint, token=token)
            raw = base64.b64decode(res["content"]).decode("utf-8")
            return json.loads(raw), res.get("sha")
        except RuntimeError as e:
            if " 404 " in str(e):
                return None, None
            if i == 2:
                raise
            time.sleep(2)
    return None, None


def put_remote(path, doc, sha, repo, token, message):
    body = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        gh("PUT", "/repos/%s/contents/%s" % (repo, path), body, token)
    except RuntimeError as e:
        if "409" in str(e) or "422" in str(e):
            time.sleep(2)
            _, sha2 = get_remote(path, repo, token)
            if sha2:
                body["sha"] = sha2
            gh("PUT", "/repos/%s/contents/%s" % (repo, path), body, token)
        else:
            raise


def fetch_json(url, retries=3):
    last = None
    for i in range(retries):
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(
            "%s%s_=%d" % (url, sep, int(time.time() * 1000)),
            headers={
                "User-Agent": "NEXUS-ccu-bot/1.0 (+github actions)",
                # 上游 CDN 有 max-age≈647s 缓存，必须穿透
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("请求失败 %s：%s" % (url, last))


def fetch_ccu(appid):
    doc = fetch_json("%s?appid=%d" % (CCU_URL, appid))
    return int(doc["response"]["player_count"])


def fetch_achievements(appid):
    doc = fetch_json("%s?gameid=%d" % (ACH_URL, appid))
    rows = doc.get("achievementpercentages", {}).get("achievements", [])
    out = []
    for a in rows:
        try:
            out.append({"n": a.get("name", "?"), "p": round(float(a.get("percent")), 1)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: -x["p"])
    return out


def build_ccu(games, old_map, now, cutoff):
    """按配置顺序生成 CCU 负载。enabled=0 的游戏保留历史序列但不采样。"""
    out, lines = [], []
    for g in games:
        appid = g["appid"]
        prev = old_map.get(appid) or {}
        points = list(prev.get("points") or [])
        players = None

        if g["enabled"]:
            try:
                players = fetch_ccu(appid)
            except RuntimeError as e:
                print("跳过 %s：%s" % (g["name"], e))

        if players is not None:
            if points and points[-1].get("ts", "")[:16] == now[:16]:
                points[-1]["p"] = players
            else:
                points.append({"ts": now, "p": players})

        points = sorted([p for p in points if p.get("ts", "") >= cutoff], key=lambda p: p["ts"])
        vals = [p["p"] for p in points] or [0]
        mean = round(sum(vals) / len(vals), 1) if points else 0

        out.append({
            "appid": appid,
            "slug": g["slug"],
            "name": g["name"],
            "color": g["color"],
            "enabled": bool(g["enabled"]),
            "tags": g["tags"],
            "points": points,
            "summary": {
                "latest": vals[-1],
                "high": max(vals),
                "low": min(vals),
                "mean": mean,
                "count": len(points),
            },
        })
        lines.append("%s %s" % (g["name"], format(vals[-1], ",") if points else "-"))
    return out, lines


def build_funnels(games, prev_map):
    out = []
    for g in games:
        appid = g["appid"]
        achs = []
        if g["enabled"]:
            try:
                achs = fetch_achievements(appid)
            except RuntimeError as e:
                print("成就跳过 %s：%s" % (g["name"], e))
        if not achs:
            # 拉取失败或未启用时沿用上一版，避免页面闪成空白、也避免丢历史
            if appid in prev_map:
                out.append(prev_map[appid])
            continue
        out.append({
            "appid": appid,
            "slug": g["slug"],
            "name": g["name"],
            "color": g["color"],
            "enabled": bool(g["enabled"]),
            "count": len(achs),
            "first": achs[0]["p"],
            "last": achs[-1]["p"],
            "span": round(achs[0]["p"] - achs[-1]["p"], 1),
            "achievements": achs,
        })
    return out


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("缺少 GITHUB_TOKEN")

    cfg = game_config.load_games(os.environ.get("GAMES_CONFIG"))
    games = cfg["games"]
    hours = int(os.environ.get("WINDOW_HOURS") or cfg["defaults"]["window_hours"])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

    # ---------- 1. CCU 时序 ----------
    old, sha = get_remote("steam-ccu.json", repo, token)
    old_map = {}
    if isinstance(old, dict) and isinstance(old.get("games"), list):
        for g in old["games"]:
            try:
                old_map[int(g.get("appid"))] = g
            except (TypeError, ValueError):
                continue
    elif isinstance(old, dict) and old.get("points"):
        # 兼容早期的单游戏结构
        old_map[int(old.get("appid", 0))] = old

    ccu_games, lines = build_ccu(games, old_map, now, cutoff)
    put_remote("steam-ccu.json", {
        "updated_at": now,
        "window_hours": hours,
        "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
        "unit": "players",
        "games": ccu_games,
    }, sha, repo, token, "data: CCU %s" % now)

    # ---------- 2. 成就漏斗 ----------
    oldf, shaf = get_remote("steam-funnels.json", repo, token)
    prev_funnels = {}
    for g in (oldf or {}).get("games", []):
        try:
            prev_funnels[int(g.get("appid"))] = g
        except (TypeError, ValueError):
            continue

    funnels = build_funnels(games, prev_funnels)
    put_remote("steam-funnels.json", {
        "updated_at": now,
        "source": "Steam ISteamUserStats/GetGlobalAchievementPercentagesForApp",
        "note": "完成率为全量玩家累计值；按完成率降序近似推进顺序",
        "games": funnels,
    }, shaf, repo, token, "data: funnels %s" % now)

    print("CCU   : " + " | ".join(lines))
    print("Funnel: " + " | ".join(
        "%s %d 个成就 跨度 %.1fpp" % (g["name"], g["count"], g["span"]) for g in funnels))
    print("Games : %d 款（启用 %d）" % (len(games), sum(1 for g in games if g["enabled"])))


if __name__ == "__main__":
    main()
