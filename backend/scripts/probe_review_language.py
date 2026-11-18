"""
判定 Steam 评论接口到底认不认 language 参数（一次性诊断脚本）。

背景：
    CI 一直用 `language=all` 拉评论（采集器 DEFAULT_LANGUAGE="all"），
    但 1013 条样本 100% 是 english —— PUBG(289) / Cyberpunk(409) 这种
    中文俄文评论量巨大的游戏一条外语都没有，统计上不可能。

由此产生一个更具体的假设：
    `filter=recent` 不仅忽略 `num_per_page`（已在采集器里记录），
    可能**同样忽略 `language`**；只有 `filter=all` 才让这两个参数生效。

本脚本就是来判这个假设，不做任何写入：
    对每个 (appid, filter, language) 组合发一次请求，
    统计返回评论的语言分布，打印成表。

用法（本地被 Steam 反爬拦时，请在 CI 里跑）：
    python backend/scripts/probe_review_language.py
    python backend/scripts/probe_review_language.py --apps 730,1091500 --pages 1
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 尽量绕开本地代理：CI 没有代理，本地有代理反而更稳
OPENER = urllib.request.build_opener()

BASE = "https://store.steampowered.com/appreviews/%d"

# (filter, language) 组合：覆盖两个 filter × 三种 language
MATRIX = [
    ("recent", "all"),
    ("recent", "schinese"),
    ("all", "all"),
    ("all", "schinese"),
    ("all", "russian"),
]


def fetch(appid, filter_, language, num=100, timeout=25):
    url = "%s?json=1&filter=%s&language=%s&num_per_page=%d&purchase_type=all" % (
        BASE % appid, filter_, language, num)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "Steam_Language=english; birthtime=628473601",
    })
    try:
        with OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": "HTTP %s" % e.code}
    except Exception as e:                       # 超时、连接失败
        return {"ok": False, "status": None, "error": type(e).__name__}

    if "json" not in ctype.lower():
        # Steam 反爬时会把请求 302 到首页，返回的是 HTML
        return {"ok": False, "status": 200, "error": "非 JSON（%s）" % ctype.split(";")[0]}
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"ok": False, "status": 200, "error": "JSON 解析失败"}

    reviews = doc.get("reviews") or []
    langs = Counter(r.get("language") for r in reviews)
    return {"ok": True, "count": len(reviews), "langs": dict(langs.most_common(6)),
            "success_flag": doc.get("success")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="730,1091500", help="逗号分隔的 appid")
    ap.add_argument("--json", action="store_true", help="只输出 JSON，便于 CI 日志解析")
    args = ap.parse_args()

    apps = [int(x) for x in args.apps.split(",") if x.strip()]
    results = []

    for appid in apps:
        for filter_, lang in MATRIX:
            r = fetch(appid, filter_, lang)
            r.update({"appid": appid, "filter": filter_, "language": lang})
            results.append(r)
            if not args.json:
                if r["ok"]:
                    print("app=%-8d filter=%-6s language=%-8s → n=%-3d 语言分布 %s"
                          % (appid, filter_, lang, r["count"], r["langs"]))
                else:
                    print("app=%-8d filter=%-6s language=%-8s → 失败：%s"
                          % (appid, filter_, lang, r["error"]))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))

    # 自动结论：只要出现非 english 的语言，就说明 language 参数在某个组合下是生效的
    foreign = [r for r in results if r["ok"] and any(k and k != "english" for k in r["langs"])]
    if not args.json:
        print()
        if foreign:
            combos = sorted({(r["filter"], r["language"]) for r in foreign})
            print("结论：language 参数在以下组合生效 → %s" % combos)
        else:
            print("结论：所有组合都只返回 english，language 参数在当前通道完全无效")


if __name__ == "__main__":
    sys.exit(main())
