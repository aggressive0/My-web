-- ============================================================
-- 本地采集库 schema（backend/data/steam.db）
-- ------------------------------------------------------------
-- 权威定义放在这里，由 backend/collectors/db.py 的 init_db() 执行；
-- 不再在各脚本里散写 CREATE TABLE，避免同表两套定义逐渐漂移。
--
-- 约定：
--   * 时间一律 UTC ISO8601 带时区（如 2026-11-16T11:30:00+00:00），
--     字符串可直接比较大小，排序即时间序。
--   * 所有写入用 INSERT OR REPLACE / UPSERT，重复采集可安全重跑。
--   * games 表是 backend/config/games.yaml 的镜像（身份信息以 yaml 为准），
--     额外承载运行期统计，因此 API 可以一次查询拿到「配置 + 状态」。
-- ============================================================

PRAGMA journal_mode = WAL;

-- 游戏注册表镜像：每次采集前从 yaml 同步，不再硬编码 AppID
CREATE TABLE IF NOT EXISTS games (
    appid            INTEGER PRIMARY KEY,
    slug             TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    color            TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    tags             TEXT,                 -- JSON 数组字符串
    updated_at       TEXT    NOT NULL
);

-- CCU 时序：五分钟级采样，长期保留
CREATE TABLE IF NOT EXISTS ccu (
    appid   INTEGER NOT NULL,
    ts_utc  TEXT    NOT NULL,
    players INTEGER NOT NULL,
    PRIMARY KEY (appid, ts_utc)
);

CREATE INDEX IF NOT EXISTS idx_ccu_ts ON ccu(appid, ts_utc);

-- 成就完成率快照：变化极慢，按需（默认 12h）快照
CREATE TABLE IF NOT EXISTS achievements (
    appid       INTEGER NOT NULL,
    ach_name    TEXT    NOT NULL,
    percent     REAL,
    snapshot_ts TEXT    NOT NULL,
    PRIMARY KEY (appid, ach_name, snapshot_ts)
);

CREATE INDEX IF NOT EXISTS idx_ach_ts ON achievements(appid, snapshot_ts);

-- 采集日志：每次请求一条，成功与失败都记。
-- 「一款失败不影响其余」要靠它来证明，而不是靠翻控制台输出。
CREATE TABLE IF NOT EXISTS collection_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    appid       INTEGER NOT NULL,
    kind        TEXT    NOT NULL,          -- ccu | achievements | reviews
    status      TEXT    NOT NULL,          -- ok | error
    ts_utc      TEXT    NOT NULL,
    duration_ms INTEGER,
    rows        INTEGER,
    http_status INTEGER,                   -- 若有
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_lookup ON collection_logs(appid, kind, ts_utc DESC);

-- 增量游标：Phase 2 的评论采集用（filter=recent / updated 两条流各一个游标）
CREATE TABLE IF NOT EXISTS collection_cursors (
    appid      INTEGER NOT NULL,
    stream     TEXT    NOT NULL,
    cursor     TEXT,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (appid, stream)
);

-- 原始评论（Phase 2）：只落本地库，不进 Git。
-- 主键用 Steam 的 recommendationid，重复采集天然幂等；
-- 追评（timestamp_updated 变化）靠 updated_ts 与 fetched_at 对比识别。
CREATE TABLE IF NOT EXISTS steam_reviews (
    appid            INTEGER NOT NULL,
    review_id        TEXT    NOT NULL,
    created_ts       TEXT    NOT NULL,        -- UTC ISO8601
    updated_ts       TEXT,
    voted_up         INTEGER NOT NULL,        -- 1 推荐 / 0 不推荐
    language         TEXT,
    playtime_minutes INTEGER,                 -- 写评论时的游戏时长
    votes_up         INTEGER,                 -- 这条评论获得的「有用」
    refunded         INTEGER,
    text             TEXT,
    fetched_at       TEXT    NOT NULL,
    PRIMARY KEY (appid, review_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_created ON steam_reviews(appid, created_ts);
CREATE INDEX IF NOT EXISTS idx_reviews_updated ON steam_reviews(appid, updated_ts);
