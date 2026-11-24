"""
知识库元数据仓储（Phase 6.2）。

为什么知识库要有 SQLite 这一份「账本」，而不是只信 Chroma：

- Chroma 只存 chunk 文本和向量，回答不了「这份文档什么时候导入的、
  原文是什么、有没有导失败」；
- 删除是两步操作（SQLite + Chroma），必须有地方记录中间状态；
- 方案 §6 要求「原始数据和分析结果分离，模型升级后可重跑」——
  原文留在 content 字段，换 embedding 模型时只重算向量。

一致性策略（很重要）：
写库顺序是 SQLite(status='indexing') → Chroma → SQLite(status='active')。
中途失败则落 status='index_failed'，检索只认 active。这样最坏情况是
「有一份没索引成功的文档」，而不是「检索到一份不存在的文档」。
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
MIGRATION_NAME = "phase6_2_knowledge_base"

VALID_STATUS = ("active", "indexing", "index_failed", "inactive")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_db_path():
    from ai.config import DEFAULT_KNOWLEDGE_DB
    return DEFAULT_KNOWLEDGE_DB


class KnowledgeRepository:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.environ.get("KNOWLEDGE_DB") \
            or os.environ.get("STEAM_DB") or _default_db_path()

    # ------------------------------------------------------------------ 连接
    @contextmanager
    def session(self):
        """一次事务一个连接。绝不跨 embedding 调用持有连接。"""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = f.read()
        with self.session() as conn:
            conn.executescript(schema)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(name, applied_at) VALUES(?,?)",
                (MIGRATION_NAME, _now()))
        return self.db_path

    # ------------------------------------------------------------------ 写入
    def find_by_hash(self, app_id, content_hash):
        """同 app_id + 同内容即视为同一份文档（幂等导入的基础）。"""
        with self.session() as conn:
            row = conn.execute(
                """SELECT * FROM knowledge_documents
                   WHERE app_id IS ? AND content_hash = ?""",
                (app_id, content_hash)).fetchone()
            return dict(row) if row else None

    def begin_document(self, document_id, app_id, scope, title, source_type,
                       content, content_hash, source_name=None, source_url=None,
                       file_name=None, publish_date=None, embedding_model=None):
        """插入或重置为 indexing 状态。同 document_id 再导入时 version+1。"""
        now = _now()
        with self.session() as conn:
            row = conn.execute(
                "SELECT version FROM knowledge_documents WHERE document_id=?",
                (document_id,)).fetchone()
            if row:
                version = int(row["version"]) + 1
                conn.execute(
                    """UPDATE knowledge_documents SET
                         app_id=?, scope=?, title=?, source_type=?, source_name=?,
                         source_url=?, file_name=?, content=?, content_hash=?,
                         publish_date=?, version=?, status='indexing',
                         chunk_count=0, embedding_model=?, updated_at=?
                       WHERE document_id=?""",
                    (app_id, scope, title, source_type, source_name, source_url,
                     file_name, content, content_hash, publish_date, version,
                     embedding_model, now, document_id))
            else:
                version = 1
                conn.execute(
                    """INSERT INTO knowledge_documents(
                         document_id, app_id, scope, title, source_type, source_name,
                         source_url, file_name, content, content_hash, publish_date,
                         version, status, chunk_count, embedding_model,
                         created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'indexing', 0, ?, ?, ?)""",
                    (document_id, app_id, scope, title, source_type, source_name,
                     source_url, file_name, content, content_hash, publish_date,
                     version, embedding_model, now, now))
            # 旧 chunk 先删：内容变了就不该留着旧索引
            conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (document_id,))
        return version

    def save_chunks(self, document_id, app_id, chunks, embedding_model=None):
        now = _now()
        rows = [(c.chunk_id, document_id, app_id, c.chunk_index, c.content,
                 embedding_model, now) for c in chunks]
        with self.session() as conn:
            conn.executemany(
                """INSERT INTO knowledge_chunks(
                     chunk_id, document_id, app_id, chunk_index, content,
                     embedding_model, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                rows)
        return len(rows)

    def finalize_document(self, document_id, status, chunk_count=None):
        if status not in VALID_STATUS:
            raise ValueError("非法状态：%s" % status)
        with self.session() as conn:
            if chunk_count is None:
                conn.execute(
                    "UPDATE knowledge_documents SET status=?, updated_at=? WHERE document_id=?",
                    (status, _now(), document_id))
            else:
                conn.execute(
                    """UPDATE knowledge_documents SET status=?, chunk_count=?, updated_at=?
                       WHERE document_id=?""",
                    (status, int(chunk_count), _now(), document_id))

    # ------------------------------------------------------------------ 读取
    def get_document(self, document_id, with_content=False):
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE document_id=?",
                (document_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        if not with_content:
            data.pop("content", None)
        return data

    def list_documents(self, app_id=None, status=None, source_type=None,
                       limit=20, offset=0, sort="created_at"):
        where, args = [], []
        if app_id is not None:
            where.append("app_id IS ?")
            args.append(app_id)
        if status:
            where.append("status = ?")
            args.append(status)
        if source_type:
            where.append("source_type = ?")
            args.append(source_type)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self.session() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM knowledge_documents" + clause, args).fetchone()[0]
            # 排序白名单：不接受外部拼串，只有这两个字段可控。
            # 事件时间线需要按公告日期排，而默认的 created_at 是「导入顺序」，
            # 两者在批量导入时完全不同（109 篇是一次性灌进去的）。
            order = "publish_date DESC, id DESC" if sort == "publish_date" \
                else "created_at DESC, id DESC"
            rows = conn.execute(
                "SELECT document_id, app_id, scope, title, source_type, source_name,"
                " source_url, file_name, publish_date, version, status, chunk_count,"
                " embedding_model, created_at, updated_at"
                " FROM knowledge_documents" + clause +
                " ORDER BY " + order + " LIMIT ? OFFSET ?",
                args + [int(limit), int(offset)]).fetchall()
        return [dict(r) for r in rows], int(total)

    def get_chunks(self, chunk_ids):
        if not chunk_ids:
            return {}
        marks = ",".join("?" for _ in chunk_ids)
        with self.session() as conn:
            rows = conn.execute(
                "SELECT chunk_id, document_id, app_id, chunk_index, content"
                " FROM knowledge_chunks WHERE chunk_id IN (%s)" % marks,
                list(chunk_ids)).fetchall()
        return {r["chunk_id"]: dict(r) for r in rows}

    def active_document_ids(self, document_ids):
        document_ids = list(document_ids or [])
        if not document_ids:
            return set()
        marks = ",".join("?" for _ in document_ids)
        with self.session() as conn:
            rows = conn.execute(
                "SELECT document_id FROM knowledge_documents"
                " WHERE status='active' AND document_id IN (%s)" % marks,
                document_ids).fetchall()
        return {r["document_id"] for r in rows}

    # ------------------------------------------------------------------ 删除
    def delete_document(self, document_id):
        """删除元数据与其 chunks（FK ON DELETE CASCADE）。向量由 service 负责删。"""
        with self.session() as conn:
            row = conn.execute(
                "SELECT 1 FROM knowledge_documents WHERE document_id=?",
                (document_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (document_id,))
            conn.execute("DELETE FROM knowledge_documents WHERE document_id=?", (document_id,))
        return True
