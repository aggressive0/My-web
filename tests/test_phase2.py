"""
Phase 2 验收测试：评论采集与聚合（全离线）。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase2 -v

覆盖三类最容易写错、也最难靠肉眼发现的东西：
  1. 接口返回结构不完整时的归一化（缺 author、缺文本、缺时间戳）
  2. 游标不前进时必须停下（实测服务端会回同一个 cursor，不判定就是死循环）
  3. 按日聚合的幂等与窗口裁剪（重复采集不能重复计数）
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import reviews_publish as RP           # noqa: E402
import steam_reviews_collector as R    # noqa: E402


def iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def raw_review(rid, days_ago=0, voted_up=True, lang="schinese", playtime=120, text="好游戏"):
    ts = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())
    return {
        "recommendationid": str(rid),
        "timestamp_created": ts,
        "timestamp_updated": ts,
        "voted_up": voted_up,
        "language": lang,
        "votes_up": 3,
        "refunded": False,
        "review": text,
        "author": {"playtime_at_review": playtime},
    }


class ParseTest(unittest.TestCase):
    def test_full_review(self):
        p = R.parse_review(raw_review("1", playtime=90))
        self.assertEqual(p["review_id"], "1")
        self.assertEqual(p["voted_up"], 1)
        self.assertEqual(p["playtime_minutes"], 90)
        self.assertTrue(p["created_ts"].endswith("+00:00"))

    def test_missing_author_and_text_does_not_raise(self):
        r = raw_review("2")
        r.pop("author")
        r.pop("review")
        p = R.parse_review(r)
        self.assertIsNone(p["playtime_minutes"])
        self.assertIsNone(p["text"])

    def test_text_is_truncated(self):
        p = R.parse_review(raw_review("3", text="x" * 9999))
        self.assertEqual(len(p["text"]), 4000)

    def test_negative_vote_is_zero_not_truthy(self):
        p = R.parse_review(raw_review("4", voted_up=False))
        self.assertEqual(p["voted_up"], 0)


class CollectTest(unittest.TestCase):
    """用假的 fetch_page 驱动，验证游标推进与终止条件。"""

    def setUp(self):
        self._orig = R.fetch_page

    def tearDown(self):
        R.fetch_page = self._orig

    def test_stops_when_server_repeats_cursor(self):
        """服务端没新数据时会把你发过去的游标原样返回。

        第一次必然要「试」一下新游标才算数（没人能凭空知道它是最后一页），
        所以正确行为是：第 2 次拿回同一个游标后立刻停，而不是翻满 pages 次。
        """
        calls = []

        def fake(appid, cursor="*", **kw):
            calls.append(cursor)
            return {"reviews": [raw_review("10")], "cursor": "SAME"}   # 游标不再前进

        R.fetch_page = fake
        reviews, cursor, _ = R.collect(1, pages=5)
        self.assertEqual(len(calls), 2, "拿回同一个游标后应立即停止")
        self.assertEqual(len(reviews), 1)          # 同一页被重复返回，应页内去重
        self.assertEqual(cursor, "SAME")

    def test_stops_on_empty_page(self):
        def fake(appid, cursor="*", **kw):
            if cursor == "*":
                return {"reviews": [raw_review("20")], "cursor": "C1"}
            return {"reviews": [], "cursor": "C1"}

        R.fetch_page = fake
        reviews, _, _ = R.collect(1, pages=4)
        self.assertEqual(len(reviews), 1)

    def test_collects_multiple_pages(self):
        pages = {
            "*": ([raw_review("30"), raw_review("31")], "C1"),
            "C1": ([raw_review("32")], "C2"),
            "C2": ([], "C2"),          # 第 3 页空，游标回同一值
        }

        def fake(appid, cursor="*", **kw):
            rows, nxt = pages[cursor]
            return {"reviews": rows, "cursor": nxt}

        R.fetch_page = fake
        reviews, cursor, _ = R.collect(1, pages=3)
        self.assertEqual([r["review_id"] for r in reviews], ["30", "31", "32"])
        self.assertEqual(cursor, "C2")

    def test_reviews_without_id_are_dropped(self):
        def fake(appid, cursor="*", **kw):
            bad = raw_review("")
            return {"reviews": [bad, raw_review("40")], "cursor": "*"}

        R.fetch_page = fake
        reviews, _, _ = R.collect(1, pages=1)
        self.assertEqual([r["review_id"] for r in reviews], ["40"])


class FallbackTest(unittest.TestCase):
    def test_falls_back_to_legacy_endpoint(self):
        """新接口抛错时应退到旧端点，而不是直接失败。"""
        class FakeResp:
            def __init__(self, body):
                self._b = body.encode("utf-8")

            def read(self):
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else str(req)
                if "IUserReviewsService" in url:
                    raise OSError("new endpoint down")
                return FakeResp(json.dumps({"success": 1, "reviews": [raw_review("50")],
                                            "cursor": "*"}))

        orig_op, orig_sleep = R.OPENER, R.time.sleep
        R.OPENER = FakeOpener()
        R.time.sleep = lambda *_: None
        try:
            resp = R.fetch_page(1, retries=0)
        finally:
            R.OPENER, R.time.sleep = orig_op, orig_sleep
        self.assertEqual(len(resp.get("reviews", [])), 1)


class AggregateTest(unittest.TestCase):
    def test_playtime_buckets(self):
        cases = {None: "unknown", 0: "<1h", 59: "<1h", 60: "1-10h",
                 599: "1-10h", 600: "10-50h", 2999: "10-50h", 3000: "50h+"}
        for minutes, expected in cases.items():
            self.assertEqual(RP.playtime_bucket(minutes), expected, minutes)

    def test_merge_into_daily_counts_and_buckets(self):
        doc = {"days": []}
        rows = [R.parse_review(raw_review("60", voted_up=True, playtime=10)),
                R.parse_review(raw_review("61", voted_up=False, playtime=700))]
        added = RP.merge_into_daily(doc, rows)
        self.assertEqual(added, 2)

        day = doc["days"][0]
        self.assertEqual(day["total"], 2)
        self.assertEqual(day["positive"], 1)
        self.assertEqual(day["negative"], 1)
        self.assertEqual(day["playtime"]["<1h"], 1)
        self.assertEqual(day["playtime"]["10-50h"], 1)

    def test_old_reviews_are_kept_because_some_games_are_sparse(self):
        """实测 PUBG / PoE 的最新评论本身就隔了几个月。

        早先按「30 天窗口」裁剪，结果这几款游戏的聚合全变成 0 条 ——
        数据明明采到了，页面上却像没数据。改为按「最近 N 个有数据的日期」保留。
        """
        doc = {"days": []}
        RP.merge_into_daily(doc, [R.parse_review(raw_review("70", days_ago=200))])
        self.assertEqual(len(doc["days"]), 1)
        self.assertEqual(doc["days"][0]["total"], 1)

    def test_day_cap_keeps_only_most_recent(self):
        doc = {"days": []}
        rows = [R.parse_review(raw_review("c%d" % i, days_ago=i)) for i in range(5)]
        RP.merge_into_daily(doc, rows)
        self.assertEqual(len(doc["days"]), 5)

        orig = RP.MAX_DAYS_KEPT
        RP.MAX_DAYS_KEPT = 3
        try:
            RP.merge_into_daily(doc, [])          # 空批次也会执行裁剪
        finally:
            RP.MAX_DAYS_KEPT = orig
        self.assertEqual(len(doc["days"]), 3)
        dates = [d["date"] for d in doc["days"]]
        self.assertEqual(dates, sorted(dates), "输出必须按日期升序，前端直接当时间轴用")

    def test_totals_and_positive_rate(self):
        doc = {"days": []}
        RP.merge_into_daily(doc, [
            R.parse_review(raw_review("80", voted_up=True, lang="schinese")),
            R.parse_review(raw_review("81", voted_up=True, lang="english")),
            R.parse_review(raw_review("82", voted_up=False, lang="schinese")),
        ])
        RP.recompute_totals(doc)
        self.assertEqual(doc["totals"]["total"], 3)
        self.assertEqual(doc["totals"]["positive_rate"], 66.7)
        self.assertEqual(doc["top_languages"]["schinese"], 2)

    def test_seen_set_is_fifo_capped(self):
        merged = RP.merge_seen(["a", "b", "c"], ["d", "e"], cap=3)
        self.assertEqual(merged, ["c", "d", "e"])

    def test_seen_set_dedupes(self):
        merged = RP.merge_seen(["a", "b"], ["b", "c"], cap=10)
        self.assertEqual(merged, ["a", "b", "c"])


class PublishFlowTest(unittest.TestCase):
    """把 reviews_publish.main() 整条链路跑一遍，GitHub API 全部打桩。

    这类「只在 CI 上才跑」的脚本最容易在拼接处出错：文件路径写错、
    sha 传空导致 422、状态文件漏字段。本地跑一遍比等 Actions 报错快得多。
    """

    def test_main_writes_four_files_with_expected_shape(self):
        writes = {}

        def fake_get_remote(path, repo, token):
            return None, None

        def fake_get_text(path, repo, token):
            return None, None

        def fake_put_remote(path, doc, sha, repo, token, message):
            writes[path] = doc

        def fake_put_text(path, text, sha, repo, token, message):
            writes[path] = text

        saved = (RP.P.get_remote, RP.P.put_remote, RP.get_text, RP.put_text, RP.R.collect)
        RP.P.get_remote = fake_get_remote
        RP.P.put_remote = fake_put_remote
        RP.get_text = fake_get_text
        RP.put_text = fake_put_text
        RP.R.collect = lambda appid, **kw: ([R.parse_review(raw_review("9%s" % appid, playtime=100))],
                                            "*", {"total_reviews": 1000, "total_positive": 900})
        RP.__dict__["print"] = lambda *a, **k: None      # 静音模块内输出
        os.environ["GITHUB_TOKEN"] = "dummy"
        os.environ["GITHUB_REPOSITORY"] = "test/repo"
        try:
            RP.main()
        finally:
            (RP.P.get_remote, RP.P.put_remote, RP.get_text,
             RP.put_text, RP.R.collect) = saved
            RP.__dict__.pop("print", None)

        self.assertEqual(set(writes), {RP.DAILY, RP.STATE, RP.SAMPLES, RP.STATUS})
        daily = writes[RP.DAILY]
        self.assertGreaterEqual(len(daily["games"]), 6)
        g0 = daily["games"][0]
        self.assertIn("totals", g0)
        self.assertEqual(g0["totals"]["total"], 1)
        self.assertEqual(g0["totals"]["positive_rate"], 100.0)
        self.assertIn(str(g0["appid"]), writes[RP.STATE]["seen"])
        self.assertIn("\"appid\"", writes[RP.SAMPLES])
        self.assertEqual(len(writes[RP.STATUS]["games"]), 6)


class WorkflowTest(unittest.TestCase):
    """配置文件与工作流必须自洽 —— 这类错误只在 CI 上炸，本地看不出来。"""

    def test_reviews_workflow_targets_existing_script(self):
        root = os.path.dirname(BACKEND)
        wf = os.path.join(root, ".github", "workflows", "collect-reviews.yml")
        self.assertTrue(os.path.exists(wf))
        with open(wf, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("backend/collectors/reviews_publish.py", text)
        self.assertIn("contents: write", text)
        self.assertNotIn("cancel-in-progress: true", text)   # 与 CCU 抢锁会互相取消

    def test_reviews_workflow_script_exists(self):
        self.assertTrue(os.path.exists(os.path.join(BACKEND, "collectors", "reviews_publish.py")))

    def test_schema_declares_reviews_table(self):
        with open(os.path.join(BACKEND, "data", "schema.sql"), encoding="utf-8") as f:
            sql = f.read()
        self.assertIn("CREATE TABLE IF NOT EXISTS steam_reviews", sql)
        self.assertIn("PRIMARY KEY (appid, review_id)", sql)


if __name__ == "__main__":
    unittest.main(verbosity=2)
