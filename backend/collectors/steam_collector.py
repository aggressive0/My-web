"""
Steam 公开数据采集器（本机侧：细粒度 CCU + 成就快照 → SQLite）

与 CI 侧的分工：
    本机  5 分钟级采样 → backend/data/steam.db（细粒度分析、成就漏斗）
    CI    每小时一次   → data 分支 JSON（线上展示）

数据来源（均无需 API Key）：
  - 同时在线人数  ISteamUserStats/GetNumberOfCurrentPlayers/v1?appid=
  - 成就完成率    ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2?gameid=

游戏列表来自 backend/config/games.yaml（唯一事实来源），不再硬编码 AppID。

口径说明（写进答辩的必备前提）：
  - CCU = 某一时刻的同时在线人数，不是 DAU，也不是活跃玩家数。
  - 成就完成率 = 全量玩家中的累计完成百分比，用它反推「进度流失」是近似，
    不能等同于留存率。

用法：
  python steam_collector.py --once                 # 按配置采一轮（计划任务用这个）
  python steam_collector.py --interval 300         # 常驻循环
  python steam_collector.py --appids 1203220 --once  # 临时只采某款，绕过配置

环境变量：
  GAMES_CONFIG  配置文件路径，默认 backend/config/games.yaml
  STEAM_PROXY   代理地址；留空则直连（--proxy 优先）
  STEAM_DB      SQLite 路径，默认 backend/data/steam.db
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import db as store
import game_config

CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
ACH_URL = "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"

PROXY = os.environ.get("STEAM_PROXY", "http://127.0.0.1:7897")
DB = os.environ.get("STEAM_DB", store.DEFAULT_DB)


def make_opener(proxy):
    # 显式传入代理字典（无代理时传空字典），否则 urllib 会去读环境变量
    # https_proxy —— 本机那个代理端口时常是死的，会让「直连」变成 502。
    mapping = {"http": proxy, "https": proxy} if proxy else {}
    op = urllib.request.build_opener(urllib.request.ProxyHandler(mapping))
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


def collect_ccu(appid):
    """采一次 CCU：写 ccu 表 + 记一条日志。失败抛异常，由调用方隔离。"""
    started = time.time()
    try:
        # _= 时间戳用于穿透 CDN 缓存（见文件顶部 OPENER 处的说明）
        url = CCU_URL + "?appid=%d&_=%d" % (appid, int(time.time() * 1000))
        d = get_json(url)
        n = int(d["response"]["player_count"])
        ts = now_utc()
        with store.session(DB) as conn:
            conn.execute("INSERT OR REPLACE INTO ccu VALUES(?,?,?)", (appid, ts, n))
            store.log_collection(conn, appid, "ccu", "ok", ts,
                                 duration_ms=int((time.time() - started) * 1000), rows=1)
        return n
    except Exception as e:
        with store.session(DB) as conn:
            store.log_collection(conn, appid, "ccu", "error", now_utc(),
                                 duration_ms=int((time.time() - started) * 1000),
                                 error=str(e)[:500])
        raise


def snapshot_achievements(appid):
    """成就完成率变化极慢，抓一次就能用很久；默认 12 小时快照一次。"""
    started = time.time()
    try:
        d = get_json(ACH_URL + "?gameid=%d" % appid)
        rows = d.get("achievementpercentages", {}).get("achievements", [])
        if not rows:
            raise RuntimeError("成就列表为空（该游戏可能未开放成就接口）")
        ts = now_utc()
        with store.session(DB) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO achievements VALUES(?,?,?,?)",
                [(appid, a.get("name", "?"), _f(a.get("percent")), ts) for a in rows],
            )
            store.log_collection(conn, appid, "achievements", "ok", ts,
                                 duration_ms=int((time.time() - started) * 1000), rows=len(rows))
        return len(rows)
    except Exception as e:
        with store.session(DB) as conn:
            store.log_collection(conn, appid, "achievements", "error", now_utc(),
                                 duration_ms=int((time.time() - started) * 1000),
                                 error=str(e)[:500])
        raise


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def last_ach_ts(appid):
    with store.session(DB) as conn:
        r = conn.execute(
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


def resolve_targets(args):
    """--appids 优先（临时排查用）；否则读配置中的 enabled 游戏。"""
    if args.appids:
        ids = [int(x) for x in args.appids.split(",") if x.strip()]
        cfg = None
        try:
            cfg = game_config.load_games(args.config)
        except game_config.GameConfigError:
            pass
        known = {g["appid"]: g for g in (cfg["games"] if cfg else [])}
        return [{"appid": a,
                 "name": known.get(a, {}).get("name") or str(a),
                 "interval_minutes": known.get(a, {}).get("interval_minutes")} for a in ids], cfg

    cfg = game_config.load_games(args.config)
    games = [g for g in cfg["games"] if g["enabled"]]
    if not games:
        raise SystemExit("配置里没有 enabled: true 的游戏")
    return games, cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appids", default="",
                    help="逗号分隔的 appid；留空则读取 backend/config/games.yaml 的启用列表")
    ap.add_argument("--interval", type=int, default=None,
                    help="采样间隔（秒）；留空取配置的 defaults.interval_minutes")
    ap.add_argument("--minutes", type=int, default=0, help="运行时长（分钟），0 表示一直跑")
    ap.add_argument("--once", action="store_true", help="只采一轮后退出（供计划任务调用）")
    ap.add_argument("--ach-hours", type=int, default=None,
                    help="成就快照最小间隔（小时）；留空取配置；-1 表示不快照")
    ap.add_argument("--proxy", default="auto",
                    help="auto 自动探测（默认）/ none 直连 / http://127.0.0.1:7897 指定代理")
    ap.add_argument("--db", default=os.environ.get("STEAM_DB", store.DEFAULT_DB))
    ap.add_argument("--config", default=os.environ.get("GAMES_CONFIG"),
                    help="游戏配置路径，默认 backend/config/games.yaml")
    ap.add_argument("--log", default=None, help="日志文件路径；计划任务下建议指定")
    args = ap.parse_args()

    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(args.log, "a", encoding="utf-8"))

    global DB
    DB = store.init_db(args.db)

    targets, cfg = resolve_targets(args)
    defaults = (cfg or {}).get("defaults", game_config.DEFAULT_DEFAULTS)
    interval = args.interval if args.interval is not None else defaults["interval_minutes"] * 60
    ach_hours = args.ach_hours if args.ach_hours is not None else defaults["achievement_interval_hours"]

    with store.session(DB) as conn:
        if cfg:
            store.sync_games(conn, cfg["games"])

    appids = [t["appid"] for t in targets]
    resolve_opener(args.proxy, appids[0])

    print("DB    : %s" % DB, flush=True)
    print("配置  : %s" % (cfg["source"] if cfg else "（未使用，--appids 直指定）"), flush=True)
    print("代理  : %s" % (PROXY or "直连"), flush=True)
    print("目标  : %s" % ", ".join("%s(%d)" % (t["name"], t["appid"]) for t in targets), flush=True)
    print("间隔  : %ds | 成就快照间隔 %sh" % (interval, ach_hours), flush=True)

    t_start = time.time()
    round_no = 0

    while True:
        round_no += 1
        for t in targets:
            appid = t["appid"]
            try:
                n = collect_ccu(appid)
                print("[%s] %-16s CCU=%s" % (now_utc(), t["name"], format(n, ",")), flush=True)
            except Exception as e:
                print("[%s] %-16s CCU 失败: %s" % (now_utc(), t["name"], e), flush=True)

        # 成就完成率变化极慢：默认 12 小时才快照一次，避免计划任务把表撑爆
        if ach_hours >= 0:
            for t in targets:
                appid = t["appid"]
                if not ach_is_stale(appid, ach_hours):
                    continue
                try:
                    k = snapshot_achievements(appid)
                    print("        %-16s 成就快照 %d 条" % (t["name"], k), flush=True)
                except Exception as e:
                    print("        %-16s 成就失败: %s" % (t["name"], e), flush=True)

        if args.once:
            break
        if args.minutes and (time.time() - t_start) > args.minutes * 60:
            break
        time.sleep(interval)

    print("done. 用时 %.1fs" % (time.time() - t_start), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped by user", flush=True)
        sys.exit(0)
    except game_config.GameConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr, flush=True)
        sys.exit(2)
