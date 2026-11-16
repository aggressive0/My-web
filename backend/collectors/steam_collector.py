"""
Steam 公开数据采集器（路线 A：真实 CCU 时序 + 成就漏斗）

数据来源（均无需 API Key）：
  - 同时在线人数  ISteamUserStats/GetNumberOfCurrentPlayers/v1?appid=
  - 成就完成率    ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2?gameid=
  - 游戏名        store.steampowered.com/api/appdetails?appids=（可选，部分网络环境被挡）

口径说明（写进答辩时的必备前提）：
  - CCU = 某一时刻的同时在线人数，不是 DAU，也不是活跃玩家数。
  - 成就完成率 = 全量玩家中的累计完成百分比，用它反推「进度流失」是近似，
    不能等同于留存率。

用法：
  python steam_collector.py --appids 578080,1203220 --interval 60 --minutes 0
  python steam_collector.py --once            # 只采一轮，用于验证环境

环境变量：
  STEAM_PROXY   代理地址，默认 http://127.0.0.1:7897；留空则直连
  STEAM_DB      SQLite 路径，默认 backend/data/steam.db
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
ACH_URL = "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
DEFAULT_DB = os.path.join(BACKEND_DIR, "data", "steam.db")
PROXY = os.environ.get("STEAM_PROXY", "http://127.0.0.1:7897")
DB = os.environ.get("STEAM_DB", DEFAULT_DB)


def make_opener(proxy):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [
        ("User-Agent", "NEXUS-retention-demo/1.0 (student research; contact: local)"),
        # 上游 CDN 会给 CCU 接口加 Cache-Control: max-age≈647（约 11 分钟）。
        # 不加这两个头 + 穿透参数，采到的会是一条水平直线，且会被误读成「玩家数稳定」。
        ("Cache-Control", "no-cache"),
        ("Pragma", "no-cache"),
    ]
    return op


OPENER = make_opener(PROXY)

# 网络环境会变：Clash 可能在系统代理 / TUN 透明代理 / 关闭之间切换。
# 逐条探测，谁通用谁，避免「代理端口一关，采集就全线静默失败」。
PROXY_CANDIDATES = [None, "http://127.0.0.1:7897", "http://127.0.0.1:7890", "http://127.0.0.1:1080"]


def resolve_opener(proxy_arg, probe_appid):
    """proxy_arg: 'auto' 自动探测 | ''/'none' 强制直连 | 具体地址强制指定"""
    global OPENER, PROXY

    if proxy_arg not in (None, "auto"):
        PROXY = None if proxy_arg in ("", "none") else proxy_arg
        OPENER = make_opener(PROXY)
        return

    url = CCU_URL + "?appid=%d&_=%d" % (probe_appid, int(time.time() * 1000))
    for cand in PROXY_CANDIDATES:
        op = make_opener(cand)
        try:
            with op.open(url, timeout=10) as r:
                json.loads(r.read().decode("utf-8"))
            OPENER, PROXY = op, cand
            return
        except Exception:
            continue
    # 全部不通：保留直连，让后续每轮都报错，而不是静默无数据
    OPENER, PROXY = make_opener(None), None


def get_json(url, timeout=25, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            with OPENER.open(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("request failed: %s (%s)" % (url, last))


def init_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS ccu(
            appid      INTEGER NOT NULL,
            ts_utc     TEXT    NOT NULL,
            players    INTEGER NOT NULL,
            PRIMARY KEY(appid, ts_utc)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS achievements(
            appid       INTEGER NOT NULL,
            ach_name    TEXT    NOT NULL,
            percent     REAL,
            snapshot_ts TEXT    NOT NULL,
            PRIMARY KEY(appid, ach_name, snapshot_ts)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ccu_ts ON ccu(appid, ts_utc)")
    return path


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Tee:
    """同时写 stdout 与日志文件；计划任务下没有控制台，日志是唯一排查依据。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def sample_ccu(appid):
    # _= 时间戳用于穿透 CDN 缓存（见文件顶部 OPENER 处的说明）
    url = CCU_URL + "?appid=%d&_=%d" % (appid, int(time.time() * 1000))
    d = get_json(url)
    n = int(d["response"]["player_count"])
    ts = now_utc()
    with sqlite3.connect(DB) as c:
        c.execute("INSERT OR REPLACE INTO ccu VALUES(?,?,?)", (appid, ts, n))
    return n


def snapshot_achievements(appid):
    """成就完成率变化极慢，抓一次就能用很久；建议每天快照一次。"""
    d = get_json(ACH_URL + "?gameid=%d" % appid)
    rows = d.get("achievementpercentages", {}).get("achievements", [])
    if not rows:
        return 0
    ts = now_utc()
    with sqlite3.connect(DB) as c:
        c.executemany(
            "INSERT OR REPLACE INTO achievements VALUES(?,?,?,?)",
            [(appid, a.get("name", "?"), _f(a.get("percent")), ts) for a in rows],
        )
    return len(rows)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def last_ach_ts(appid):
    with sqlite3.connect(DB) as c:
        r = c.execute(
            "SELECT MAX(snapshot_ts) FROM achievements WHERE appid=?", (appid,)
        ).fetchone()
    return r[0] if r and r[0] else None


def ach_is_stale(appid, hours):
    ts = last_ach_ts(appid)
    if not ts:
        return True
    try:
        prev = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - prev).total_seconds() > hours * 3600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appids", default="1203220", help="逗号分隔的 appid 列表")
    ap.add_argument("--interval", type=int, default=300, help="采样间隔（秒），建议 300")
    ap.add_argument("--minutes", type=int, default=0, help="运行时长（分钟），0 表示一直跑")
    ap.add_argument("--once", action="store_true", help="只采一轮后退出（供计划任务调用）")
    ap.add_argument("--ach-hours", type=int, default=12, help="成就快照最小间隔（小时）")
    ap.add_argument("--proxy", default="auto",
                    help="auto 自动探测（默认）/ none 直连 / http://127.0.0.1:7897 指定代理")
    ap.add_argument("--db", default=os.environ.get("STEAM_DB", DEFAULT_DB))
    ap.add_argument("--log", default=None, help="日志文件路径；计划任务下建议指定")
    args = ap.parse_args()

    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(args.log, "a", encoding="utf-8"))

    global DB, OPENER, PROXY
    DB = init_db(args.db)
    appids = [int(x) for x in args.appids.split(",") if x.strip()]
    resolve_opener(args.proxy, appids[0])

    print("DB  : %s" % DB, flush=True)
    print("代理: %s" % (PROXY or "直连"), flush=True)
    print("目标: %s" % appids, flush=True)

    t0 = time.time()
    round_no = 0

    while True:
        round_no += 1
        for appid in appids:
            try:
                n = sample_ccu(appid)
                print("[%s] appid=%d  CCU=%s" % (now_utc(), appid, format(n, ",")), flush=True)
            except Exception as e:
                print("[%s] appid=%d  CCU 失败: %s" % (now_utc(), appid, e), flush=True)

        # 成就完成率变化极慢：默认 12 小时才快照一次，避免计划任务把表撑爆
        if args.ach_hours >= 0:
            for appid in appids:
                if not ach_is_stale(appid, args.ach_hours):
                    continue
                try:
                    k = snapshot_achievements(appid)
                    print("        appid=%d  成就快照 %d 条" % (appid, k), flush=True)
                except Exception as e:
                    print("        appid=%d  成就失败: %s" % (appid, e), flush=True)

        if args.once:
            break
        if args.minutes and (time.time() - start) > args.minutes * 60:
            break
        time.sleep(args.interval)

    print("done.", flush=True)


if __name__ == "__main__":
    start = time.time()
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped by user", flush=True)
        sys.exit(0)
