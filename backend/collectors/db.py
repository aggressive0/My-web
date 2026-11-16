"""
本地采集库的统一入口：建表、同步游戏注册表、写采集日志、读运行状态。

存在意义：schema 只有一份（backend/data/schema.sql），采集脚本、后端 API、
测试都通过本模块访问数据库，避免每个脚本各写一套 CREATE TABLE / 连接参数，
最后出现「表结构以谁为准」的扯皮。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(BACKEND_DIR, "data", "schema.sql")
DEFAULT_DB = os.path.join(BACKEND_DIR, "data", "steam.db")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db(path=None):
    return path or os.environ.get("STEAM_DB") or DEFAULT_DB


def connect(path=None):
    """打开连接。row_factory=Row，方便按列名取值。"""
    p = resolve_db(path)
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    conn = sqlite3.connect(p, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=None):
    """执行 schema.sql（幂等）。返回数据库路径。"""
    p = resolve_db(path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    conn = connect(p)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    return p


@contextmanager
def session(path=None):
    """一次事务一个连接：正常退出提交，异常回滚，退出即关闭。

    直接 `with sqlite3.connect(...)` 只提交不关闭，长期运行的采集循环会漏 fd。
    """
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_games(conn, games, ts=None):
    """把 yaml 里的游戏同步进 games 表。

    只做 upsert，不删除配置中已移除的游戏 —— 移除配置不应连带删掉历史数据，
    否则「历史 CCU 不丢」无法保证。被移除的游戏只是之后不再被采集。
    """
    ts = ts or now_utc()
    rows = [
        (g["appid"], g["slug"], g["name"], g.get("color"),
         1 if g.get("enabled", True) else 0,
         int(g.get("interval_minutes", 60)),
         json.dumps(g.get("tags") or [], ensure_ascii=False),
         ts)
        for g in games
    ]
    conn.executemany(
        """INSERT INTO games(appid, slug, name, color, enabled, interval_minutes, tags, updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(appid) DO UPDATE SET
             slug=excluded.slug,
             name=excluded.name,
             color=excluded.color,
             enabled=excluded.enabled,
             interval_minutes=excluded.interval_minutes,
             tags=excluded.tags,
             updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()
    return len(rows)


def log_collection(conn, appid, kind, status, ts=None, duration_ms=None,
                   rows=None, http_status=None, error=None):
    """记一条采集日志。失败也要记 —— 否则「哪款游戏挂了、挂了多久」只能靠猜。"""
    conn.execute(
        """INSERT INTO collection_logs(appid, kind, status, ts_utc, duration_ms, rows, http_status, error)
           VALUES(?,?,?,?,?,?,?,?)""",
        (appid, kind, status, ts or now_utc(), duration_ms, rows, http_status, error),
    )
    conn.commit()


def game_stats(conn, appid):
    """给 API 用的运行状态：最近一次成功、点数、最近失败原因。"""
    def one(sql, args=()):
        r = conn.execute(sql, args).fetchone()
        return r[0] if r else None

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")

    return {
        "last_ccu_ts": one("SELECT MAX(ts_utc) FROM ccu WHERE appid=?", (appid,)),
        "last_ccu_players": one(
            "SELECT players FROM ccu WHERE appid=? ORDER BY ts_utc DESC LIMIT 1", (appid,)),
        "ccu_points_total": one("SELECT COUNT(*) FROM ccu WHERE appid=?", (appid,)) or 0,
        "ccu_points_24h": one(
            "SELECT COUNT(*) FROM ccu WHERE appid=? AND ts_utc>=?", (appid, since)) or 0,
        "last_achievement_ts": one(
            "SELECT MAX(snapshot_ts) FROM achievements WHERE appid=?", (appid,)),
        "last_ok_at": one(
            "SELECT MAX(ts_utc) FROM collection_logs WHERE appid=? AND status='ok'", (appid,)),
        "last_error": one(
            "SELECT error FROM collection_logs WHERE appid=? AND status='error' "
            "ORDER BY ts_utc DESC LIMIT 1", (appid,)),
        "last_error_at": one(
            "SELECT MAX(ts_utc) FROM collection_logs WHERE appid=? AND status='error'", (appid,)),
    }


def main():
    """CLI：建库（幂等）并把当前配置同步进去。"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_config import load_games

    path = init_db()
    cfg = load_games()
    with connect(path) as conn:
        n = sync_games(conn, cfg["games"])
        counts = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("games", "ccu", "achievements", "collection_logs", "collection_cursors")}
    print("数据库：%s" % path)
    print("已同步 %d 款游戏" % n)
    print("表行数：%s" % counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
