"""
LLM 调用日志（Phase 6.2 Task 7）。

为什么必须落库：`provider.last_metrics` 只活在进程内存里，Flask 一重启就没了。
「上次那批请求是不是又被截断了」「冷启动到底几秒」这类问题，没有历史就只能靠重演。

为什么单独一张表而不是塞进 rag_evaluation_runs：那是检索评估记录，
字段语义（Recall@5、MRR）与调用耗时毫无关系，混在一起查询和清理都会互相牵连。

保留策略：默认 30 天（`LLM_LOG_RETENTION_DAYS` 可覆盖）。每次写入后顺手清理，
不做定时任务——为了清一张小表引入调度器不划算，而写入频率本身很低。
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
DEFAULT_RETENTION_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def default_db_path():
    from ai.config import DEFAULT_KNOWLEDGE_DB
    return (os.environ.get("LLM_CALL_LOG_DB")
            or os.environ.get("KNOWLEDGE_DB")
            or os.environ.get("STEAM_DB")
            or DEFAULT_KNOWLEDGE_DB)


class LLMCallLogRepository:
    def __init__(self, db_path=None, retention_days=None):
        self.db_path = db_path or default_db_path()
        try:
            self.retention_days = int(
                retention_days if retention_days is not None
                else os.environ.get("LLM_LOG_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
        except (TypeError, ValueError):
            self.retention_days = DEFAULT_RETENTION_DAYS

    @contextmanager
    def session(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
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
        return self.db_path

    # ------------------------------------------------------------------ 写入
    def record(self, purpose, provider, model, ok, metrics=None, wall_ms=None,
               error_code=None, app_id=None, truncated=False, thinking_enabled=None):
        """记一条调用。失败也要记——只记成功的日志回答不了「为什么失败」。"""
        metrics = metrics or {}
        call_id = uuid.uuid4().hex[:16]
        created_at = _now().isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                """INSERT INTO llm_call_logs(
                     call_id, purpose, provider, model, app_id, ok, error_code,
                     done_reason, truncated, prompt_tokens, eval_tokens,
                     thinking_chars, content_chars, load_ms, prompt_eval_ms,
                     eval_ms, wall_ms, tokens_per_s, thinking_enabled, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (call_id, purpose, provider, model, app_id, 1 if ok else 0, error_code,
                 metrics.get("done_reason"), 1 if truncated else 0,
                 metrics.get("prompt_eval_count"), metrics.get("eval_count"),
                 metrics.get("thinking_chars"), metrics.get("content_chars"),
                 metrics.get("load_ms"), metrics.get("prompt_eval_ms"),
                 metrics.get("eval_ms"), wall_ms, metrics.get("tokens_per_s"),
                 None if thinking_enabled is None else (1 if thinking_enabled else 0),
                 created_at))
        self.purge()
        return call_id

    def purge(self, keep_days=None):
        """按时间清理。返回删除条数。"""
        days = self.retention_days if keep_days is None else int(keep_days)
        cutoff = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self.session() as conn:
            cursor = conn.execute(
                "DELETE FROM llm_call_logs WHERE created_at < ?", (cutoff,))
            return cursor.rowcount

    # ------------------------------------------------------------------ 读取
    def recent(self, limit=20, purpose=None):
        clause, args = "", []
        if purpose:
            clause, args = " WHERE purpose = ?", [purpose]
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_call_logs" + clause +
                " ORDER BY id DESC LIMIT ?", args + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    def stats(self, days=30):
        cutoff = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self.session() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS calls,
                          SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures,
                          SUM(truncated) AS truncated,
                          AVG(eval_tokens) AS avg_eval_tokens,
                          AVG(load_ms) AS avg_load_ms,
                          AVG(eval_ms) AS avg_eval_ms,
                          AVG(tokens_per_s) AS avg_tokens_per_s
                   FROM llm_call_logs WHERE created_at >= ?""", (cutoff,)).fetchone()
        data = dict(row) if row else {}
        calls = data.get("calls") or 0
        return {
            "window_days": days,
            "calls": calls,
            "failures": data.get("failures") or 0,
            "truncated": data.get("truncated") or 0,
            "avg_eval_tokens": round(data["avg_eval_tokens"], 1)
            if data.get("avg_eval_tokens") is not None else None,
            "avg_load_ms": round(data["avg_load_ms"]) if data.get("avg_load_ms") is not None else None,
            "avg_eval_ms": round(data["avg_eval_ms"]) if data.get("avg_eval_ms") is not None else None,
            "avg_tokens_per_s": round(data["avg_tokens_per_s"], 1)
            if data.get("avg_tokens_per_s") is not None else None,
            "retention_days": self.retention_days,
        }


# ---------------------------------------------------------------------- 缓存
_REPO_CACHE = {}


def get_repository(path=None):
    """进程内按路径缓存：每个请求都 executescript 一次 schema 没必要。"""
    key = path or default_db_path()
    repo = _REPO_CACHE.get(key)
    if repo is None:
        repo = LLMCallLogRepository(key)
        repo.init_schema()
        _REPO_CACHE[key] = repo
    return repo


def reset_cache():
    _REPO_CACHE.clear()
