"""
信号 → 检索查询的测试（Phase 6.5）。

要防的是「查什么」这件事退化成常量或随机：
- 差评高频词必须真的来自评论，不是硬编码；
- 指标方向决定补充哪些主题词，方向判错等于检索错方向；
- 没有信号时必须回落，而不是拼出一串垃圾词。
"""

import os
import shutil
import sys
import tempfile
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)

from ai.recall.signals import (build_knowledge_query, detect_directions,   # noqa: E402
                               extract_terms)


def metrics(**values):
    return {"evidence": [{"name": k, "value": v} for k, v in values.items()]}


class ExtractTermsTest(unittest.TestCase):
    def test_picks_frequent_topical_words(self):
        texts = ["the game is unstable and buggy",
                 "unstable servers again, very buggy",
                 "loading screens are slow"]
        top = dict(extract_terms(texts, limit=5))
        self.assertIn("unstable", top)
        self.assertIn("buggy", top)

    def test_drops_stopwords_and_short_tokens(self):
        texts = ["this is the game and they are not good"] * 3
        self.assertEqual(extract_terms(texts, limit=5), [])

    def test_single_occurrence_is_noise(self):
        self.assertEqual(extract_terms(["unique"], limit=5), [])
        self.assertTrue(extract_terms(["crash", "crash"], limit=5))

    def test_strips_punctuation_and_case(self):
        words = dict(extract_terms(["Crash! crash. CRASH,"], limit=3))
        self.assertEqual(words.get("crash"), 3)

    def test_profanity_never_reaches_the_query(self):
        """实测 90 天窗口里捞出过 bullshit / fucking。情绪由指标表达，查询词只留话题。"""
        texts = ["this bullshit is fucking broken", "bullshit matchmaking again"] * 2
        words = [w for w, _ in extract_terms(texts, limit=5)]
        self.assertNotIn("bullshit", words)
        self.assertNotIn("fucking", words)
        self.assertIn("broken", words)
        self.assertIn("matchmaking", words)

    def test_same_stem_is_deduplicated(self):
        """feel / feels 同时进查询只占位不增信息。"""
        words = [w for w, _ in extract_terms(["feel bad", "feels bad", "feels bad"], limit=5)]
        self.assertEqual(len([w for w in words if w.startswith("feel")]), 1)

    def test_apostrophe_variants_are_filtered(self):
        """it's / don't / player's 归一化后要能命中停用词，否则会污染查询。

        这是实机发现的：生成的查询里出现过 "it's"。
        """
        texts = ["it's broken, don't buy", "it's broken again"] * 2
        words = [w for w, _ in extract_terms(texts, limit=5)]
        self.assertNotIn("its", words)
        self.assertNotIn("dont", words)
        self.assertIn("broken", words)

    def test_empty_input(self):
        self.assertEqual(extract_terms([]), [])
        self.assertEqual(extract_terms(None), [])


class DirectionTest(unittest.TestCase):
    def test_ccu_drop_detected(self):
        self.assertIn("ccu_down", detect_directions(metrics(ccu_change_24h_pct=-5.2)))

    def test_small_ccu_change_is_not_a_signal(self):
        self.assertEqual(detect_directions(metrics(ccu_change_24h_pct=-0.5)), [])

    def test_high_negative_ratio_detected(self):
        self.assertIn("sentiment_down",
                      detect_directions(metrics(negative_ratio_window=0.42)))

    def test_missing_metric_is_not_guessed(self):
        self.assertEqual(detect_directions({"evidence": []}), [])


class BuildQueryTest(unittest.TestCase):
    def test_includes_direction_hint_and_terms(self):
        query, basis = build_knowledge_query(
            metrics(ccu_change_24h_pct=-8.0, negative_ratio_window=0.35),
            ["unstable unstable", "loading loading"])
        self.assertIn("服务器", query)          # ccu_down 的方向词
        self.assertIn("修复", query)            # sentiment_down 的方向词
        self.assertIn("unstable", query)        # 评论高频词
        self.assertEqual(set(basis["directions"]), {"ccu_down", "sentiment_down"})

    def test_falls_back_when_no_signal(self):
        query, basis = build_knowledge_query({"evidence": []}, [])
        self.assertEqual(basis["directions"], [])
        self.assertEqual(basis["terms"], [])
        self.assertIn("版本更新", query)
        self.assertIn("回落", basis["note"])

    def test_query_has_no_duplicate_whitespace(self):
        query, _ = build_knowledge_query(metrics(ccu_change_24h_pct=-9), ["crash crash"])
        self.assertNotIn("  ", query)
        self.assertEqual(query, query.strip())


class CollectorIntegrationTest(unittest.TestCase):
    """评论取样锚点必须是「最新一条评论」，不是 now()。"""

    def test_recent_negative_texts(self):
        import sqlite3
        from datetime import datetime, timedelta, timezone
        from ai.recall.metrics import MetricsCollector

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "steam.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE steam_reviews (appid INTEGER, review_id TEXT, created_ts TEXT,"
            " voted_up INTEGER, language TEXT, playtime_minutes INTEGER, votes_up INTEGER,"
            " refunded INTEGER, text TEXT);")
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        rows = [
            (1, "a", (now - timedelta(days=1)).isoformat(), 0, "en", 10, 0, 0, "unstable"),
            (1, "b", (now - timedelta(days=2)).isoformat(), 1, "en", 10, 0, 0, "good game"),
            (1, "c", (now - timedelta(days=90)).isoformat(), 0, "en", 10, 0, 0, "ancient"),
        ]
        conn.executemany("INSERT INTO steam_reviews VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

        texts = MetricsCollector(db).recent_negative_texts(1, days=30)
        self.assertEqual(texts, ["unstable"], "只应取窗口内的差评，且不含好评")

    def test_no_reviews_returns_empty(self):
        import sqlite3
        from ai.recall.metrics import MetricsCollector

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "steam.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE steam_reviews (appid INTEGER, review_id TEXT, created_ts TEXT,"
            " voted_up INTEGER, language TEXT, playtime_minutes INTEGER, votes_up INTEGER,"
            " refunded INTEGER, text TEXT);")
        conn.commit()
        conn.close()
        self.assertEqual(MetricsCollector(db).recent_negative_texts(1), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
