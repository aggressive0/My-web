"""Offline tests for the real language-market globe publisher."""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, os.path.join(BACKEND, "collectors"))
sys.path.insert(0, os.path.join(BACKEND, "analysis"))

import globe_publish as G


class GlobePublishTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

    def rows(self, lang="schinese", count=25):
        return [{
            "appid": 730, "id": str(i), "date": "2026-11-18",
            "lang": lang, "voted_up": i % 3 != 0,
            "text": "游戏卡顿严重，优化很差" if i % 3 == 0 else "good gameplay and fun"
        } for i in range(count)]

    def test_builds_derived_market_without_claiming_player_location(self):
        doc = G.build_doc(self.rows(), [{"appid": 730, "name": "Counter-Strike 2"}],
                          now=self.now, min_reviews=20, period_days=7)
        self.assertEqual(len(doc["markets"]), 1)
        market = doc["markets"][0]
        self.assertEqual(market["language"], "schinese")
        self.assertEqual(market["review_count"], 25)
        self.assertEqual(market["data_type"], "derived")
        self.assertIn("not reviewer/player geolocation", doc["definition"]["market"])

    def test_suppresses_small_samples(self):
        doc = G.build_doc(self.rows(count=5), [{"appid": 730, "name": "CS2"}],
                          now=self.now, min_reviews=20, period_days=7)
        self.assertEqual(doc["markets"], [])

    def test_unknown_language_is_not_guessed(self):
        doc = G.build_doc(self.rows(lang="made_up_language"), [{"appid": 730}],
                          now=self.now, min_reviews=20, period_days=7)
        self.assertEqual(doc["markets"], [])

    def test_market_is_game_scoped(self):
        rows = self.rows() + [dict(r, appid=440, id="tf2-" + r["id"]) for r in self.rows()]
        doc = G.build_doc(rows, [{"appid": 730, "name": "CS2"}, {"appid": 440, "name": "TF2"}],
                          now=self.now, min_reviews=20, period_days=7)
        self.assertEqual({m["appid"] for m in doc["markets"]}, {730, 440})


if __name__ == "__main__":
    unittest.main()
