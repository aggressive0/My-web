"""
训练情感分类头：本地 steam_reviews → MiniLM 句向量 → 逻辑回归 → backend/ml/models/*.joblib

用法：
    # 先攒够样本（本机库）
    python backend/collectors/steam_reviews_collector.py --mode backfill --pages 5 --once

    # 训练（首次会自动下载 ~470MB 的 MiniLM 模型）
    HF_ENDPOINT=https://hf-mirror.com python backend/ml/train_head.py

    # 只想验证管道、不想下模型：
    python backend/ml/train_head.py --embedder hash

产出：
    backend/ml/models/sentiment-head-v1.joblib          分类头（385 个参数，几十 KB）
    backend/ml/models/sentiment-head-v1.metrics.json    训练指标与样本量

为什么把模型文件提交进仓库：
    它只有几十 KB，且必须与「训练时的句向量模型 + 版本号」严格对应。
    放仓库里能让 CI 与本地使用完全相同的权重，避免「线上跑的是另一个模型」。
    真正的 470MB 句向量模型不进仓库，由 sentence-transformers 按名字下载。
"""

import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import embedder as E      # noqa: E402
import head as H          # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND, "collectors"))
import db as store        # noqa: E402

CACHE_DIR = os.path.join(H.MODEL_DIR, "cache")


def load_dataset(db_path, min_len=20, per_game=None, only_balanced=None):
    """从本地库读样本。返回 (rows, X_texts, y)。"""
    sql = """SELECT appid, review_id, text, voted_up, language, created_ts
             FROM steam_reviews
             WHERE text IS NOT NULL AND length(text) >= ?"""
    with store.session(db_path) as conn:
        rows = conn.execute(sql, (min_len,)).fetchall()

    out = []
    seen_text = set()
    for r in rows:
        text = (r["text"] or "").strip()
        key = hashlib.md5(text.lower().encode("utf-8")).hexdigest()
        if key in seen_text:          # 同一段话复制粘贴的评论不重复计数
            continue
        seen_text.add(key)
        out.append({
            "appid": r["appid"],
            "review_id": r["review_id"],
            "text": text,
            "y": int(r["voted_up"]),
            "language": r["language"],
            "created_ts": r["created_ts"],
        })

    if per_game:
        grouped = {}
        for row in out:
            grouped.setdefault(row["appid"], []).append(row)
        out = []
        for appid, items in grouped.items():
            out.extend(items[:per_game])

    return out


def dedupe_balance(rows):
    """按类别下采样到 1:1.5 以内，避免 80/20 的极度倾斜让 CV 看起来很好但没意义。"""
    pos = [r for r in rows if r["y"] == 1]
    neg = [r for r in rows if r["y"] == 0]
    if not pos or not neg:
        return rows
    minority, majority = (neg, pos) if len(neg) < len(pos) else (pos, neg)
    cap = int(len(minority) * 1.5)
    if len(majority) > cap:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(majority), size=cap, replace=False)
        majority = [majority[i] for i in sorted(idx)]
    return minority + majority


def embed_with_cache(texts, embedder, cache_key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.md5(("%s|%d|%s" % (cache_key, len(texts),
                                   hashlib.md5("\n".join(texts).encode("utf-8")).hexdigest()))
                    .encode("utf-8")).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, "emb-%s.npy" % h)
    if os.path.exists(path):
        print("复用缓存向量：%s" % path)
        return np.load(path)
    vecs = embedder.encode(texts)
    np.save(path, vecs)
    return vecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("STEAM_DB", store.DEFAULT_DB))
    ap.add_argument("--embedder", choices=("st", "hash"), default="st")
    ap.add_argument("--min-len", type=int, default=20, help="过短的评论信息量不足，默认丢弃")
    ap.add_argument("--per-game", type=int, default=None, help="每款游戏最多取多少条")
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--no-balance", action="store_true", help="不做类别下采样")
    args = ap.parse_args()

    rows = load_dataset(args.db, args.min_len, args.per_game)
    if not args.no_balance:
        before = len(rows)
        rows = dedupe_balance(rows)
        print("样本：%d 条（去重后），下采样到 %d 条" % (before, len(rows)))
    else:
        print("样本：%d 条（去重后）" % len(rows))

    if len(rows) < 50:
        raise SystemExit("样本不足 50 条，训练没有意义。先跑 backfill 攒数据。")

    y = np.array([r["y"] for r in rows])
    print("类别：推荐 %d / 不推荐 %d" % (int(y.sum()), int(len(y) - y.sum())))

    encoder = E.build_embedder(args.embedder)
    print("编码器：%s" % getattr(encoder, "model_name", args.embedder))
    texts = [r["text"] for r in rows]
    X = embed_with_cache(texts, encoder, getattr(encoder, "model_name", args.embedder))
    print("向量：%s" % (X.shape,))

    model, metrics = H.train(X, y, C=args.C)
    metrics["embedding_model"] = getattr(encoder, "model_name", args.embedder)
    metrics["sources"] = {
        "games": sorted({r["appid"] for r in rows}),
        "languages": sorted({r["language"] for r in rows if r["language"]}),
        "date_range": [min(r["created_ts"] for r in rows), max(r["created_ts"] for r in rows)],
    }

    path, mpath = H.save(model, metrics)
    print("\n已保存：%s" % path)
    print("指标：%s" % json.dumps({k: v for k, v in metrics.items()
                                   if k in ("n_samples", "train_accuracy", "cv")},
                                  ensure_ascii=False)[:600])

    if metrics.get("cv"):
        cv = metrics["cv"]
        print("\n5 折交叉验证 macro-F1 = %.3f" % cv["cv_f1_macro"])
        print("混淆矩阵（行=真实，列=预测）: %s" % cv["confusion_matrix"])
        print("\n注意：标签是 voted_up（弱标签），F1 衡量的是「能否复现推荐/不推荐的划分」，")
        print("      不等于「能否判断情绪」。请勿把该指标写成情感识别准确率。")


if __name__ == "__main__":
    main()
