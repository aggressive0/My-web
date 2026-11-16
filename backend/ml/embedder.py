"""
多语言句向量封装（sentence-transformers / paraphrase-multilingual-MiniLM-L12-v2）。

为什么要包一层，而不是在脚本里直接 `from sentence_transformers import ...`：
  1. 导入 torch 要 3–5 秒、占几百 MB 内存。只要有一个模块在顶部 import，
     所有 CLI（包括 --help）都会变慢，CI 上还会白装依赖。
  2. 没有装 torch 时应该给出可执行的提示，而不是一屏 ImportError 栈。
  3. 测试需要可注入的假编码器 —— 46 项离线测试不能在 CI 上去下载 500MB 模型。

模型选择理由：
  paraphrase-multilingual-MiniLM-L12-v2 覆盖 50+ 语言（含中英），384 维，
  CPU 上 900 条文本约 3–5 秒。它本身是句向量模型而不是情感分类器，
  所以后面必须挂一个在 Steam 评论上训过的分类头 —— 这点不能含糊。
"""

import hashlib
import os

import numpy as np

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384


class EmbedderUnavailable(RuntimeError):
    pass


class SentenceTransformerEmbedder:
    """真实编码器。构造时才加载模型，避免 import 即付出代价。"""

    def __init__(self, model_name=MODEL_NAME, device="cpu"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbedderUnavailable(
                "缺少 sentence-transformers/torch。安装：\n"
                "  pip install --index-url https://download.pytorch.org/whl/cpu torch\n"
                "  pip install sentence-transformers\n"
                "（原始错误：%s）" % e
            )
        # 国内网络建议设 HF_ENDPOINT=https://hf-mirror.com 再运行
        # source 记录「实际用的是哪来的」（hub 名或本地目录），
        # 而 model_name 永远给规范名 —— 否则发布出去会变成一串缓存哈希。
        self.source = model_name
        self.model_name = MODEL_NAME
        self.model = SentenceTransformer(model_name, device=device)

    def encode(self, texts, batch_size=32):
        """返回 L2 归一化后的 (n, 384) float32；空输入返回 (0, 384)。"""
        if not texts:
            return np.zeros((0, DIM), dtype="float32")
        vecs = self.model.encode(
            list(texts),
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype="float32")


class HashEmbedder:
    """确定性假编码器：仅用于测试与离线联调。

    用字符 n-gram 的哈希投影模拟「语义相近 → 向量相近」的弱性质，
    足够的目的是验证管道接线（维度、归一化、批量、缓存），
    不适合任何真实判断 —— 名字里带 Hash 就是为了防止被误用。
    """

    name = "hash-embedder"

    def __init__(self, dim=DIM):
        self.dim = dim
        self.model_name = "hash-embedder(%d)" % dim

    def encode(self, texts, batch_size=None):
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            s = (t or "").strip().lower()
            if not s:
                continue                      # 空文本保持零向量，便于上层跳过
            for j in range(max(1, len(s) - 2)):
                gram = s[j:j + 3]
                h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


def build_embedder(kind="st"):
    if kind == "hash":
        return HashEmbedder()
    return SentenceTransformerEmbedder(os.environ.get("ST_MODEL", MODEL_NAME))
