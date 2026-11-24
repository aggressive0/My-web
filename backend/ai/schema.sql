-- AI 调用日志表（Phase 6.2 Task 7）
-- 与 backend/data/schema.sql（采集层）、backend/ai/rag/schema.sql（知识库）同库不同域。
-- 单独一份的理由：LLM 调用日志的保留策略与知识库完全无关，
-- 混在一起会让「清日志」看起来像在动知识库。

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT UNIQUE NOT NULL,
    purpose         TEXT,                    -- ping | warmup | strategy
    provider        TEXT,
    model           TEXT,
    app_id          INTEGER,
    ok              INTEGER NOT NULL,         -- 0/1
    error_code      TEXT,                     -- LLM_TRUNCATED / LLM_UNAVAILABLE ...
    done_reason     TEXT,                     -- stop | length
    truncated       INTEGER NOT NULL DEFAULT 0,
    prompt_tokens   INTEGER,
    eval_tokens     INTEGER,
    thinking_chars  INTEGER,
    content_chars   INTEGER,
    load_ms         INTEGER,
    prompt_eval_ms  INTEGER,
    eval_ms         INTEGER,
    wall_ms         INTEGER,
    tokens_per_s    REAL,
    thinking_enabled INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_call_logs_created
    ON llm_call_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_purpose
    ON llm_call_logs(purpose, created_at);
