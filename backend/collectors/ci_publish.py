"""
把最新 CCU 采样发布到独立的 data 分支（不碰 main，不触发 Pages 部署）。

为什么用 Contents API 而不是 git clone：
  - 工作流只需 checkout main 取脚本，不必再克隆一个分支；
  - 数据分支保持孤立，main 的历史保持干净；
  - 一次 POST 完成，失败可重试，不会留下半完成的仓库状态。

页面侧从 https://raw.githubusercontent.com/<repo>/data/steam-ccu.json 读取。

用法（在 GitHub Actions 中）：
  GITHUB_TOKEN 由 Actions 自动注入，GITHUB_REPOSITORY 同理

本地手动发布：
  GITHUB_TOKEN=<pat> GITHUB_REPOSITORY=aggressive0/My-web python3 ci_publish.py
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

API = "https://api.github.com"
BRANCH = "data"
PATH = "steam-ccu.json"
CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
GAMES = {1203220: "永劫无间"}


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


def fetch_remote_points(repo, token, retries=2):
    """读取 data 分支上的现有 JSON；不存在时返回空。"""
    for i in range(retries + 1):
        try:
            res = gh("GET", "/repos/%s/contents/%s?ref=%s" % (repo, PATH, BRANCH), token=token)
            raw = base64.b64decode(res["content"]).decode("utf-8")
            doc = json.loads(raw)
            return doc.get("points") or [], res.get("sha")
        except RuntimeError as e:
            if " 404 " in str(e) or "→ 404" in str(e):
                return [], None
            if i == retries:
                raise
            time.sleep(2)
    return [], None


def fetch_ccu(appid, retries=3):
    last = None
    for i in range(retries):
        url = "%s?appid=%d&_=%d" % (CCU_URL, appid, int(time.time() * 1000))
        req = urllib.request.Request(url, headers={
            "User-Agent": "NEXUS-ccu-bot/1.0 (+github actions)",
            # 上游 CDN 有 max-age≈647s 缓存，必须穿透
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
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("缺少 GITHUB_TOKEN")
    appid = int(os.environ.get("STEAM_APPID", "1203220"))
    hours = int(os.environ.get("WINDOW_HOURS", "72"))

    points, sha = fetch_remote_points(repo, token)
    players = fetch_ccu(appid)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if points and points[-1].get("ts", "")[:16] == ts[:16]:
        points[-1]["p"] = players
    else:
        points.append({"ts": ts, "p": players})

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    points = sorted([p for p in points if p.get("ts", "") >= cutoff], key=lambda p: p["ts"])

    payload = {
        "appid": appid,
        "game": GAMES.get(appid, str(appid)),
        "source": "Steam ISteamUserStats/GetNumberOfCurrentPlayers",
        "unit": "players",
        "generated_at": ts,
        "window_hours": hours,
        "points": points,
        "summary": {
            "latest": points[-1]["p"] if points else None,
            "high": max((p["p"] for p in points), default=None),
            "low": min((p["p"] for p in points), default=None),
            "count": len(points),
        },
    }

    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = {
        "message": "data: CCU %s" % ts,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha

    try:
        gh("PUT", "/repos/%s/contents/%s" % (repo, PATH), body, token)
    except RuntimeError as e:
        # sha 冲突（并发或上一轮刚改过）：重取一次再试
        if "409" in str(e) or "422" in str(e):
            time.sleep(2)
            _, sha2 = fetch_remote_points(repo, token)
            if sha2:
                body["sha"] = sha2
            gh("PUT", "/repos/%s/contents/%s" % (repo, PATH), body, token)
        else:
            raise

    s = payload["summary"]
    print("本次 %s 人 | 窗口内 %d 点 | 区间 %s ~ %s | 已写入 %s 分支"
          % (format(players, ","), s["count"],
             format(s["low"], ",") if s["low"] else "-",
             format(s["high"], ",") if s["high"] else "-", BRANCH))


if __name__ == "__main__":
    main()
