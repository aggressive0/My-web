"""
判定 Steam 评论接口到底认不认 language 参数（一次性诊断脚本）。

背景：
    CI 一直用 `language=all` 拉评论（采集器 DEFAULT_LANGUAGE="all"），
    但 1013 条样本 100% 是 english —— PUBG(289) / Cyberpunk(409) 这种
    中文俄文评论量巨大的游戏一条外语都没有，统计上不可能。

第一版探针的错误（已修）：
    手写 store.steampowered.com/appreviews 的 URL，结果 10 次全部返回 HTML ——
    Steam 反爬把请求跳回首页。而生产用的是 api.steampowered.com 的
    IUserReviewsService，走的是完全不同的通道。
    **测不到生产路径的探针，结论没有意义。**

本版直接调用采集器的 fetch_page()：与生产同一条通道、同一套重试与降级逻辑，
只变 language 一个变量。

用法（本地被反爬拦时请在 CI 里跑）：
    python backend/scripts/probe_review_language.py
    python backend/scripts/probe_review_language.py --apps 730,1091500 --json
"""

import argparse
import json
import os
import sys
from collections import Counter

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import steam_reviews_collector as R  # noqa: E402

# (filter, language)：两个 filter × 三种 language
MATRIX = [
    ("recent", "all"),
    ("recent", "schinese"),
    ("all", "all"),
    ("all", "schinese"),
    ("all", "russian"),
]


def probe(appid, filter_, language):
    try:
        resp = R.fetch_page(appid, cursor="*", filter_=filter_, num=100,
                            language=language, purchase_type="all")
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:80])}

    if not isinstance(resp, dict):
        return {"ok": False, "error": "返回非字典：%s" % type(resp).__name__}
    if resp.get("success") not in (1, True, None):
        return {"ok": False, "error": "success=%s" % resp.get("success"), "raw_keys": list(resp)[:6]}

    reviews = resp.get("reviews") or []
    langs = Counter((r.get("language") or "unknown") for r in reviews)
    return {"ok": True, "count": len(reviews), "langs": dict(langs.most_common(8))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="730,1091500", help="逗号分隔的 appid")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    apps = [int(x) for x in args.apps.split(",") if x.strip()]
    results = []

    for appid in apps:
        for filter_, lang in MATRIX:
            r = probe(appid, filter_, lang)
            r.update({"appid": appid, "filter": filter_, "language": lang})
            results.append(r)
            if not args.json:
                if r["ok"]:
                    print("app=%-8d filter=%-6s language=%-8s → n=%-3d 语言 %s"
                          % (appid, filter_, lang, r["count"], r["langs"]))
                else:
                    print("app=%-8d filter=%-6s language=%-8s → 失败：%s"
                          % (appid, filter_, lang, r["error"]))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    foreign = [r for r in results if r["ok"] and any(k != "english" for k in r["langs"])]
    print()
    if foreign:
        combos = sorted({(r["filter"], r["language"]) for r in foreign})
        print("结论：language 在以下组合生效 → %s" % combos)
        print("       → 把采集器切到该组合即可获得多语言样本")
    else:
        print("结论：所有组合都只返回 english —— 该通道不支持语言过滤")
        print("       → 需要换通道（官方 WebAPI key / 本地化 storefront / 地区代理）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
