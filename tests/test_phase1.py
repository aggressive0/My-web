"""
Phase 1 验收测试：游戏注册表、数据库 schema、数据合并、API 契约。

跑法（必须在 venv 里，因为要 import flask）：
    .venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v

设计原则：
  * 全部离线 —— 任何一条测试都不发真实网络请求。
    采集函数用 monkeypatch 替换，只为验证「失败隔离/历史保留」这些逻辑，
    而不是验证 Steam 今天是否在线。
  * 用临时目录建库、临时文件写配置，绝不碰 backend/data/steam.db。
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import ci_publish as P            # noqa: E402
import db as store                # noqa: E402
import game_config                # noqa: E402

NOW = "2026-11-16T11:00:00+00:00"
CUTOFF = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(timespec="seconds")

MINI_YAML = """\
defaults:
  interval_minutes: 5
  window_hours: 72
  achievement_interval_hours: 12

games:
  - appid: 1
    slug: alpha
    name: Alpha "Game"        # 含引号与行内注释
    color: "#35D9F2"
    tags: [live-service, cn]
  - appid: 2
    slug: beta
    name: Beta
    enabled: false
"""


def _game(appid, enabled=True, name=None, slug=None):
    return {
        "appid": appid,
        "slug": slug or ("g%d" % appid),
        "name": name or ("Game %d" % appid),
        "color": "#35D9F2",
        "enabled": enabled,
        "interval_minutes": 60,
        "tags": [],
    }


class TempConfig:
    """写一份临时配置，退出时删掉。"""

    def __init__(self, text):
        self.dir = tempfile.mkdtemp(prefix="games-cfg-")
        self.path = os.path.join(self.dir, "games.yaml")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def __enter__(self):
        return self.path

    def __exit__(self, *a):
        try:
            os.remove(self.path)
            os.rmdir(self.dir)
        except OSError:
            pass


class ConfigTest(unittest.TestCase):
    def test_real_config_loads_six_games(self):
        cfg = game_config.load_games()
        self.assertEqual(len(cfg["games"]), 6)
        self.assertEqual(len([g for g in cfg["games"] if g["enabled"]]), 6)
        self.assertEqual(cfg["defaults"]["window_hours"], 72)
        # 身份字段齐全，且 appid/slug 唯一
        self.assertEqual(len({g["appid"] for g in cfg["games"]}), 6)
        self.assertEqual(len({g["slug"] for g in cfg["games"]}), 6)

    def test_subset_parser_handles_quotes_comments_inline_list(self):
        with TempConfig(MINI_YAML) as path:
            cfg = game_config.load_games(path)
        alpha, beta = cfg["games"]
        self.assertEqual(alpha["name"], 'Alpha "Game"')
        self.assertEqual(alpha["color"], "#35D9F2")     # 引号内的 # 不被当注释
        self.assertEqual(alpha["tags"], ["live-service", "cn"])
        self.assertFalse(beta["enabled"])
        self.assertEqual(beta["color"], "#9275FF")      # 缺省从调色板取

    def test_new_game_needs_only_yaml(self):
        """验收要求：新增一款游戏只需改 yaml。"""
        with TempConfig(MINI_YAML) as path:
            with open(path, "a", encoding="utf-8") as f:
                f.write("  - appid: 3\n    slug: gamma\n    name: Gamma\n")
            cfg = game_config.load_games(path)
        self.assertEqual([g["slug"] for g in cfg["games"]], ["alpha", "beta", "gamma"])

    def test_duplicate_appid_rejected(self):
        bad = MINI_YAML + "  - appid: 1\n    slug: dup\n    name: Dup\n"
        with TempConfig(bad) as path:
            with self.assertRaises(game_config.GameConfigError) as ctx:
                game_config.load_games(path)
        self.assertIn("重复", str(ctx.exception))

    def test_duplicate_slug_rejected(self):
        bad = MINI_YAML + "  - appid: 4\n    slug: alpha\n    name: Dup\n"
        with TempConfig(bad) as path:
            with self.assertRaises(game_config.GameConfigError):
                game_config.load_games(path)

    def test_unknown_field_rejected(self):
        bad = MINI_YAML + "  - appid: 5\n    slug: x5\n    name: X\n    typo_field: 1\n"
        with TempConfig(bad) as path:
            with self.assertRaises(game_config.GameConfigError) as ctx:
                game_config.load_games(path)
        self.assertIn("未知字段", str(ctx.exception))

    def test_all_disabled_rejected(self):
        bad = "games:\n  - appid: 9\n    slug: only\n    name: Only\n    enabled: false\n"
        with TempConfig(bad) as path:
            with self.assertRaises(game_config.GameConfigError) as ctx:
                game_config.load_games(path)
        self.assertIn("至少需要一款", str(ctx.exception))

    def test_public_payload_is_json_safe(self):
        import json
        payload = game_config.public_payload()
        json.dumps(payload, ensure_ascii=False)       # 不可序列化会直接抛
        self.assertIn("games", payload)
        self.assertEqual(payload["games"][0]["slug"], "naraka")


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="steam-db-")
        self.path = os.path.join(self.dir, "t.db")
        store.init_db(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.remove(p)
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_schema_has_all_tables(self):
        with store.session(self.path) as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("games", "ccu", "achievements", "collection_logs", "collection_cursors"):
            self.assertIn(t, names)

    def test_init_is_idempotent_and_keeps_history(self):
        with store.session(self.path) as conn:
            conn.execute("INSERT INTO ccu VALUES(?,?,?)", (1, NOW, 123))
        store.init_db(self.path)                     # 再执行一次，不应清表
        with store.session(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ccu").fetchone()[0], 1)

    def test_sync_games_upserts_without_touching_ccu(self):
        with store.session(self.path) as conn:
            store.sync_games(conn, [_game(1, name="Old")])
            conn.execute("INSERT INTO ccu VALUES(?,?,?)", (1, NOW, 10))
        with store.session(self.path) as conn:
            store.sync_games(conn, [_game(1, name="New")])
            row = conn.execute("SELECT name FROM games WHERE appid=1").fetchone()
            self.assertEqual(row["name"], "New")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ccu").fetchone()[0], 1)

    def test_collection_log_records_error_and_stats(self):
        with store.session(self.path) as conn:
            store.log_collection(conn, 1, "ccu", "ok", NOW, rows=1)
            store.log_collection(conn, 2, "ccu", "error", NOW, error="timeout")
        with store.session(self.path) as conn:
            s1 = store.game_stats(conn, 1)
            s2 = store.game_stats(conn, 2)
        self.assertEqual(s1["last_ok_at"], NOW)
        self.assertIsNone(s1["last_error"])
        self.assertEqual(s2["last_error"], "timeout")
        self.assertIsNone(s2["last_ok_at"])


class MergeTest(unittest.TestCase):
    """失败隔离与历史保留 —— 这是「一款挂了不影响其余」的可执行证明。"""

    def setUp(self):
        self._orig = P.fetch_ccu
        self._orig_ach = P.fetch_achievements

    def tearDown(self):
        P.fetch_ccu = self._orig
        P.fetch_achievements = self._orig_ach

    def test_one_failure_does_not_break_others(self):
        def fake(appid):
            if appid == 2:
                raise RuntimeError("upstream 500")
            return 200
        P.fetch_ccu = fake

        out, _ = P.build_ccu([_game(1), _game(2)], {}, NOW, CUTOFF)
        self.assertEqual(out[0]["summary"]["latest"], 200)
        self.assertEqual(len(out[1]["points"]), 0)
        self.assertTrue(out[1]["enabled"])              # 失败不等于停用

    def test_failed_fetch_keeps_existing_points(self):
        def boom(appid):
            raise RuntimeError("timeout")
        P.fetch_ccu = boom
        old = {1: {"points": [{"ts": "2026-11-16T10:00:00+00:00", "p": 100}]}}

        out, _ = P.build_ccu([_game(1)], old, NOW, CUTOFF)
        self.assertEqual(len(out[0]["points"]), 1)
        self.assertEqual(out[0]["summary"]["latest"], 100)

    def test_disabled_game_keeps_history_and_is_not_fetched(self):
        called = []

        def spy(appid):
            called.append(appid)
            return 999
        P.fetch_ccu = spy
        old = {2: {"points": [{"ts": "2026-11-16T10:00:00+00:00", "p": 55}]}}

        out, _ = P.build_ccu([_game(1), _game(2, enabled=False)], old, NOW, CUTOFF)
        self.assertNotIn(2, called)                     # 停用后不再打接口
        self.assertEqual(out[1]["summary"]["latest"], 55)   # 但历史仍在
        self.assertFalse(out[1]["enabled"])

    def test_window_trims_old_points(self):
        old = {1: {"points": [
            {"ts": "2020-01-01T00:00:00+00:00", "p": 1},
            {"ts": "2026-11-16T10:00:00+00:00", "p": 2},
        ]}}
        P.fetch_ccu = lambda appid: 3
        out, _ = P.build_ccu([_game(1)], old, NOW, CUTOFF)
        self.assertEqual([p["p"] for p in out[0]["points"]], [2, 3])

    def test_same_minute_overwrites_instead_of_appending(self):
        old = {1: {"points": [{"ts": "2026-11-16T11:00:30+00:00", "p": 1}]}}
        P.fetch_ccu = lambda appid: 7
        out, _ = P.build_ccu([_game(1)], old, NOW, CUTOFF)
        self.assertEqual(len(out[0]["points"]), 1)
        self.assertEqual(out[0]["points"][0]["p"], 7)

    def test_funnel_failure_falls_back_to_previous(self):
        def boom(appid):
            raise RuntimeError("nope")
        P.fetch_achievements = boom
        prev = {1: {"appid": 1, "name": "Game 1", "count": 9, "span": 60.0,
                    "first": 90.0, "last": 30.0, "achievements": [{"n": "a", "p": 90.0}]}}

        out = P.build_funnels([_game(1), _game(2)], prev)
        self.assertEqual(len(out), 1)                   # 无历史可沿用的被跳过
        self.assertEqual(out[0]["count"], 9)



class ApiTest(unittest.TestCase):
    """API 契约：统一 meta、错误可预期、无数据不伪造。"""

    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A
        cls.client = A.app.test_client()

    def test_list_games_contract(self):
        r = self.client.get("/api/games")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        for key in ("api_version", "generated_at", "source", "data_type", "count"):
            self.assertIn(key, j["meta"])
        self.assertEqual(j["meta"]["count"], len(j["data"]))
        self.assertEqual(len(j["data"]), 6)
        g = j["data"][0]
        for key in ("appid", "slug", "name", "color", "enabled", "tags", "data_type", "collection"):
            self.assertIn(key, g)

    def test_single_game_ok(self):
        r = self.client.get("/api/games/1203220")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["slug"], "naraka")

    def test_unknown_game_returns_structured_404(self):
        r = self.client.get("/api/games/999999")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "GAME_NOT_FOUND")

    def test_invalid_appid_returns_400(self):
        r = self.client.get("/api/games/abc")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_APPID")

    def test_missing_db_reports_unavailable_not_fake_zero(self):
        old = os.environ.get("STEAM_DB")
        os.environ["STEAM_DB"] = os.path.join(tempfile.gettempdir(), "definitely-missing-xyz.db")
        try:
            j = self.client.get("/api/games").get_json()
        finally:
            if old is None:
                os.environ.pop("STEAM_DB", None)
            else:
                os.environ["STEAM_DB"] = old
        self.assertFalse(j["meta"]["db_available"])
        self.assertEqual(j["data"][0]["data_type"], "unavailable")
        self.assertIsNone(j["data"][0]["collection"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
