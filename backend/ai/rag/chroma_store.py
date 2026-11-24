"""
ChromaDB 实现（Phase 6.2）。

三个实现细节值得写下来，因为它们都是踩过才知道的：

1. **collection 名**至少 3 个字符、只允许 [a-zA-Z0-9._-]，且首尾必须是字母数字。
   `get_or_create_collection('t')` 会直接抛 InvalidArgumentError。
2. **metadata 不接受 None 和 list**。所以全局文档的 app_id 用 -1 表示、
   tags 用逗号串表示。SQLite 侧仍然是 NULL，映射发生在写库这一层。
3. **cosine 空间下 distance = 1 - cos_sim**，相似度必须自己换算成 1 - distance。
   直接把 distance 当 score 会让「最相关」排到最后。
"""

import os

from .vector_store import (GLOBAL_APP_ID, Chunk, SearchHit, VectorStore,
                           VectorStoreUnavailable)

_BATCH = 64


class ChromaVectorStore(VectorStore):
    name = "chroma"

    def __init__(self, config, client=None):
        self.config = config
        self._client = client
        self._collection = None
        self.last_error = None

    # ------------------------------------------------------------------ 内部
    def _col(self):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
        except Exception as e:
            raise VectorStoreUnavailable("chromadb 未安装，无法使用向量库") from e
        try:
            os.makedirs(self.config.chroma_dir, exist_ok=True)
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self.config.chroma_dir)
            self._collection = self._client.get_or_create_collection(
                name=self.config.collection,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            raise VectorStoreUnavailable(
                "无法打开向量库（collection=%s）" % self.config.collection) from e
        return self._collection

    @staticmethod
    def _where(app_id, include_global=True):
        """检索阶段过滤。app_id 必须进 where，不能查完再筛。"""
        scope = ({"$or": [{"app_id": int(app_id)}, {"scope": "global"}]}
                 if include_global else {"app_id": int(app_id)})
        return {"$and": [{"status": "active"}, scope]}

    # ------------------------------------------------------------------ 接口
    def add_chunks(self, chunks):
        chunks = list(chunks or [])
        col = self._col()
        for start in range(0, len(chunks), _BATCH):
            batch = chunks[start:start + _BATCH]
            try:
                col.upsert(
                    ids=[c.chunk_id for c in batch],
                    embeddings=[c.embedding for c in batch],
                    metadatas=[c.metadata() for c in batch],
                    documents=[c.content for c in batch],
                )
            except Exception as e:
                raise VectorStoreUnavailable(
                    "写入向量库失败（第 %d 批）" % (start // _BATCH + 1)) from e
        return len(chunks)

    def search(self, query_vector, app_id, top_k=5, include_global=True):
        col = self._col()
        try:
            res = col.query(
                query_embeddings=[query_vector],
                n_results=max(1, int(top_k)),
                where=self._where(app_id, include_global),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            raise VectorStoreUnavailable("向量检索失败") from e

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        hits = []
        for i, chunk_id in enumerate(ids):
            meta = metas[i] or {}
            distance = float(dists[i])
            hits.append(SearchHit(
                chunk_id=chunk_id,
                document_id=meta.get("document_id", ""),
                app_id=int(meta.get("app_id", GLOBAL_APP_ID)),
                scope=meta.get("scope", "game_specific"),
                chunk_index=int(meta.get("chunk_index", 0)),
                content=docs[i] if i < len(docs) else "",
                score=1.0 - distance,          # cosine 距离 → 相似度
                distance=distance,
                title=meta.get("title", ""),
                source_type=meta.get("source_type", ""),
                source_name=meta.get("source_name", ""),
                source_url=meta.get("source_url", ""),
                publish_date=meta.get("publish_date", ""),
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def delete_document(self, document_id):
        col = self._col()
        try:
            existing = col.get(where={"document_id": document_id}, include=[])
            ids = existing.get("ids") or []
            if ids:
                col.delete(ids=ids)
            return len(ids)
        except Exception as e:
            raise VectorStoreUnavailable("删除向量失败（document_id=%s）" % document_id) from e

    def count(self, app_id=None):
        col = self._col()
        if app_id is None:
            return int(col.count())
        got = col.get(where={"app_id": int(app_id)}, include=[])
        return len(got.get("ids") or [])

    def health(self):
        info = {"name": "vector_db", "available": False, "detail": None,
                "store": self.name, "collection": self.config.collection,
                "count": None}
        try:
            col = self._col()
            info["count"] = int(col.count())
            info["available"] = True
        except Exception as e:                     # 含 VectorStoreUnavailable
            info["detail"] = str(e)[:200]
            self.last_error = str(e)[:200]
        return info
