"""
把评论采集结果发布到 data 分支（Phase 2 的 CI 侧）。

为什么 CI 侧不落 SQLite：
    Actions 每次都是全新 runner，SQLite 无法跨运行保留。所以这里走「无状态」路线：
    上一次的结果就在 data 分支的文件里，本次读取 → 合并 → 写回。

产出三个文件（data 分支根目录）：
    reviews-daily.json       按游戏 × 日期聚合（总量/推荐率/语言/时长分档）
    reviews-samples.jsonl    可追溯样本（每行一条，滚动上限，供「热点→评论 ID」引用）
    collection-status.json   每款游戏最后一次成功时间、条数、错误

去重：
    reviews-state.json 保存已计入的 review_id（每款上限 3000，FIFO 淘汰）。
    重复采集不会重复计数；超过上限后被淘汰的旧 id 若再次出现会计重一次，
    这是容量与精确度之间的折中 —— 见 docs/DATA_MIGRATION_PLAN.md §2.2 的取舍说明。

为什么样本是 .jsonl 而不是 .jsonl.gz：
    gz 是二进制，每次重写都会在 Git 里产生一个全新的 blob（无法 delta），
    一天 12 次运行就是 12 份完整副本。文本 JSONL 能被 Git 正常 delta 压缩，
    再配合行数上限，仓库增长反而更可控。
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_publish as P          # 复用 GitHub Contents API 的读写实现
import game_config
import steam_reviews_collector as R

DAILY = "reviews-daily.json"
STATE = "reviews-state.json"
SAMPLES = "reviews-samples.jsonl"
STATUS = "collection-status.json"

WINDOW_DAYS = 30
SEEN_CAP = 3000          # 每款游戏记住多少个已计数的 review_id
SAMPLES_CAP = 3000       # 样本文件总行数上限
MAX_PAGES = 3            # 每款每次最多翻几页（每页 100 条）


def put_text(path, text, sha, repo, token, message):
    body = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": P.BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        P.gh("PUT", "/repos/%s/contents/%s" % (repo, path), body, token)
    except RuntimeError as e:
        if "409" in str(e) or "422" in str(e):
            time.sleep(2)
            _, sha2 = P.get_remote(path, repo, token)
            if sha2:
                body["sha"] = sha2
            P.gh("PUT", "/repos/%s/contents/%s" % (repo, path), body, token)
        else:
            raise


def get_text(path, repo, token):
    """读非 JSON 文件（jsonl）。不存在时返回 (None, None)。"""
    for i in range(3):
        try:
            res = P.gh("GET", "/repos/%s/contents/%s?ref=%s" % (repo, path, P.BRANCH), token=token)
            return base64.b64decode(res["content"]).decode("utf-8", "replace"), res.get("sha")
        except RuntimeError as e:
            if " 404 " in str(e):
                return None, None
            if i == 2:
                return None, None
            time.sleep(2)
    return None, None


def day_of(ts):
    return (ts or "")[:10] or "unknown"


def playtime_bucket(minutes):
    if minutes is None:
        return "unknown"
    m = int(minutes)
    if m < 60:
        return "<1h"
    if m < 600:
        return "1-10h"
    if m < 3000:
        return "10-50h"
    return "50h+"


def merge_seen(prev_ids, new_ids, cap=SEEN_CAP):
    """已计数 id 的去重合并 + FIFO 淘汰。抽成函数是为了能离线测试容量边界。"""
    merged = list(dict.fromkeys(list(prev_ids) + list(new_ids)))
    return merged[-cap:]


def empty_day(date):
    return {"date": date, "total": 0, "positive": 0, "negative": 0,
            "languages": {}, "playtime": {}}


def merge_into_daily(game_doc, reviews):
    """把新评论并入按日聚合。返回实际新增条数（去重后）。"""
    days = {d["date"]: d for d in game_doc.get("days", [])}
    added = 0
    for r in reviews:
        d = days.get(day_of(r["created_ts"]))
        if d is None:
            d = empty_day(day_of(r["created_ts"]))
            days[day_of(r["created_ts"])] = d
        d["total"] += 1
        if r["voted_up"]:
            d["positive"] += 1
        else:
            d["negative"] += 1
        lang = r.get("language") or "unknown"
        d["languages"][lang] = d["languages"].get(lang, 0) + 1
        b = playtime_bucket(r.get("playtime_minutes"))
        d["playtime"][b] = d["playtime"].get(b, 0) + 1
        added += 1

    # 只保留窗口内的日期，按时间升序
    today = datetime.now(timezone.utc).date()
    keep = []
    for date, d in sorted(days.items()):
        try:
            age = (today - datetime.strptime(date, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if age <= WINDOW_DAYS:
            keep.append(d)
    game_doc["days"] = keep
    return added


def recompute_totals(game_doc):
    tot = sum(d["total"] for d in game_doc.get("days", []))
    pos = sum(d["positive"] for d in game_doc.get("days", []))
    game_doc["totals"] = {
        "total": tot,
        "positive": pos,
        "negative": tot - pos,
        "positive_rate": round(100.0 * pos / tot, 1) if tot else None,
        "days": len(game_doc.get("days", [])),
    }
    langs = Counter()
    for d in game_doc.get("days", []):
        langs.update(d.get("languages", {}))
    game_doc["top_languages"] = dict(langs.most_common(6))


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("缺少 GITHUB_TOKEN")

    cfg = game_config.load_games(os.environ.get("GAMES_CONFIG"))
    targets = [g for g in cfg["games"] if g["enabled"]]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    daily, sha_daily = P.get_remote(DAILY, repo, token)
    daily = daily or {"window_days": WINDOW_DAYS, "games": []}
    by_appid = {}
    for g in daily.get("games", []):
        try:
            by_appid[int(g["appid"])] = g
        except (TypeError, ValueError):
            continue

    state, sha_state = P.get_remote(STATE, repo, token)
    state = state or {"seen": {}}
    seen_all = {str(k): list(v) for k, v in (state.get("seen") or {}).items()}

    sample_text, sha_samples = get_text(SAMPLES, repo, token)
    sample_lines = [ln for ln in (sample_text or "").splitlines() if ln.strip()]

    status = {"updated_at": now, "games": []}
    total_new = 0

    for g in targets:
        appid, name = g["appid"], g["name"]
        started = time.time()
        entry = {"appid": appid, "slug": g["slug"], "name": name,
                 "last_run": now, "fetched": 0, "added": 0, "error": None}

        try:
            reviews, _cursor, summary = R.collect(appid, mode="recent", pages=MAX_PAGES)
        except R.ReviewFetchError as e:
            entry["error"] = str(e)[:300]
            status["games"].append(entry)
            print("  %-16s 失败：%s" % (name, entry["error"]))
            continue

        seen = set(seen_all.get(str(appid), []))
        fresh = [r for r in reviews if r["review_id"] not in seen]
        entry["fetched"] = len(reviews)

        if fresh:
            doc = by_appid.get(appid)
            if doc is None:
                doc = {"appid": appid, "slug": g["slug"], "name": name,
                       "color": g["color"], "days": []}
                by_appid[appid] = doc
            entry["added"] = merge_into_daily(doc, fresh)
            recompute_totals(doc)
            total_new += entry["added"]

            # 记录已计数 id（FIFO，超上限淘汰最旧的）
            seen_all[str(appid)] = merge_seen(seen_all.get(str(appid), []),
                                              [r["review_id"] for r in fresh])

            for r in fresh:
                sample_lines.append(json.dumps({
                    "appid": appid,
                    "id": r["review_id"],
                    "created": r["created_ts"],
                    "voted_up": bool(r["voted_up"]),
                    "lang": r.get("language"),
                    "playtime_min": r.get("playtime_minutes"),
                    "text": (r.get("text") or "")[:400],
                }, ensure_ascii=False, separators=(",", ":")))

        entry["duration_ms"] = int((time.time() - started) * 1000)
        if summary.get("total_reviews"):
            entry["app_total_reviews"] = summary["total_reviews"]
            entry["app_positive_rate"] = round(
                100.0 * summary.get("total_positive", 0) / max(1, summary["total_reviews"]), 1)
        status["games"].append(entry)
        print("  %-16s 取到 %3d 条，新增 %3d 条" % (name, len(reviews), entry["added"]))

    # 只保留配置里仍存在的游戏，避免配置删除后残留在文件里
    keep_ids = {g["appid"] for g in cfg["games"]}
    daily["games"] = [by_appid[a] for a in sorted(by_appid) if a in keep_ids]
    daily["updated_at"] = now

    # 写入顺序：聚合 → 样本 → 状态 → **最后**写去重集合。
    # seen 是「这批 id 已经计入聚合」的凭证，必须最后落盘：
    # 若它先写成功、聚合写失败，下一轮就会把这批评论当成已处理而永久漏计。
    P.put_remote(DAILY, daily, sha_daily, repo, token, "data: reviews daily %s" % now)
    put_text(SAMPLES, "\n".join(sample_lines[-SAMPLES_CAP:]) + "\n",
             sha_samples, repo, token, "data: reviews samples %s" % now)
    P.put_remote(STATUS, status, None, repo, token, "data: collection status %s" % now)
    P.put_remote(STATE, {"updated_at": now, "seen": seen_all},
                 sha_state, repo, token, "data: reviews state %s" % now)

    print("本轮新增 %d 条；样本 %d 行；聚合覆盖 %d 款游戏"
          % (total_new, min(len(sample_lines), SAMPLES_CAP), len(daily["games"])))


if __name__ == "__main__":
    main()
