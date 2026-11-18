"""
判定 Steam 评论新接口的正确请求协议，以及 languages 是否真正生效。

结论修正史（这个脚本本身被推翻了两次，所以要写清楚）：
  第一版：手写 store.steampowered.com/appreviews 的旧 URL → 被反爬跳回首页，
          10 次全返回 HTML。测的不是生产路径，结论无效。
  第二版：改调采集器的 fetch_page()（生产同通道），10/10 组合全 english，
          得出「该通道不支持语言过滤」。
  第三版（本脚本）：怀疑真正的根因是**参数格式**——
          采集器请求新接口 IUserReviewsService/GetAppReviews/v1/，
          但参数仍按旧接口的扁平 query 传（appid/filter/language/num_per_page），
          新接口要求 `input_json`，字段是 `languages`（数组）与整数枚举。

本脚本对同一款游戏发四种请求，比较语言分布：
  A legacy            —— 现在的写法（扁平 query）
  B input_json(all)   —— languages=["all"] + filter=1(Recent)
  C input_json(schinese)
  D input_json(all, filter=0) —— 排除 filter 枚举取值错误的干扰

判据：
  A 全 english 而 B 出现多语言 → 参数格式就是根因，修采集器即可
  B/C/D 仍全 english          → 端点确不提供多语言，需换通道（key/代理）
  全部请求失败                → 网络或鉴权问题，不能据此下任何结论

用法：
    python backend/scripts/probe_review_language.py
    python backend/scripts/probe_review_language.py --apps 730 --json
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import steam_reviews_collector as R  # noqa: E402

UA = "NEXUS-reviews/1.0 (student research)"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 新接口枚举（IUserReviewsService）
FILTER_RECENT = 1
FILTER_ALL = 0
REVIEW_TYPE_ALL = 0
PURCHASE_ALL = 1


def _request(url, timeout=25, retries=2):
    last = None
    for i in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            return json.loads(raw), None
        except urllib.error.HTTPError as e:
            return None, "HTTP %s" % e.code
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:60])
            time.sleep(1.2 * (2 ** i))
    return None, last


def summarize(body):
    """新接口把结果放在 response 里，旧接口在顶层；两种都兼容。"""
    resp = body.get("response", body) if isinstance(body, dict) else {}
    reviews = resp.get("reviews") or []
    langs = Counter((r.get("language") or "unknown") for r in reviews)
    return {
        "n": len(reviews),
        "langs": dict(langs.most_common(8)),
        "has_cursor": bool(resp.get("cursor")),
    }


def call_legacy(appid, language="all"):
    """现在的写法：扁平 query（疑似参数格式错误）。"""
    params = {
        "appid": appid, "filter": "recent", "num_per_page": 100,
        "language": language, "purchase_type": "all", "cursor": "*",
    }
    url = R.ENDPOINT_NEW + "?" + urllib.parse.urlencode(params)
    body, err = _request(url)
    return {"error": err} if err else summarize(body)


def call_input_json(appid, languages, filter_=FILTER_RECENT, num=100):
    """官方格式：input_json + languages 数组 + 整数枚举。"""
    payload = {
        "appid": appid,
        "filter": filter_,
        "languages": languages,
        "review_type": REVIEW_TYPE_ALL,
        "purchase_type": PURCHASE_ALL,
        "num_per_page": num,
        "cursor": "*",
    }
    url = R.ENDPOINT_NEW + "?" + urllib.parse.urlencode({"input_json": json.dumps(payload)})
    body, err = _request(url)
    return {"error": err} if err else summarize(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="730,1091500")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    apps = [int(x) for x in args.apps.split(",") if x.strip()]
    rows = []

    for appid in apps:
        variants = [
            ("A_legacy", lambda a=appid: call_legacy(a)),
            ("B_input_json_all", lambda a=appid: call_input_json(a, ["all"])),
            ("C_input_json_schinese", lambda a=appid: call_input_json(a, ["schinese"])),
            ("D_input_json_all_f0", lambda a=appid: call_input_json(a, ["all"], filter_=FILTER_ALL)),
        ]
        for name, fn in variants:
            r = fn()
            r.update({"appid": appid, "variant": name})
            rows.append(r)
            if not args.json:
                if "error" in r:
                    print("app=%-8d %-22s → 失败：%s" % (appid, name, r["error"]))
                else:
                    print("app=%-8d %-22s → n=%-3d 语言 %s"
                          % (appid, name, r["n"], r["langs"]))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    ok = [r for r in rows if "error" not in r]
    multi = [r for r in ok if any(k != "english" for k in r["langs"])]
    print()
    if multi:
        print("结论：出现非英语样本的变形 → %s" % sorted({r["variant"] for r in multi}))
        print("      → 参数格式就是根因，按该格式修采集器")
    elif ok:
        print("结论：所有变形仍只返回 english → 该端点不提供多语言，需要换通道")
    else:
        print("结论：所有请求失败，先查网络/鉴权，不能据此下任何结论")
    return 0


if __name__ == "__main__":
    sys.exit(main())
