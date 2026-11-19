"""
召回策略存储（Phase 6.5）。

只存「生成成功的完整结构」与少量列表字段：
payload_json 保留原文，列表字段（风险等级、措施数）是为了历史页面不必解析 JSON。

不存失败结果——失败已经写在 /api/recall/generate 的响应和 llm_call_logs 里，
再存一份空策略只会让历史列表出现「看起来生成过」的脏记录。
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_strategies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id             TEXT UNIQUE NOT NULL,
    app_id                  INTEGER NOT NULL,
    game_name               TEXT,
    execution_status        TEXT NOT NULL,
    risk_level              TEXT,
    summary                 TEXT,
    data_evidence_count     INTEGER DEFAULT 0,
    knowledge_evidence_count INTEGER DEFAULT 0,
    action_count            INTEGER DEFAULT 0,
    model                   TEXT,
    payload_json            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recall_strategies_app
    ON recall_strategies(app_id, created_at DESC);
"""


class RecallStrategyStore:
    def __init__(self, db_path):
        self.db_path = db_path

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
        with self.session() as conn:
            conn.executescript(SCHEMA)
        return self.db_path

    def save(self, strategy, model=None, game_name=None):
        """strategy 必须是已通过 Schema 与证据校验的字典。"""
        strategy_id = "strategy_" + uuid.uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.session() as conn:
            conn.execute(
                """INSERT INTO recall_strategies(
                     strategy_id, app_id, game_name, execution_status, risk_level,
                     summary, data_evidence_count, knowledge_evidence_count,
                     action_count, model, payload_json, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (strategy_id, strategy.get("app_id"), game_name,
                 strategy.get("execution_status") or "suggested",
                 (strategy.get("risk_analysis") or {}).get("risk_level"),
                 (strategy.get("summary") or "")[:200],
                 len(strategy.get("data_evidence") or []),
                 len(strategy.get("knowledge_evidence") or []),
                 len(strategy.get("recall_actions") or []),
                 model, json.dumps(strategy, ensure_ascii=False), created_at))
        return strategy_id

    def get(self, strategy_id):
        with self.session() as conn:
            row = conn.execute("SELECT * FROM recall_strategies WHERE strategy_id = ?",
                               (strategy_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["strategy"] = json.loads(data.pop("payload_json"))
        return data

    def list(self, app_id=None, limit=20, offset=0):
        clause, args = "", []
        if app_id is not None:
            clause, args = " WHERE app_id = ?", [app_id]
        with self.session() as conn:
            total = conn.execute("SELECT COUNT(*) FROM recall_strategies" + clause,
                                 args).fetchone()[0]
            rows = conn.execute(
                "SELECT strategy_id, app_id, game_name, execution_status, risk_level,"
                " summary, data_evidence_count, knowledge_evidence_count, action_count,"
                " model, created_at FROM recall_strategies" + clause +
                " ORDER BY id DESC LIMIT ? OFFSET ?", args + [int(limit), int(offset)]
            ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total,
                "limit": limit, "offset": offset}
