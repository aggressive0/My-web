"""
评论样本的读写与解析（无重型依赖）。

从 sentiment_publish 拆出来的原因：
    主题管线只需要「读 jsonl + 解析」，但它原先 `import sentiment_publish`，
    而后者顶部 `import numpy`（情感模型需要）。结果是：纯规则的 20 行解析
    被 2GB 的 ML 依赖绑住，CI 里把主题步骤排在装依赖之前就直接崩。

本模块只依赖标准库，任何下游都能安全 import。
"""

import base64
import time
from collections import Counter

import ci_publish as P

SAMPLES = "reviews-samples.jsonl"


def read_text(path, repo, token):
    """读 data 分支上的文本文件（jsonl）。不存在返回 (None, None)。"""
    for _ in range(3):
        try:
            res = P.gh("GET", "/repos/%s/contents/%s?ref=%s" % (repo, path, P.BRANCH), token=token)
            return base64.b64decode(res["content"]).decode("utf-8", "replace"), res.get("sha")
        except RuntimeError as e:
            if " 404 " in str(e):
                return None, None
            time.sleep(2)
    return None, None


def parse_samples(text):
    """返回 (rows, skipped)。坏行记数而不是静默丢弃。

    字段约定（与 reviews_publish 的输出对齐）：
        appid / id / created / text / voted_up / lang
    """
    rows, skipped = [], Counter()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        import json
        try:
            d = json.loads(line)
        except ValueError:
            skipped["bad_json"] += 1
            continue

        body = (d.get("text") or "").strip()
        if not body:
            skipped["no_text"] += 1
            continue
        if not d.get("created"):
            skipped["no_date"] += 1
            continue

        rows.append({
            "appid": d.get("appid"),
            "id": d.get("id"),
            "date": d["created"][:10],
            "text": body,
            "voted_up": bool(d.get("voted_up")),
            "lang": d.get("lang") or "unknown",
        })

    # 同一 id 只保留最后一次（样本文件可能因重建而包含旧副本）
    dedup = {}
    for r in rows:
        dedup[(r["appid"], r["id"])] = r
    return list(dedup.values()), skipped
