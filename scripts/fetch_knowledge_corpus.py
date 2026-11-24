"""抓取 Steam 官方公告，作为检索评估的语料（Phase 6.3）。

数据来源：`ISteamNews/GetNewsForApp/v2`，公开接口，无需 Key。
它返回的是**官方发布的公告原文**（补丁说明、活动公告、开发者日志），
正是 writing-block.md §5.1 列出的知识库来源，不是模拟数据。

为什么要落成本地 fixture 而不是每次评估都现抓：
    Recall@5 这类数字只有「同一语料、同一问题集」下才可比。
    联网抓取会让语料随发布时间漂移，今天的 0.8 和下周的 0.8 不是一回事。

网络事实（本机实测）：`api.steampowered.com` 直连可用，
走 `127.0.0.1:7897` 代理反而 SSL EOF。所以这里强制不走代理。

用法：
    python scripts/fetch_knowledge_corpus.py              # 抓默认 6 款
    python scripts/fetch_knowledge_corpus.py --force      # 覆盖已有 fixture
    python scripts/fetch_knowledge_corpus.py --appid 1203220 --count 20
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(ROOT, "tests", "fixtures", "rag_corpus")
GAMES_CONFIG = os.path.join(ROOT, "backend", "config", "games.yaml")

API = ("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
       "?appid=%d&count=%d&maxlength=%d&feeds=%s")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 只要 Steam 官方公告频道。不加这个参数会混进 PlayGround.ru / PC Gamer /
# PCGamesN 等第三方媒体稿——它们不是官方资料，进知识库就是污染。
OFFICIAL_FEED = "steam_community_announcements"

# 正文短于此值的丢弃并计数。Steam 上有大量「见图公告」正文只有几十个字符，
# 入库只会制造噪声；丢弃要记数，不能悄悄消失。
MIN_CONTENT_CHARS = 200


def load_appids():
    """直接读 games.yaml 的 appid 行。

    不复用 game_config.load_games：那个模块在 backend/collectors 下，
    为读一个数字把 backend 塞进 sys.path 不值得；这里的解析只需 appid。
    """
    appids = []
    with open(GAMES_CONFIG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- appid:"):
                appids.append(int(line.split(":", 1)[1].strip()))
    return appids


def fetch(appid, count=20, maxlength=0, feeds=OFFICIAL_FEED, retries=3):
    """maxlength=0 表示全文。截断正文会让「证据」变成残句，评估随之失真。"""
    url = API % (appid, count, maxlength, feeds)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MyWeb-RAG-Eval/1.0"})
            with OPENER.open(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            items = payload.get("appnews", {}).get("newsitems") or []
            kept, dropped = [], 0
            for item in items:
                contents = item.get("contents") or ""
                if len(contents.strip()) < MIN_CONTENT_CHARS:
                    dropped += 1
                    continue
                kept.append({
                    "gid": str(item.get("gid")),
                    "title": (item.get("title") or "").strip(),
                    "url": item.get("url"),
                    "feedname": item.get("feedname"),
                    "date_utc": datetime.fromtimestamp(
                        item.get("date", 0), timezone.utc).isoformat(timespec="seconds"),
                    "contents": contents,
                })
            return kept, dropped
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError("appid=%d 抓取失败：%r" % (appid, last_error))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", type=int, action="append", help="只抓指定 appid，可重复")
    parser.add_argument("--count", type=int, default=20, help="每款游戏抓多少条")
    parser.add_argument("--maxlength", type=int, default=0, help="0=全文（默认）")
    parser.add_argument("--feeds", default=OFFICIAL_FEED, help="Steam 公告频道过滤")
    parser.add_argument("--force", action="store_true", help="覆盖已有 fixture")
    args = parser.parse_args()

    os.makedirs(CORPUS_DIR, exist_ok=True)
    appids = args.appid or load_appids()
    summary = []

    for appid in appids:
        path = os.path.join(CORPUS_DIR, "%d.json" % appid)
        if os.path.exists(path) and not args.force:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            summary.append((appid, len(cached.get("items") or []), "cached"))
            continue
        items, dropped = fetch(appid, count=args.count, maxlength=args.maxlength,
                               feeds=args.feeds)
        doc = {
            "appid": appid,
            "source": "steam_news_api",
            "feed": args.feeds,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(items),
            "dropped_short": dropped,
            "items": items,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        summary.append((appid, len(items), "fetched（丢弃过短 %d）" % dropped))

    print("语料目录：%s" % CORPUS_DIR)
    for appid, count, status in summary:
        print("  appid=%-8d items=%-3d %s" % (appid, count, status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
