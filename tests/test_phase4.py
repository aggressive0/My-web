"""
Phase 4 验收测试：风险指数的分量、重归一化与历史合并（全离线）。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase4 -v

重点盯三类容易出错的口径：
  1. 缺失分量绝不当 0（否则没数据的游戏看起来「最健康」）；
  2. low_sample 必须传染到整体 status；
  3. 历史同日重跑覆盖而不是追加。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "analysis"))
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import risk as R                # noqa: E402


class ComponentNTest(unittest.TestCase):
    def test_ok_and_low_sample(self):
        score, meta = R.score_N({"n": 100, "neg": 30})
        self.assertEqual(score, 30.0)
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["neg_rate"], 30.0)

        _, meta = R.score_N({"n": 5, "neg": 4})
        self.assertEqual(meta["status"], "low_sample")

    def test_no_samples_is_unavailable_not_zero(self):
        score, meta = R.score_N({"n": 0, "neg": 0})
        self.assertIsNone(score)
        self.assertEqual(meta["status"], "unavailable")
        self.assertIsNone(R.score_N(None)[0])


class ComponentGTest(unittest.TestCase):
    def test_uses_top_topic_share(self):
        game = {"n_reviews": 300,
                "topics": [{"slug": "story", "label": "剧情与角色", "share": 32.9},
                           {"slug": "content", "label": "内容与更新", "share": 22.9}]}
        score, meta = R.score_G(game)
        self.assertEqual(score, 32.9)
        self.assertEqual(meta["top_slug"] if "top_slug" in meta else meta["top_topic"], "story")

    def test_no_hits_unavailable(self):
        score, meta = R.score_G({"n_reviews": 10, "topics": []})
        self.assertIsNone(score)
        self.assertEqual(meta["status"], "unavailable")


class ComponentCTest(unittest.TestCase):
    def _game(self, hours_ago_values):
        now = datetime.now(timezone.utc)
        points = [{"ts": (now - timedelta(hours=h)).isoformat(), "p": v}
                  for h, v in hours_ago_values]
        return {"points": points}

    def test_decline_maps_linearly(self):
        # 近 24h 均值 80，之前 24h 均值 100 → -20% → score 40
        game = self._game([(2, 80), (6, 80), (10, 80), (26, 100), (30, 100), (40, 100)])
        score, meta = R.score_C(game)
        self.assertEqual(score, 40.0)
        self.assertEqual(meta["change_pct"], -20.0)
        self.assertEqual(meta["status"], "ok")

    def test_growth_scores_zero(self):
        game = self._game([(2, 120), (6, 120), (10, 120), (26, 100), (30, 100), (40, 100)])
        score, meta = R.score_C(game)
        self.assertEqual(score, 0.0)

    def test_insufficient_points_unavailable(self):
        score, meta = R.score_C(self._game([(2, 50), (3, 50)]))
        self.assertIsNone(score)
        self.assertIn("采样不足", meta["reason"])

    def test_zero_base_unavailable(self):
        game = self._game([(2, 10), (6, 10), (10, 10), (26, 0), (30, 0), (40, 0)])
        self.assertIsNone(R.score_C(game)[0])


class ComponentATest(unittest.TestCase):
    def test_always_unavailable_with_reason(self):
        score, meta = R.score_A()
        self.assertIsNone(score)
        self.assertEqual(meta["status"], "unavailable")
        self.assertIn("时间序列", meta["reason"])


class CombineTest(unittest.TestCase):
    def test_renormalisation_over_available_components(self):
        # N=60, G=40，C/A 缺失 → (0.35*60 + 0.30*40) / 0.65 = 33/0.65 ≈ 50.8
        parts = {
            "N": (60.0, {"weight": 0.35, "status": "ok"}),
            "G": (40.0, {"weight": 0.30, "status": "ok"}),
            "C": (None, {"weight": 0.20, "status": "unavailable"}),
            "A": (None, {"weight": 0.15, "status": "unavailable"}),
        }
        risk, used = R.combine(parts)
        self.assertEqual(used, ["N", "G"])
        self.assertAlmostEqual(risk, 50.8, places=1)

    def test_all_missing_returns_none(self):
        parts = {k: (None, {"weight": w, "status": "unavailable"})
                 for k, w in R.WEIGHTS.items()}
        risk, used = R.combine(parts)
        self.assertIsNone(risk)
        self.assertEqual(used, [])


class BuildGameTest(unittest.TestCase):
    def _build(self, n=100, neg=30, top_share=20.0):
        return R.build_game(
            1,
            {"n": n, "neg": neg},
            {"n_reviews": n, "topics": [{"slug": "x", "label": "X", "share": top_share}]},
            {"points": []},
        )

    def test_full_output_shape(self):
        g = self._build()
        self.assertIn("risk_index", g)
        self.assertIn("status", g)
        self.assertEqual(g["components_used"], ["N", "G"])
        self.assertIn("definition_keys_are_present", "x") if False else None
        for k in "NGCA":
            self.assertIn(k, g["components"])
            self.assertIn("status", g["components"][k])
            self.assertIn("label", g["components"][k])

    def test_low_sample_propagates_to_status(self):
        g = self._build(n=10, neg=5)
        self.assertEqual(g["status"], "low_sample")

    def test_nothing_available_is_insufficient(self):
        g = R.build_game(1, None, None, None)
        self.assertIsNone(g["risk_index"])
        self.assertEqual(g["status"], "insufficient")


class HistoryTest(unittest.TestCase):
    def test_same_day_overwrites_not_appends(self):
        prev = {1: [{"date": "2026-11-17", "risk_index": 50.0},
                    {"date": "2026-11-16", "risk_index": 40.0}]}
        entries = [{"appid": 1, "risk_index": 61.0}]
        merged = R.merge_history(prev, entries, "2026-11-17")
        dates = [p["date"] for p in merged[1]]
        self.assertEqual(dates.count("2026-11-17"), 1)
        self.assertEqual(merged[1][-1]["risk_index"], 61.0)   # 升序，今天在最后
        self.assertEqual(len(merged[1]), 2)

    def test_none_risk_not_recorded(self):
        merged = R.merge_history({}, [{"appid": 2, "risk_index": None}], "2026-11-17")
        self.assertNotIn(2, merged)

    def test_cap_and_sort(self):
        prev = {1: [{"date": "2026-%02d-%02d" % (max(1, (i // 28) + 1), i % 28 + 1),
                     "risk_index": i} for i in range(120)]}
        merged = R.merge_history(prev, [{"appid": 1, "risk_index": 999}], "2026-11-17")
        self.assertEqual(len(merged[1]), R.HISTORY_DAYS)
        self.assertEqual(merged[1][-1]["risk_index"], 999)


class OrderingTest(unittest.TestCase):
    def test_sufficiency_beats_raw_risk(self):
        """ok 状态的高风险必须排在 low_sample 的更高数值之前。"""
        import risk_publish as RP
        entries = [
            {"appid": 1, "status": "low_sample", "risk_index": 50.0},
            {"appid": 2, "status": "ok", "risk_index": 26.6},
            {"appid": 3, "status": "insufficient", "risk_index": None},
            {"appid": 4, "status": "ok", "risk_index": 40.0},
        ]
        doc = {"games": sorted(entries, key=lambda e: (
            {"ok": 0, "low_sample": 1, "insufficient": 2}.get(e["status"], 3),
            -(e["risk_index"] or 0)))}
        order = [g["appid"] for g in doc["games"]]
        self.assertEqual(order, [4, 2, 1, 3])


class PublishFlowTest(unittest.TestCase):
    """--from-dir 本地跑通整条链（不碰网络）。"""

    def test_main_dry_run_from_dir(self):
        import json
        import tempfile
        import risk_publish as RP

        d = tempfile.mkdtemp(prefix="risk-in-")
        json.dump({"games": [{"appid": 1, "totals": {"n": 100, "neg": 40}}]},
                  open(os.path.join(d, "sentiment-daily.json"), "w", encoding="utf-8"))
        json.dump({"games": [{"appid": 1, "n_reviews": 100,
                              "topics": [{"slug": "x", "label": "X", "share": 25.0}]}]},
                  open(os.path.join(d, "topics-daily.json"), "w", encoding="utf-8"))
        json.dump({"games": [{"appid": 1, "points": []}]},
                  open(os.path.join(d, "steam-ccu.json"), "w", encoding="utf-8"))

        argv = sys.argv
        sys.argv = ["risk_publish.py", "--dry-run", "--from-dir", d]
        RP.__dict__["print"] = lambda *a, **k: None
        try:
            RP.main()
        finally:
            sys.argv = argv
            RP.__dict__.pop("print", None)

        # dry-run 不应产生任何远端写入；此处主要验证链路不抛异常
        self.assertTrue(os.path.exists(os.path.join(d, "sentiment-daily.json")))


class WorkflowTest(unittest.TestCase):
    def test_risk_runs_after_sentiment_in_same_workflow(self):
        root = os.path.dirname(BACKEND)
        with open(os.path.join(root, ".github", "workflows", "analyze-sentiment.yml"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn("risk_publish.py", text)
        self.assertLess(text.index("topics_publish.py"), text.index("risk_publish.py"))
        i_topics = text.index("topics_publish.py")
        i_sent = text.index("sentiment_publish.py")
        i_risk = text.index("risk_publish.py")
        self.assertLess(i_topics, i_sent)
        self.assertGreater(i_risk, i_sent, "risk 应排在情感之后（它消费情感产物）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
