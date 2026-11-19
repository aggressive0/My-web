"""
知识库业务编排（Phase 6.2）。

导入流程（每一步失败都有明确落点，不留半成品）：

    加载 → 清洗 → 内容哈希(去重) → 切分 → 向量化
         → SQLite status='indexing' + chunks
         → Chroma upsert
         → SQLite status='active'

为什么先写 SQLite 再写 Chroma：SQLite 是账本，先记「我要索引这份文档」，
再写向量。反过来的话，Chroma 成功而 SQLite 失败会留下无主的向量，
检索时只能靠孤儿检查兜底；现在最坏情况是「一份没索引成功的文档」，
它是可枚举、可重试的。
"""

import hashlib

from . import document_cleaner, document_loader, document_splitter
from .embedding_service import EmbeddingService, EmbeddingUnavailable
from .knowledge_repository import KnowledgeRepository
from .retriever import Retriever
from .vector_store import Chunk, GLOBAL_APP_ID, VectorStoreUnavailable

VALID_SCOPES = ("game_specific", "global")
VALID_SOURCE_TYPES = ("announcement", "patch_note", "faq", "guide", "operation_case",
                      "review_digest", "web_page", "doc", "other")


class KnowledgeError(ValueError):
    code = "KNOWLEDGE_INVALID"

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


class KnowledgeService:
    def __init__(self, config, embedder=None, store=None, repository=None):
        self.config = config
        self.embedder = embedder or EmbeddingService(config)
        self.repository = repository or KnowledgeRepository(config.knowledge_db)
        # 幂等建表。放在这里而不是某个「启动脚本」里：漏建表的后果是每个请求
        # 都 500，而 executescript 的成本只有毫秒级，不值得赌调用顺序。
        self.repository.init_schema()
        if store is None:
            from .chroma_store import ChromaVectorStore
            store = ChromaVectorStore(config)
        self.store = store
        self.retriever = Retriever(store, self.embedder, config, self.repository)

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def content_hash(text):
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def document_id_for(app_id, content_hash):
        key = "%s|%s" % (GLOBAL_APP_ID if app_id is None else app_id, content_hash)
        return "doc_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def _validate(self, app_id, scope, source_type, title):
        if scope not in VALID_SCOPES:
            raise KnowledgeError("scope 必须是 %s 之一" % " / ".join(VALID_SCOPES))
        if source_type not in VALID_SOURCE_TYPES:
            raise KnowledgeError("source_type 不受支持：%s（可选：%s）"
                                 % (source_type, " / ".join(VALID_SOURCE_TYPES)))
        if not (title or "").strip():
            raise KnowledgeError("title 不能为空")
        if scope == "global":
            if app_id not in (None, 0, GLOBAL_APP_ID):
                raise KnowledgeError("global 文档不应携带 app_id，否则会污染单游戏口径")
        else:
            if not isinstance(app_id, int) or app_id <= 0:
                raise KnowledgeError("game_specific 文档必须提供正整数 app_id")

    # ------------------------------------------------------------------ 导入
    def import_document(self, app_id, title, content, source_type, scope="game_specific",
                        source_name=None, source_url=None, publish_date=None,
                        file_name=None, tags=None, force=False):
        """force=True 跳过内容哈希去重，用于「换了 embedding 策略后重建索引」。"""
        self._validate(app_id, scope, source_type, title)

        text = document_cleaner.clean_text(content)
        if not text:
            raise KnowledgeError("清洗后正文为空，拒绝入库")

        if scope == "global":
            app_id = None
            app_id_marker = GLOBAL_APP_ID
        else:
            app_id_marker = int(app_id)

        digest = self.content_hash(text)
        existing = self.repository.find_by_hash(app_id, digest)
        if existing and not force:
            return {
                "created": False,
                "document_id": existing["document_id"],
                "reason": "内容未变化，未重新索引",
                "chunk_count": existing.get("chunk_count", 0),
                "status": existing.get("status"),
                "title_mismatch": bool(existing.get("title") != title.strip()),
            }

        document_id = self.document_id_for(app_id, digest)
        chunks_text = document_splitter.split_text(
            text, size=self.config.chunk_size, overlap=self.config.chunk_overlap)
        if not chunks_text:
            raise KnowledgeError("切分后没有产生任何 chunk，拒绝入库")

        # 向量化必须带上标题。Steam 公告的正文往往从 "Dear players" 开始，
        # 「封禁名单」「Prime Resurgence」这类关键信息只出现在标题里；
        # 只对正文做向量会让这类查询在正文里命中 0 条（6.3 评估时实测到）。
        # 存进 content 的仍然是正文，标题只参与向量计算，证据可追溯性不受影响。
        title_prefix = title.strip()
        try:
            vectors = self.embedder.encode(
                ["%s\n%s" % (title_prefix, piece) for piece in chunks_text])
        except EmbeddingUnavailable as e:
            raise KnowledgeError(str(e), code="EMBEDDING_UNAVAILABLE") from e

        if len(vectors) != len(chunks_text):
            raise KnowledgeError("向量数量与 chunk 数量不一致（%d vs %d）"
                                 % (len(vectors), len(chunks_text)),
                                 code="EMBEDDING_MISMATCH")

        chunks = []
        for index, (piece, vector) in enumerate(zip(chunks_text, vectors)):
            chunks.append(Chunk(
                chunk_id="%s#%04d" % (document_id, index),
                document_id=document_id,
                app_id=app_id_marker,
                scope=scope,
                chunk_index=index,
                content=piece,
                embedding=vector,
                title=title.strip(),
                source_type=source_type,
                source_name=source_name or "",
                source_url=source_url or "",
                publish_date=publish_date or "",
                status="active",
                tags=",".join(tags or []),
                embedding_model=self.config.embedding_model,
            ))

        version = self.repository.begin_document(
            document_id=document_id, app_id=app_id, scope=scope, title=title.strip(),
            source_type=source_type, content=text, content_hash=digest,
            source_name=source_name, source_url=source_url, file_name=file_name,
            publish_date=publish_date, embedding_model=self.config.embedding_model)
        self.repository.save_chunks(document_id, app_id, chunks,
                                    embedding_model=self.config.embedding_model)

        try:
            self.store.add_chunks(chunks)
        except VectorStoreUnavailable as e:
            # 向量没写成就不能算 active，否则检索会命中一份「查不到元数据」的文档
            self.repository.finalize_document(document_id, "index_failed", 0)
            raise KnowledgeError(str(e), code="VECTOR_STORE_UNAVAILABLE") from e

        self.repository.finalize_document(document_id, "active", len(chunks))
        return {
            "created": True,
            "document_id": document_id,
            "version": version,
            "chunk_count": len(chunks),
            "chars": len(text),
            "content_hash": digest,
            "embedding_model": self.config.embedding_model,
            "embedding_dim": len(chunks[0].embedding) if chunks else 0,
            "status": "active",
        }

    def import_file(self, path, app_id, title=None, source_type="doc",
                    scope="game_specific", **kwargs):
        loaded = document_loader.load_from_path(path)
        text = loaded["text"]
        if loaded["kind"] == "html":
            html_title, text = document_cleaner.html_to_text(text)
            title = title or html_title
        return self.import_document(
            app_id=app_id,
            title=title or loaded["file_name"],
            content=text,
            source_type=source_type,
            scope=scope,
            file_name=loaded["file_name"],
            **kwargs)

    def import_bytes(self, filename, data, app_id, title=None, source_type="doc",
                     scope="game_specific", **kwargs):
        loaded = document_loader.load_from_bytes(filename, data)
        text = loaded["text"]
        if loaded["kind"] == "html":
            html_title, text = document_cleaner.html_to_text(text)
            title = title or html_title
        return self.import_document(
            app_id=app_id,
            title=title or loaded["file_name"],
            content=text,
            source_type=source_type,
            scope=scope,
            file_name=loaded["file_name"],
            **kwargs)

    # ------------------------------------------------------------------ 查询
    def search(self, app_id, query, top_k=None, threshold=None):
        return self.retriever.search(app_id, query, top_k=top_k, threshold=threshold)

    def list_documents(self, app_id=None, status=None, source_type=None,
                       limit=20, offset=0):
        rows, total = self.repository.list_documents(
            app_id=app_id, status=status, source_type=source_type,
            limit=limit, offset=offset)
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def get_document(self, document_id, with_content=True):
        doc = self.repository.get_document(document_id, with_content=with_content)
        if not doc:
            raise KnowledgeError("文档不存在：%s" % document_id, code="DOCUMENT_NOT_FOUND")
        return doc

    def delete_document(self, document_id):
        """两步删除。向量删失败时保留元数据，宁可多留不可谎报删除。"""
        if not self.repository.get_document(document_id):
            raise KnowledgeError("文档不存在：%s" % document_id, code="DOCUMENT_NOT_FOUND")
        try:
            removed = self.store.delete_document(document_id)
        except VectorStoreUnavailable as e:
            raise KnowledgeError(
                "向量删除失败，元数据已保留以免检索到不存在的文档：%s" % e,
                code="VECTOR_STORE_UNAVAILABLE") from e
        self.repository.delete_document(document_id)
        return {"document_id": document_id, "deleted_chunks": removed}

    def health(self):
        return {
            "embedding": self.embedder.health(),
            "vector_db": self.store.health(),
        }


# ---------------------------------------------------------------------- 单例
# 每个请求都新建 EmbeddingService 会导致每次请求重新加载 2.1GB 模型。
# 按配置指纹缓存：配置变了才重建（测试里改环境变量不会串味）。
_SERVICE_CACHE = {}
_OVERRIDE = None


def set_service_override(service):
    """仅测试使用：注入假 embedder / 临时目录。生产路径不会调用。"""
    global _OVERRIDE
    _OVERRIDE = service


def reset_service_cache():
    global _OVERRIDE
    _OVERRIDE = None
    _SERVICE_CACHE.clear()


def get_service(config=None):
    if _OVERRIDE is not None:
        return _OVERRIDE
    if config is None:
        from ai.config import AIConfig
        config = AIConfig().rag
    key = (config.embedding_model, config.embedding_device, config.chroma_dir,
           config.collection, config.knowledge_db, config.chunk_size,
           config.chunk_overlap, config.top_k, config.retrieval_min_score)
    service = _SERVICE_CACHE.get(key)
    if service is None:
        service = KnowledgeService(config)
        _SERVICE_CACHE[key] = service
    return service
