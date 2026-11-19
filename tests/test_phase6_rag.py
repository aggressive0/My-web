"""
Phase 6.2 知识库与基础 RAG 的测试。

**不用真实 BGE-M3**：2.1GB 模型 + CPU 编码会让测试从十几秒变成几分钟，
CI 上更是根本没有这个模型。这里用确定性假 embedder 验证协议、
app_id 隔离、幂等、失败路径；真实模型的检索质量属于 Phase 6.4 的评估集，
它必须用人工标注的相关性来算 Recall@5，不能靠单元测试自证。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase6_rag -v
"""

import hashlib
import io
import math
import os
import shutil
import sys
import tempfile
import unittest
import zlib

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.config import RAGConfig                              # noqa: E402
from ai.rag import document_cleaner, document_loader, document_splitter  # noqa: E402
from ai.rag.chroma_store import ChromaVectorStore            # noqa: E402
from ai.rag.embedding_service import EmbeddingService        # noqa: E402
from ai.rag.knowledge_repository import KnowledgeRepository  # noqa: E402
from ai.rag.service import (KnowledgeError, KnowledgeService,  # noqa: E402
                            reset_service_cache, set_service_override)
from ai.rag.vector_store import Chunk, VectorStoreUnavailable  # noqa: E402


def _has_chromadb():
    import importlib.util
    return importlib.util.find_spec("chromadb") is not None


class FakeEmbedder:
    """确定性假 embedder：字符二元组哈希到 64 维计数向量，L2 归一化。

    它没有语义理解，但具备测试需要的性质：同样的词会落在同样的维度上，
    因此「含相同关键词的 chunk 相似度更高」是稳定成立的。
    """

    dim = 64

    def __init__(self):
        self.encode_calls = 0
        self.fail = None

    def _vec(self, text):
        vec = [0.0] * self.dim
        text = text or ""
        for i in range(len(text) - 1):
            vec[zlib.crc32(text[i:i + 2].encode("utf-8")) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def encode(self, texts, batch_size=None, normalize_embeddings=True):
        if self.fail:
            raise self.fail
        self.encode_calls += 1
        return [self._vec(t) for t in texts]

    def get_sentence_embedding_dimension(self):
        return self.dim

    def health(self):
        return {"name": "embedding", "available": True, "loaded": True,
                "model": "fake", "device": "cpu", "dim": self.dim, "detail": None}


def make_config(tmpdir, **overrides):
    cfg = RAGConfig()
    cfg.chroma_dir = os.path.join(tmpdir, "chroma")
    cfg.knowledge_db = os.path.join(tmpdir, "knowledge.db")
    cfg.chunk_size = 120
    cfg.chunk_overlap = 20
    cfg.embedding_batch_size = 4
    cfg.retrieval_min_score = None       # 单测不做召回过滤，隔离性由 where 保证
    # 排序策略（新近度加权）不属于检索正确性，单测里一律关闭，
    # 否则「改了权重导致某条断言挂掉」会看起来像检索坏了
    cfg.recency_weight = 0.0
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_chunk(document_id, app_id, scope, index, content, embedder, **extra):
    vector = embedder.encode([content])[0]
    return Chunk(chunk_id="%s#%04d" % (document_id, index), document_id=document_id,
                 app_id=app_id, scope=scope, chunk_index=index, content=content,
                 embedding=vector, **extra)


# ---------------------------------------------------------------- 切分 / 清洗
class SplitterTest(unittest.TestCase):
    def test_every_chunk_within_size(self):
        text = "玩家反馈匹配很慢。" * 200
        chunks = document_splitter.split_text(text, size=100, overlap=20)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)
            self.assertTrue(chunk)

    def test_prefers_sentence_boundary(self):
        text = "第一句话说的是匹配问题。" + "第二句话说的是付费问题。" * 10
        chunks = document_splitter.split_text(text, size=40, overlap=0)
        self.assertTrue(chunks[0].endswith("。"), chunks[0])

    def test_overlap_is_reused(self):
        text = "甲" * 50 + "乙" * 50
        chunks = document_splitter.split_text(text, size=60, overlap=20)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[1].startswith("甲"))

    def test_empty_and_whitespace(self):
        self.assertEqual(document_splitter.split_text("   \n\n "), [])

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            document_splitter.split_text("x", size=0)
        with self.assertRaises(ValueError):
            document_splitter.split_text("x" * 100, size=50, overlap=50)

    def test_progress_with_large_overlap(self):
        """overlap 再大也不能原地踏步（死循环）。"""
        chunks = document_splitter.split_text("字" * 300, size=50, overlap=49)
        self.assertLess(len(chunks), 400)


class CleanerTest(unittest.TestCase):
    def test_html_extracts_title_and_text(self):
        html = ("<html><head><title>更新公告 1.2.0</title>"
                "<style>.a{color:red}</style></head>"
                "<body><script>alert(1)</script>"
                "<h1>版本更新</h1><p>修复了匹配问题。</p></body></html>")
        title, text = document_cleaner.html_to_text(html)
        self.assertEqual(title, "更新公告 1.2.0")
        self.assertIn("修复了匹配问题", text)
        self.assertIn("版本更新", text)
        self.assertNotIn("alert(1)", text)
        self.assertNotIn("color:red", text)

    def test_control_chars_and_blank_lines(self):
        raw = "第一段\x00内容\r\n\r\n\r\n\r\n第二段  多余空格   "
        cleaned = document_cleaner.clean_text(raw)
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\n\n\n", cleaned)
        self.assertIn("第二段 多余空格", cleaned)

    def test_empty_input(self):
        self.assertEqual(document_cleaner.clean_text(""), "")
        self.assertEqual(document_cleaner.clean_text(None), "")


# ---------------------------------------------------------------------- 加载
class LoaderTest(unittest.TestCase):
    def test_markdown(self):
        loaded = document_loader.load_from_bytes("公告.md", "# 标题\n正文".encode("utf-8"))
        self.assertEqual(loaded["kind"], "markdown")
        self.assertIn("正文", loaded["text"])

    def test_gbk_fallback(self):
        data = "修复了匹配问题".encode("gb18030")
        loaded = document_loader.load_from_bytes("a.txt", data)
        self.assertEqual(loaded["text"].strip(), "修复了匹配问题")
        self.assertEqual(loaded["encoding"], "gb18030")

    def test_pdf_is_explicitly_unsupported(self):
        with self.assertRaises(document_loader.UnsupportedFormat) as ctx:
            document_loader.load_from_bytes("补丁.pdf", b"%PDF-1.7 fake")
        message = str(ctx.exception)
        self.assertIn(".pdf", message)
        self.assertIn("OCR", message)          # 扫描版 PDF 的坑要提前说清

    def test_docx_is_explicitly_unsupported(self):
        with self.assertRaises(document_loader.UnsupportedFormat):
            document_loader.load_from_bytes("说明.docx", b"PK\x03\x04")

    def test_oversize_rejected(self):
        old = document_loader.MAX_BYTES
        document_loader.MAX_BYTES = 8
        try:
            with self.assertRaises(ValueError):
                document_loader.load_from_bytes("a.txt", b"0123456789")
        finally:
            document_loader.MAX_BYTES = old

    def test_empty_after_parse(self):
        with self.assertRaises(ValueError):
            document_loader.load_from_bytes("a.txt", b"   \n  ")


# ------------------------------------------------------------------ SQLite
class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = KnowledgeRepository(os.path.join(self.tmp, "k.db"))
        self.repo.init_schema()

    def test_init_schema_is_idempotent(self):
        self.repo.init_schema()
        self.repo.init_schema()
        with self.repo.session() as conn:
            names = [r["name"] for r in conn.execute(
                "SELECT name FROM schema_migrations").fetchall()]
        self.assertEqual(names, ["phase6_2_knowledge_base"])

    def test_document_lifecycle(self):
        self.repo.begin_document("doc_a", 1203220, "game_specific", "标题", "patch_note",
                                 "正文", "hash1")
        self.repo.save_chunks("doc_a", 1203220, [])
        self.repo.finalize_document("doc_a", "active", 0)
        doc = self.repo.get_document("doc_a", with_content=True)
        self.assertEqual(doc["status"], "active")
        self.assertEqual(doc["content"], "正文")
        self.assertEqual(self.repo.active_document_ids(["doc_a"]), {"doc_a"})
        self.assertTrue(self.repo.delete_document("doc_a"))
        self.assertIsNone(self.repo.get_document("doc_a"))

    def test_list_filters_by_app(self):
        for app_id, doc_id in ((1, "d1"), (2, "d2")):
            self.repo.begin_document(doc_id, app_id, "game_specific", doc_id, "doc",
                                     "内容%s" % doc_id, "h%s" % doc_id)
        rows, total = self.repo.list_documents(app_id=1)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["document_id"], "d1")

    def test_global_document_has_null_app(self):
        self.repo.begin_document("d_g", None, "global", "通用", "faq", "内容", "hg")
        rows, _ = self.repo.list_documents()
        self.assertIsNone(rows[0]["app_id"])

    def test_delete_cascades_chunks(self):
        self.repo.begin_document("d", 1, "game_specific", "t", "doc", "c", "h")
        self.repo.save_chunks("d", 1, [Chunk("d#0", "d", 1, "game_specific", 0, "正文")])
        self.assertEqual(len(self.repo.get_chunks(["d#0"])), 1)
        self.repo.delete_document("d")
        self.assertEqual(self.repo.get_chunks(["d#0"]), {})


# ---------------------------------------------------------- embedding 健康检查
class EmbeddingHealthTest(unittest.TestCase):
    """回归：健康检查不许撒谎，也不许顺手加载 2.1GB 模型。

    这条踩过：snapshot_download(local_files_only=True) 仍会联网核对 revision，
    导致「模型明明在缓存里，health 却报不可用」。
    """

    def setUp(self):
        self.service = EmbeddingService(RAGConfig())

    def test_cached_model_reports_available(self):
        self.service._cached_snapshot_path = lambda: "/fake/cache"
        health = self.service.health()
        self.assertTrue(health["available"])
        self.assertTrue(health["cached"])
        self.assertIsNone(health["detail"])

    def test_missing_model_reports_reason(self):
        self.service._cached_snapshot_path = lambda: None
        health = self.service.health()
        self.assertFalse(health["available"])
        self.assertFalse(health["cached"])
        self.assertTrue(health["detail"])

    def test_health_does_not_load_model(self):
        self.service._cached_snapshot_path = lambda: "/fake/cache"
        self.service.health()
        self.assertFalse(self.service.loaded)


# -------------------------------------------------------------------- Chroma
@unittest.skipUnless(_has_chromadb(), "chromadb 未安装")
class ChromaStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = make_config(self.tmp)
        self.emb = FakeEmbedder()
        self.store = ChromaVectorStore(self.cfg)

    def test_score_is_similarity_not_distance(self):
        chunk = make_chunk("d1", 100, "game_specific", 0, "匹配超时", self.emb)
        self.store.add_chunks([chunk])
        hits = self.store.search(chunk.embedding, app_id=100, top_k=3)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].score, 1.0, places=5)
        self.assertAlmostEqual(hits[0].distance, 0.0, places=5)

    def test_app_id_isolation(self):
        self.store.add_chunks([
            make_chunk("d1", 100, "game_specific", 0, "匹配系统崩溃", self.emb),
            make_chunk("d2", 200, "game_specific", 0, "匹配系统崩溃", self.emb),
        ])
        hits = self.store.search(self.emb.encode(["匹配系统崩溃"])[0], app_id=100, top_k=10)
        self.assertEqual([h.document_id for h in hits], ["d1"])

    def test_global_scope_crosses_games(self):
        self.store.add_chunks([
            make_chunk("g1", -1, "global", 0, "运营通用建议", self.emb, status="active"),
            make_chunk("d2", 200, "game_specific", 0, "运营通用建议", self.emb),
        ])
        hits = self.store.search(self.emb.encode(["运营通用建议"])[0], app_id=100, top_k=10)
        self.assertEqual([h.document_id for h in hits], ["g1"])

    def test_upsert_is_idempotent(self):
        chunk = make_chunk("d1", 100, "game_specific", 0, "同一段文本", self.emb)
        self.store.add_chunks([chunk])
        self.store.add_chunks([chunk])
        self.assertEqual(self.store.count(app_id=100), 1)

    def test_delete_by_document(self):
        self.store.add_chunks([
            make_chunk("d1", 100, "game_specific", 0, "第一段", self.emb),
            make_chunk("d1", 100, "game_specific", 1, "第二段", self.emb),
        ])
        self.assertEqual(self.store.delete_document("d1"), 2)
        self.assertEqual(self.store.count(app_id=100), 0)

    def test_health_reports_count(self):
        health = self.store.health()
        self.assertTrue(health["available"])
        self.assertEqual(health["count"], 0)


# ------------------------------------------------------------------- Service
@unittest.skipUnless(_has_chromadb(), "chromadb 未安装")
class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = make_config(self.tmp)
        self.emb = FakeEmbedder()
        self.service = KnowledgeService(self.cfg, embedder=self.emb)

    def import_doc(self, app_id=1203220, content=None, title="更新公告", **kw):
        return self.service.import_document(
            app_id=app_id, title=title,
            content=content or "本次更新修复了匹配超时问题。" * 10,
            source_type="patch_note", **kw)

    def test_import_writes_chunks_and_active_status(self):
        result = self.import_doc()
        self.assertTrue(result["created"])
        self.assertGreater(result["chunk_count"], 1)
        doc = self.service.get_document(result["document_id"])
        self.assertEqual(doc["status"], "active")
        self.assertEqual(doc["chunk_count"], result["chunk_count"])
        self.assertEqual(doc["version"], 1)

    def test_duplicate_import_is_not_reindexed(self):
        first = self.import_doc()
        second = self.import_doc()
        self.assertFalse(second["created"])
        self.assertEqual(second["document_id"], first["document_id"])
        self.assertEqual(self.emb.encode_calls, 1)      # 没有重复向量化

    def test_same_content_different_game_are_two_documents(self):
        a = self.import_doc(app_id=1203220)
        b = self.import_doc(app_id=570)
        self.assertNotEqual(a["document_id"], b["document_id"])
        self.assertEqual(self.service.list_documents(app_id=570)["total"], 1)

    def test_search_only_returns_own_game(self):
        self.import_doc(app_id=1203220, content="匹配超时问题严重。" * 20)
        self.import_doc(app_id=570, content="匹配超时问题严重。" * 20)
        result = self.service.search(1203220, "匹配超时")
        self.assertTrue(result["hits"])
        self.assertTrue(all(h["app_id"] == 1203220 for h in result["hits"]))
        self.assertEqual(result["stats"]["dropped_orphan"], 0)

    def test_global_document_visible_to_every_game(self):
        self.import_doc(app_id=None, scope="global", title="通用运营手册",
                        content="面向所有游戏的回归活动设计原则。" * 10)
        result = self.service.search(1203220, "回归活动设计原则")
        self.assertTrue(result["hits"])
        self.assertEqual(result["hits"][0]["scope"], "global")

    def test_delete_removes_from_retrieval(self):
        result = self.import_doc()
        self.service.delete_document(result["document_id"])
        after = self.service.search(1203220, "匹配超时")
        self.assertEqual(after["hits"], [])
        # Phase 6.3 起检索不再判断「能不能回答」：insufficient_evidence 已移除，
        # 只保留「检索是否为空」这个事实性字段。拒答由 EvidenceGate 负责。
        self.assertTrue(after["retrieval_empty"])

    def test_orphan_vector_is_dropped(self):
        """Chroma 有向量但 SQLite 没有 active 文档 → 必须被丢弃并计数。"""
        self.service.store.add_chunks([
            make_chunk("ghost", 1203220, "game_specific", 0, "幽灵文档", self.emb)])
        result = self.service.search(1203220, "幽灵文档")
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["stats"]["dropped_orphan"], 1)
        # 成因要报对：孤儿过滤不等于「低于召回下限」，后者会把人引向调阈值
        self.assertIn("孤儿", result["empty_reason"])

    def test_vector_failure_marks_index_failed(self):
        class Broken(self.service.store.__class__):
            def add_chunks(self, chunks):
                raise VectorStoreUnavailable("模拟写入失败")

        self.service.store = Broken(self.cfg)
        self.service.retriever.store = self.service.store
        with self.assertRaises(KnowledgeError) as ctx:
            self.import_doc()
        self.assertEqual(ctx.exception.code, "VECTOR_STORE_UNAVAILABLE")

        rows = self.service.repository.list_documents()[0]
        self.assertEqual(rows[0]["status"], "index_failed")
        self.assertEqual(self.service.search(1203220, "匹配超时")["hits"], [])

    def test_embedding_failure_is_explicit(self):
        self.emb.fail = RuntimeError("模型没加载")
        with self.assertRaises(RuntimeError):
            self.import_doc()

    def test_validation(self):
        with self.assertRaises(KnowledgeError):
            self.service.import_document(1203220, "", "x", "patch_note")
        with self.assertRaises(KnowledgeError):
            self.service.import_document(1203220, "t", "x", "不存在的类型")
        with self.assertRaises(KnowledgeError):
            self.service.import_document(1203220, "t", "x", "faq", scope="global")
        with self.assertRaises(KnowledgeError):
            self.service.import_document(None, "t", "x", "faq", scope="game_specific")
        with self.assertRaises(KnowledgeError):
            self.service.get_document("doc_missing")

    def test_html_import_uses_title_tag(self):
        html = "<html><head><title>1.3.0 补丁</title></head><body><p>修复闪退。</p></body></html>"
        result = self.service.import_bytes("patch.html", html.encode("utf-8"),
                                           app_id=1203220, title=None,
                                           source_type="patch_note")
        doc = self.service.get_document(result["document_id"])
        self.assertEqual(doc["title"], "1.3.0 补丁")
        self.assertNotIn("<p>", doc["content"])


# ----------------------------------------------------------------------- API
@unittest.skipUnless(_has_chromadb(), "chromadb 未安装")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.client = A.app.test_client()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_config(self.tmp)
        self.emb = FakeEmbedder()
        self.service = KnowledgeService(self.cfg, embedder=self.emb)
        set_service_override(self.service)

    def tearDown(self):
        reset_service_cache()
        shutil.rmtree(self.tmp, True)

    def _import(self, **payload):
        body = {"app_id": 1203220, "title": "更新公告", "source_type": "patch_note",
                "content": "本次更新修复了匹配超时与闪退问题。" * 10}
        body.update(payload)
        return self.client.post("/api/knowledge/import", json=body)

    def test_import_creates_201_then_200(self):
        first = self._import()
        self.assertEqual(first.status_code, 201)
        data = first.get_json()["data"]
        self.assertTrue(data["created"])
        second = self._import()
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["data"]["created"])

    def test_multipart_upload(self):
        data = {"app_id": "1203220", "title": "补丁说明", "source_type": "patch_note",
                "file": (io.BytesIO("# 1.4.0\n修复了匹配超时。".encode("utf-8")), "patch.md")}
        r = self.client.post("/api/knowledge/import", data=data,
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["data"]["chunk_count"], 1)

    def test_unsupported_format_returns_400(self):
        data = {"app_id": "1203220", "title": "补丁", "source_type": "patch_note",
                "file": (io.BytesIO(b"%PDF-1.7"), "patch.pdf")}
        r = self.client.post("/api/knowledge/import", data=data,
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "UNSUPPORTED_FORMAT")
        self.assertIn("OCR", r.get_json()["error"]["message"])

    def test_list_and_detail(self):
        doc_id = self._import().get_json()["data"]["document_id"]
        listing = self.client.get("/api/knowledge/documents?app_id=1203220").get_json()
        self.assertEqual(listing["data"]["total"], 1)
        self.assertEqual(listing["data"]["items"][0]["document_id"], doc_id)
        detail = self.client.get("/api/knowledge/documents/%s" % doc_id).get_json()
        self.assertIn("修复了匹配超时", detail["data"]["content"])

    def test_search_returns_hits_and_meta(self):
        self._import()
        r = self.client.post("/api/knowledge/search",
                             json={"app_id": 1203220, "query": "匹配超时"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["data"]["hits"])
        self.assertLessEqual(body["data"]["hits"][0]["score"], 1.0)
        self.assertIn("1 - cosine_distance", body["meta"]["note"])

    def test_search_requires_app_id(self):
        r = self.client.post("/api/knowledge/search", json={"query": "匹配"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_APP_ID")

    def test_search_is_isolated_between_games(self):
        self._import()
        r = self.client.post("/api/knowledge/search",
                             json={"app_id": 570, "query": "匹配超时"})
        self.assertEqual(r.get_json()["data"]["hits"], [])

    def test_delete_then_search_empty(self):
        imported = self._import().get_json()["data"]
        doc_id = imported["document_id"]
        d = self.client.delete("/api/knowledge/documents/%s" % doc_id)
        self.assertEqual(d.status_code, 200)
        # 删掉的向量数必须等于导入时写入的 chunk 数，否则就是只删了一半
        self.assertEqual(d.get_json()["data"]["deleted_chunks"], imported["chunk_count"])
        after = self.client.post("/api/knowledge/search",
                                 json={"app_id": 1203220, "query": "匹配超时"})
        self.assertEqual(after.get_json()["data"]["hits"], [])

    def test_delete_missing_returns_404(self):
        r = self.client.delete("/api/knowledge/documents/doc_nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "DOCUMENT_NOT_FOUND")

    def test_health_reports_real_component_status(self):
        body = self.client.get("/api/ai/health").get_json()
        self.assertTrue(body["data"]["embedding"]["available"])
        self.assertTrue(body["data"]["vector_db"]["available"])
        self.assertEqual(body["data"]["vector_db"]["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
