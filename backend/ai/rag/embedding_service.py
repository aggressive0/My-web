"""
Embedding 服务（Phase 6.2）。

为什么用 sentence-transformers + CPU，而不是 Ollama 的 /api/embed：
生成依赖 Ollama 是合理的（本来就要模型），检索再依赖它就是设计缺陷——
Ollama 一挂，连「查一下知识库里有什么」都做不了。两者必须能独立失败。

模型维度不写死：从模型自身读（方案 §3.4 明确要求）。
本文件在 import 阶段不碰 torch / sentence-transformers，
让 `/api/ai/health` 能在不加载 2GB 模型的前提下回答「能不能用」。
"""

import importlib.util
import os
import threading
import time

from .vector_store import VectorStoreUnavailable


class EmbeddingUnavailable(RuntimeError):
    pass


_LOAD_LOCK = threading.Lock()


class EmbeddingService:
    def __init__(self, config, model=None):
        self.config = config
        self._model = model            # 允许注入（测试用假 embedder）
        self._dim = None
        self._lock = _LOAD_LOCK
        self.last_load_ms = None
        self.embedding_model = config.embedding_model

    # ------------------------------------------------------------------ 内部
    def _cached_snapshot_path(self):
        """只在本地缓存索引里找路径，不发任何网络请求。

        不能用 snapshot_download(local_files_only=True)：实测它仍会去 HF 核对
        revision（本机就出现过「模型明明在缓存里，却报了 30 秒网络警告」的情况）。
        try_to_load_from_cache 是纯本地查询，这才是「在不在缓存」的可靠判据。

        必须同时确认**权重文件**存在：只缓存了 config.json 的半成品目录会让
        健康检查说谎（这不是假设，是我第一次下载时真的踩到的状态）。
        """
        try:
            from huggingface_hub import try_to_load_from_cache
        except Exception:
            return None
        for name in ("model.safetensors", "pytorch_model.bin"):
            hit = try_to_load_from_cache(self.config.embedding_model, name)
            if isinstance(hit, str) and os.path.isfile(hit):
                return os.path.dirname(hit)
        return None

    def _resolve_source(self):
        """优先用本地缓存路径，避免离线环境去连 huggingface 卡住半小时。"""
        endpoint = os.environ.get("EMBEDDING_HF_ENDPOINT")
        if endpoint and not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = endpoint
        return self._cached_snapshot_path() or self.config.embedding_model

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:          # 双检：并发首次调用只加载一次
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as e:
                raise EmbeddingUnavailable(
                    "sentence-transformers 未安装，无法做向量化") from e

            started = time.time()
            try:
                model = SentenceTransformer(self._resolve_source(),
                                            device=self.config.embedding_device)
            except Exception as e:
                # 消息里不放模型路径：它属于部署内部信息，原始异常进 cause 交给日志
                raise EmbeddingUnavailable(
                    "加载 embedding 模型失败（%s）" % self.config.embedding_model) from e
            self._model = model
            self.last_load_ms = int((time.time() - started) * 1000)
            return model

    # ------------------------------------------------------------------ 接口
    @property
    def dim(self):
        if self._dim is None:
            model = self._load()
            getter = getattr(model, "get_sentence_embedding_dimension", None)
            if callable(getter):
                self._dim = int(getter())
            else:                                 # 假 embedder 没有这个方法
                self._dim = len(self.encode(["dimension probe"])[0])
        return self._dim

    @property
    def loaded(self):
        return self._model is not None

    def encode(self, texts, batch_size=None):
        """返回 list[list[float]]，已做 L2 归一化（cosine 检索的前提）。"""
        texts = [t for t in (texts or []) if t is not None]
        if not texts:
            return []
        model = self._load()
        bs = batch_size or self.config.embedding_batch_size
        try:
            vectors = model.encode(texts, batch_size=bs, normalize_embeddings=True,
                                   show_progress_bar=False)
        except TypeError:
            # 注入的假 embedder 可能不支持这些关键字，退回最小调用
            vectors = model.encode(texts)
        return [[float(x) for x in vec] for vec in vectors]

    def health(self):
        """不加载模型的前提下给出如实状态。

        available 的语义是「现在就能用」：依赖在、模型已在本地缓存（或已加载）。
        模型没缓存时说 true 是不诚实的——第一次调用会去下载 2.1GB。
        """
        info = {
            "name": "embedding",
            "available": False,
            "detail": None,
            "model": self.config.embedding_model,
            "device": self.config.embedding_device,
            "loaded": self._model is not None,
            "dim": self._dim,
        }
        if importlib.util.find_spec("sentence_transformers") is None:
            info["detail"] = "sentence-transformers 未安装"
            return info
        if self._model is not None:
            info["available"] = True
            info["cached"] = True
            return info
        if self._cached_snapshot_path():
            info["available"] = True
            info["cached"] = True
        else:
            info["cached"] = False
            info["detail"] = ("模型 %s 不在本地缓存；首次使用需下载，"
                              "离线环境请先预下载或设置 HF_ENDPOINT"
                              % self.config.embedding_model)
        return info


def build_embedding_service(config):
    return EmbeddingService(config)
