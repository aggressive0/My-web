-- Phase 6.2 知识库表结构（幂等，可重复执行）
-- 与 backend/data/schema.sql（采集层）共用同一个 SQLite 文件，但表不重叠。
-- 之所以单独一份，是为了让「知识库的迁移」能独立于采集 schema 演进，
-- 又不至于把两个 schema 的变更搅在一次提交里。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     TEXT UNIQUE NOT NULL,
    app_id          INTEGER,                          -- NULL = 全局文档
    scope           TEXT NOT NULL DEFAULT 'game_specific',
    title           TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_name     TEXT,
    source_url      TEXT,
    file_name       TEXT,
    content         TEXT NOT NULL,                    -- 保留清洗后的原文，便于重算向量
    content_hash    TEXT NOT NULL,
    publish_date    TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'active',    -- active | indexing | index_failed | inactive
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_app
    ON knowledge_documents(app_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_hash
    ON knowledge_documents(app_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_status
    ON knowledge_documents(status);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id        TEXT UNIQUE NOT NULL,
    document_id     TEXT NOT NULL,
    app_id          INTEGER,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding_model TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY(document_id)
        REFERENCES knowledge_documents(document_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc
    ON knowledge_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_app
    ON knowledge_chunks(app_id);

-- 迁移记录：让「这次 schema 是谁加的、什么时候加的」有据可查
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

-- 检索评估记录（Phase 6.3）。每次评估追加一行，不覆盖：
-- 「这次 0.62、上次 0.58」必须能对比，否则调参就是盲改。
CREATE TABLE IF NOT EXISTS rag_evaluation_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id       TEXT UNIQUE NOT NULL,
    test_set_version    TEXT,
    corpus_fingerprint  TEXT,
    total_questions     INTEGER NOT NULL,
    answerable_count    INTEGER,
    unanswerable_count  INTEGER,
    recall_at_5         REAL,
    precision_at_5      REAL,
    mrr                 REAL,
    appid_isolation_pass INTEGER,
    refusal_accuracy    REAL,
    chunk_count         INTEGER,
    embedding_model     TEXT,
    params_json         TEXT,
    details_json        TEXT,
    created_at          TEXT NOT NULL
);
