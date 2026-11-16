"""
Steam 评论采集（Phase 2）：原始评论 → 本地 SQLite。

接口（实测于 2026-11-16，无需 API Key）：
    新：https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/
    旧：https://store.steampowered.com/appreviews/<appid>?json=1   （降级用）

实测出来的三条「文档没写」的行为，直接决定实现方式：
  1. `num_per_page` 只在 `filter=all` 时生效（100 条/页）；
     `filter=recent` 固定返回 5 条，无论传多少。
  2. `filter=recent` 翻页时，若没有更多数据，服务端会**返回同一个 cursor**
     且 reviews 为空 —— 必须显式判定「游标没前进就停」，否则死循环。
  3. `language=` 参数被忽略（传 schinese 仍返回 english 评论）。
     因此语言不做服务端过滤，改为落库后按 review.language 聚合。

设计取舍：
  - 原始评论只进本地库，不进 Git（见 docs/DATA_MIGRATION_PLAN.md §2.2）。
  - 主键 (appid, review_id)，重复采集天然幂等。
  - 追评（timestamp_updated 变化）不删旧行，只覆盖；是否要重跑情感分析
    由 updated_ts > fetched_at 判断。

用法：
  python steam_reviews_collector.py --once                     # 按配置采一轮（recent）
  python steam_reviews_collector.py --once --pages 6           # 多翻几页
  python steam_reviews_collector.py --mode backfill --once     # 回溯历史（filter=all）
  python steam_reviews_collector.py --status                   # 看库里已有多少
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import db as store
import game_config

ENDPOINT_NEW = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
ENDPOINT_LEGACY = "https://store.steampowered.com/appreviews/%d"

DB = os.environ.get("STEAM_DB", store.DEFAULT_DB)

# 显式不使用环境代理：本机 https_proxy 常指向已死端口，默认 opener 会把它当代理用，
# 于是「直连」变成 502 Tunnel failed。Actions runner 无此变量，行为一致。
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DEFAULT_LANGUAGE = "all"


class ReviewFetchError(RuntimeError):
    pass


def _iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def parse_review(r):
    """把两种接口的 review 归一成同一结构。字段缺失一律 None，不猜。"""
    author = r.get("author") or {}
    text = r.get("review")
    return {
        "review_id": str(r.get("recommendationid") or ""),
        "created_ts": _iso(r.get("timestamp_created")),
        "updated_ts": _iso(r.get("timestamp_updated")),
        "voted_up": 1 if r.get("voted_up") else 0,
        "language": r.get("language"),
        "playtime_minutes": author.get("playtime_at_review"),
        "votes_up": r.get("votes_up"),
        "refunded": 1 if r.get("refunded") else 0,
        "text": (text or "")[:4000] or None,
    }


def fetch_page(appid, cursor="*", filter_="recent", num=100, language=DEFAULT_LANGUAGE,
               purchase_type="all", day_range=None, timeout=25, retries=3):
    """取一页评论，返回 response 字典（含 reviews / cursor / query_summary）。"""
    params = {
        "appid": appid,
        "filter": filter_,
        "num_per_page": num,
        "language": language,
        "purchase_type": purchase_type,
        "cursor": cursor or "*",
    }
    if day_range:
        params["day_range"] = day_range

    url = ENDPOINT_NEW + "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": "NEXUS-reviews/1.0 (student research)",
            "Cache-Control": "no-cache",
        })
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")).get("response", {})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            last = e
            # 实测同一时刻 CCU 端点超时、评论端点却 200 —— 抖动是逐连接的，
            # 因此靠退避重试而不是换个端点绕。第 4 次仍失败才降级到旧接口。
            time.sleep(1.5 * (2 ** i))

    # 新接口不可用时退回旧端点（历史上它在部分出口 IP 会 302，故只作降级）
    legacy = "%s?json=1&filter=%s&language=%s&purchase_type=%s&cursor=%s&num_per_page=%d" % (
        ENDPOINT_LEGACY % appid, filter_, language, purchase_type,
        urllib.parse.quote(cursor or "*"), num)
    try:
        req = urllib.request.Request(legacy, headers={"User-Agent": "NEXUS-reviews/1.0"})
        with OPENER.open(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        if body.get("success") == 1 or "reviews" in body:
            return body
        raise ReviewFetchError("旧端点返回 success=%s" % body.get("success"))
    except Exception as e:
        raise ReviewFetchError("两个端点均失败：新接口 %s；旧接口 %s" % (last, e))


def collect(appid, mode="recent", pages=3, num=100, language=DEFAULT_LANGUAGE):
    """翻若干页。返回 (reviews, next_cursor, query_summary)。

    mode=recent   ：增量，服务端每页固定 5 条
    mode=backfill ：回溯，filter=all + day_range，每页最多 100 条
    """
    if mode == "backfill":
        filter_, num, day_range = "all", num, 365
    else:
        filter_, num, day_range = "recent", num, None

    out, cursor, summary, seen = [], "*", {}, set()
    for _ in range(pages):
        resp = fetch_page(appid, cursor=cursor, filter_=filter_, num=num,
                          language=language, day_range=day_range)
        rows = resp.get("reviews") or []
        summary = resp.get("query_summary") or summary
        for r in rows:
            pr = parse_review(r)
            rid = pr["review_id"]
            # 游标未前进时服务端会重复返回同一页，页内去重避免下游重复处理
            if rid and rid not in seen:
                seen.add(rid)
                out.append(pr)
        nxt = resp.get("cursor")
        # 没有新数据时服务端会回同一个游标 —— 不判定就会无限翻同一页
        if not rows or not nxt or nxt == cursor:
            break
        cursor = nxt

    return out, cursor, summary


def upsert_reviews(conn, appid, reviews, ts=None):
    ts = ts or store.now_utc()
    rows = [(appid, r["review_id"], r["created_ts"], r["updated_ts"], r["voted_up"],
             r["language"], r["playtime_minutes"], r["votes_up"], r["refunded"],
             r["text"], ts) for r in reviews]
    conn.executemany(
        """INSERT INTO steam_reviews
           (appid, review_id, created_ts, updated_ts, voted_up, language,
            playtime_minutes, votes_up, refunded, text, fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(appid, review_id) DO UPDATE SET
             updated_ts=excluded.updated_ts,
             voted_up=excluded.voted_up,
             language=excluded.language,
             playtime_minutes=excluded.playtime_minutes,
             votes_up=excluded.votes_up,
             refunded=excluded.refunded,
             text=excluded.text,
             fetched_at=excluded.fetched_at""",
        rows,
    )
    return len(rows)


def get_cursor(conn, appid, stream):
    r = conn.execute(
        "SELECT cursor FROM collection_cursors WHERE appid=? AND stream=?", (appid, stream)
    ).fetchone()
    return r[0] if r else None


def set_cursor(conn, appid, stream, cursor):
    conn.execute(
        """INSERT INTO collection_cursors(appid, stream, cursor, updated_at) VALUES(?,?,?,?)
           ON CONFLICT(appid, stream) DO UPDATE SET cursor=excluded.cursor,
                                                   updated_at=excluded.updated_at""",
        (appid, stream, cursor, store.now_utc()),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appids", default="", help="逗号分隔；留空读配置的启用列表")
    ap.add_argument("--mode", choices=("recent", "backfill"), default="recent")
    ap.add_argument("--pages", type=int, default=3, help="每次翻几页（recent 每页 5 条）")
    ap.add_argument("--num", type=int, default=100, help="backfill 模式的每页条数（上限 100）")
    ap.add_argument("--once", action="store_true", help="采一轮后退出（当前仅支持单轮）")
    ap.add_argument("--status", action="store_true", help="只打印库内现状，不采集")
    ap.add_argument("--db", default=os.environ.get("STEAM_DB", store.DEFAULT_DB))
    ap.add_argument("--config", default=os.environ.get("GAMES_CONFIG"))
    args = ap.parse_args()

    global DB
    DB = store.init_db(args.db)

    if args.status:
        with store.session(DB) as conn:
            rows = conn.execute(
                """SELECT g.name, COUNT(r.review_id), MIN(r.created_ts), MAX(r.created_ts)
                   FROM games g LEFT JOIN steam_reviews r ON r.appid=g.appid
                   GROUP BY g.appid ORDER BY g.appid""").fetchall()
            for name, n, lo, hi in rows:
                print("%-16s %5d 条  %s ~ %s" % (name, n, lo or "-", hi or "-"))
        return 0

    cfg = game_config.load_games(args.config)
    if args.appids:
        ids = {int(x) for x in args.appids.split(",") if x.strip()}
        targets = [g for g in cfg["games"] if g["appid"] in ids]
    else:
        targets = [g for g in cfg["games"] if g["enabled"]]

    with store.session(DB) as conn:
        store.sync_games(conn, cfg["games"])

    print("DB  : %s" % DB)
    print("模式: %s | 每游戏 %d 页" % (args.mode, args.pages))

    total = 0
    for g in targets:
        started = time.time()
        try:
            reviews, cursor, summary = collect(
                g["appid"], mode=args.mode, pages=args.pages, num=args.num)
        except ReviewFetchError as e:
            print("  %-16s 失败：%s" % (g["name"], e))
            with store.session(DB) as conn:
                store.log_collection(conn, g["appid"], "reviews", "error",
                                     error=str(e)[:500])
            continue

        with store.session(DB) as conn:
            n = upsert_reviews(conn, g["appid"], reviews)
            if args.mode == "recent" and cursor:
                set_cursor(conn, g["appid"], "recent", cursor)
            store.log_collection(conn, g["appid"], "reviews", "ok",
                                 duration_ms=int((time.time() - started) * 1000), rows=n)
        total += n
        extra = ""
        if summary.get("total_reviews"):
            extra = " | 全站 %s 条，好评率 %.1f%%" % (
                format(summary["total_reviews"], ","),
                100.0 * summary.get("total_positive", 0) / max(1, summary["total_reviews"]))
        print("  %-16s 取到 %3d 条%s" % (g["name"], n, extra))

    print("done. 共 %d 条" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
