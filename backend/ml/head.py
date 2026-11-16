"""
情感分类头：逻辑回归，输入 384 维 MiniLM 句向量，输出 P(推荐)。

为什么不是端到端微调 MiniLM：
  - 样本量只有几千条，微调 118M 参数必然过拟合，且 CPU 上训练要几十分钟；
  - 逻辑回归在这 384 维上只有 385 个参数，训练秒级、可复现、权重可解释；
  - 换句向量模型时只需重训分类头，不用重跑整个训练流程。

标签从哪来（必须说清楚，否则数字没有意义）：
  Steam 评论自带 voted_up（推荐 / 不推荐）。这是**弱标签**：
  它混入了玩法喜好、价格、优化、外挂等非情感因素，
  也受「差评轰炸」影响。因此本模型输出的是「推荐倾向」，
  不能直接当作「情绪极性」——文档与页面上都必须这样标注。

为什么不用第三方「情感数据集」：
  公开 Steam 评论数据集（如 Kaggle 上的）需要账号授权，无法在 CI 复现，
  也说不清版本。用自己采的真实评论 + 明确的弱标签定义，至少是**可复现且诚实**的。
"""

import json
import os
import time

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_VERSION = "v1"
DEFAULT_MODEL = os.path.join(MODEL_DIR, "sentiment-head-%s.joblib" % MODEL_VERSION)
METRICS_PATH = os.path.join(MODEL_DIR, "sentiment-head-%s.metrics.json" % MODEL_VERSION)


def _sklearn():
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_predict
        from sklearn.metrics import classification_report, confusion_matrix, f1_score
    except ImportError as e:      # pragma: no cover
        raise RuntimeError("缺少 scikit-learn：pip install scikit-learn（%s）" % e)
    return LogisticRegression, cross_val_predict, classification_report, confusion_matrix, f1_score


def train(X, y, C=1.0, seed=42):
    """训练并返回 (model, metrics)。metrics 里必须带样本量与类别分布。"""
    LogisticRegression, cross_val_predict, classification_report, confusion_matrix, f1_score = _sklearn()

    X = np.asarray(X, dtype="float64")
    y = np.asarray(y).astype(int)
    if len(X) != len(y):
        raise ValueError("特征与标签长度不一致：%d vs %d" % (len(X), len(y)))
    if len(set(y.tolist())) < 2:
        raise ValueError("标签只有一个类别，无法训练分类器")

    model = LogisticRegression(C=C, max_iter=2000, class_weight="balanced", random_state=seed)

    # 交叉验证只在样本足够时做；否则指标会误导
    cv_metrics = None
    if len(y) >= 50:
        pred = cross_val_predict(model, X, y, cv=5)
        cv_metrics = {
            "cv_folds": 5,
            "cv_f1_macro": round(float(f1_score(y, pred, average="macro")), 4),
            "confusion_matrix": confusion_matrix(y, pred).tolist(),
            "report": classification_report(y, pred, digits=3, output_dict=True,
                                            target_names=["not_recommend", "recommend"]),
        }

    model.fit(X, y)

    metrics = {
        "model_version": MODEL_VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_samples": int(len(y)),
        "n_positive": int(y.sum()),
        "n_negative": int(len(y) - y.sum()),
        "feature_dim": int(X.shape[1]),
        "C": C,
        "label_definition": "voted_up（Steam 推荐/不推荐，弱标签，非纯情绪极性）",
        "train_accuracy": round(float(model.score(X, y)), 4),
        "cv": cv_metrics,
    }
    return model, metrics


def save(model, metrics, path=DEFAULT_MODEL, metrics_path=METRICS_PATH):
    try:
        import joblib
    except ImportError:            # pragma: no cover
        raise RuntimeError("缺少 joblib：pip install joblib")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(model, path)
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return path, metrics_path


def load(path=DEFAULT_MODEL):
    try:
        import joblib
    except ImportError:            # pragma: no cover
        raise RuntimeError("缺少 joblib：pip install joblib")
    if not os.path.exists(path):
        raise FileNotFoundError("找不到分类头：%s（先运行 backend/ml/train_head.py）" % path)
    return joblib.load(path)


def predict_proba(model, X):
    X = np.asarray(X, dtype="float64")
    if X.size == 0:
        return np.zeros((0,), dtype="float32")
    return model.predict_proba(X)[:, 1].astype("float32")


# 中性带：0.4–0.6 之间不强行二分类。
# 弱标签模型在中间区域的输出本来就不可信，硬分只会制造假精度。
NEUTRAL_LOW = 0.4
NEUTRAL_HIGH = 0.6


def band(p):
    if p >= NEUTRAL_HIGH:
        return "pos"
    if p <= NEUTRAL_LOW:
        return "neg"
    return "neu"


def confidence(p):
    """置信度 = 离 0.5 的距离 ×2；中性样本天然低置信，便于前端弱化展示。"""
    return round(abs(float(p) - 0.5) * 2, 3)
