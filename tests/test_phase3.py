"""
Phase 3 验收测试：情感分类头的边界、聚合口径、发布链路（全离线）。

跑法：
    .venv/Scripts/python.exe -m unittest tests.test_phase3 -v

设计：
  * 不加载真的 MiniLM（要下 470MB）—— 用 HashEmbedder 与假模型验证接线；
  * 不训练真模型（sklearn 缺失时自动跳过那两条），但真正训练时用的
    band/confidence/聚合口径必须被测到，因为那些才是产出数字的地方；
  * 重点断言「每个比例都带分母」——这是本项目最容易被写坏的一条口径。
"""

import json
import os
import sys
import unittest

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "collectors"))
sys.path.insert(0, os.path.join(BACKEND, "ml"))

import embedder as E            # noqa: E402
import head as H                # noqa: E402
import sentiment_publish as SP  # noqa: E402

try:
    import sklearn  # noqa: F401
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


class BandTest(unittest.TestCase):
    def test_band_boundaries(self):
        self.assertEqual(H.band(0.95), "pos")
        self.assertEqual(H.band(0.60), "pos")     # 含边界
        self.assertEqual(H.band(0.59), "neu")
        self.assertEqual(H.band(0.41), "neu")
        self.assertEqual(H.band(0.40), "neg")     # 含边界
        self.assertEqual(H.band(0.05), "neg")

    def test_confidence_is_distance_from_half(self):
        self.assertAlmostEqual(H.confidence(0.5), 0.0)
        self.assertAlmostEqual(H.confidence(0.75), 0.5)
        self.assertAlmostEqual(H.confidence(0.0), 1.0)
        self.assertAlmostEqual(H.confidence(1.0), 1.0)


class ParseSamplesTest(unittest.TestCase):
    def _line(self, **kw):
        base = {"appid": 1, "id": "r1", "created": "2026-11-16T10:00:00+00:00",
                "text": "good game", "voted_up": True, "lang": "english"}
        base.update(kw)
        return json.dumps(base, ensure_ascii=False)

    def test_skips_bad_json_empty_text_and_missing_date(self):
        text = "\n".join([
            self._line(),
            "{not json",
            self._line(id="r2", text="   "),
            self._line(id="r3", created=""),
        ])
        rows, skipped = SP.parse_samples(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped["bad_json"], 1)
        self.assertEqual(skipped["no_text"], 1)
        self.assertEqual(skipped["no_date"], 1)

    def test_duplicate_ids_keep_last(self):
        text = "\n".join([self._line(text="old"), self._line(text="new")])
        rows, _ = SP.parse_samples(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "new")

    def test_date_is_derived_from_created(self):
        rows, _ = SP.parse_samples(self._line())
        self.assertEqual(rows[0]["date"], "2026-11-16")


class AggregateTest(unittest.TestCase):
    def _rows(self):
        return [
            {"appid": 1, "id": "a", "date": "2026-11-16", "text": "x", "voted_up": True, "lang": "en"},
            {"appid": 1, "id": "b", "date": "2026-11-16", "text": "y", "voted_up": True, "lang": "en"},
            {"appid": 1, "id": "c", "date": "2026-11-16", "text": "z", "voted_up": False, "lang": "en"},
            {"appid": 1, "id": "d", "date": "2026-11-15", "text": "w", "voted_up": False, "lang": "zh"},
        ]

    def test_counts_and_totals_are_consistent(self):
        games = SP.aggregate(self._rows(), [0.9, 0.8, 0.1, 0.2])
        self.assertEqual(len(games), 1)
        g = games[0]
        self.assertEqual(g["totals"]["n"], 4)
        self.assertEqual(g["totals"]["pos"], 2)
        self.assertEqual(g["totals"]["neg"], 2)
        self.assertEqual(g["totals"]["neu"], 0)
        self.assertEqual(g["totals"]["pos_rate"], 50.0)
        # 真实投票率必须与模型判定并列，用于看校准偏差
        self.assertEqual(g["totals"]["voted_up_rate"], 50.0)

    def test_percentages_always_carry_denominator(self):
        """本项目红线：不允许出现没有 sample_size 的比例。"""
        g = SP.aggregate(self._rows(), [0.9, 0.8, 0.1, 0.2])[0]
        self.assertIn("n", g["totals"])
        for day in g["days"]:
            self.assertIn("n", day)
            self.assertGreater(day["n"], 0)

    def test_neutral_band_is_not_forced_into_pos_or_neg(self):
        games = SP.aggregate(self._rows(), [0.5, 0.5, 0.5, 0.5])
        t = games[0]["totals"]
        self.assertEqual(t["neu"], 4)
        self.assertEqual(t["pos"], 0)
        self.assertEqual(t["pos_rate"], 0.0)

    def test_examples_are_capped_and_traceable(self):
        rows = [{"appid": 1, "id": "p%d" % i, "date": "2026-11-16", "text": "t",
                 "voted_up": True, "lang": "en"} for i in range(5)]
        g = SP.aggregate(rows, [0.9] * 5)[0]
        ex = g["days"][0]["examples"]
        self.assertEqual(len(ex["pos"]), SP.EXAMPLES_PER_CLASS)
        self.assertTrue(all(isinstance(i, str) for i in ex["pos"]))

    def test_days_sorted_and_coverage_reported(self):
        games = SP.aggregate(self._rows(), [0.9, 0.8, 0.1, 0.2])
        g = games[0]
        dates = [d["date"] for d in g["days"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(g["coverage"]["first"], "2026-11-15")
        self.assertEqual(g["coverage"]["last"], "2026-11-16")
        self.assertEqual(g["coverage"]["days"], 2)

    def test_low_confidence_share_present(self):
        g = SP.aggregate(self._rows(), [0.55, 0.9, 0.1, 0.2])[0]
        self.assertIsNotNone(g["totals"]["low_conf_share"])
        self.assertGreaterEqual(g["totals"]["low_conf_share"], 0.0)


class HashEmbedderTest(unittest.TestCase):
    def test_shape_and_normalisation(self):
        emb = E.HashEmbedder(dim=32)
        v = emb.encode(["hello world", ""])
        self.assertEqual(v.shape, (2, 32))
        self.assertAlmostEqual(float((v[0] ** 2).sum()), 1.0, places=5)
        self.assertAlmostEqual(float((v[1] ** 2).sum()), 0.0, places=5)   # 空串全零，不 NaN

    def test_empty_input(self):
        v = E.HashEmbedder(dim=8).encode([])
        self.assertEqual(v.shape[0], 0)


@unittest.skipUnless(HAS_SKLEARN, "未安装 scikit-learn，跳过训练相关用例")
class TrainTest(unittest.TestCase):
    def _data(self, n=120):
        import numpy as np
        rng = np.random.default_rng(0)
        X = rng.normal(size=(n, 16))
        y = (X[:, 0] > 0).astype(int)
        return X, y

    def test_train_reports_cv_and_sample_size(self):
        X, y = self._data()
        model, metrics = H.train(X, y)
        self.assertEqual(metrics["n_samples"], len(y))
        self.assertIn("cv", metrics)
        self.assertGreaterEqual(metrics["cv"]["cv_f1_macro"], 0.6)
        self.assertEqual(metrics["feature_dim"], 16)

    def test_single_class_rejected(self):
        import numpy as np
        with self.assertRaises(ValueError):
            H.train(np.zeros((10, 4)), np.ones(10))

    def test_predict_proba_shape(self):
        X, y = self._data()
        model, _ = H.train(X, y)
        p = H.predict_proba(model, X[:3])
        self.assertEqual(p.shape, (3,))
        self.assertTrue(((p >= 0) & (p <= 1)).all())


class PublishFlowTest(unittest.TestCase):
    """整条链路：样本 → 向量 → 概率 → 聚合 → 写入。外部依赖全部打桩。"""

    def test_main_writes_document_with_model_metadata(self):
        written = {}

        class FakeModel:
            def predict_proba(self, X):
                import numpy as np
                p = np.linspace(0.1, 0.9, len(X))
                return np.column_stack([1 - p, p])

        samples = "\n".join([
            json.dumps({"appid": 1, "id": "r%d" % i, "created": "2026-11-16T10:00:00+00:00",
                        "text": "text %d" % i, "voted_up": True, "lang": "en"})
            for i in range(5)
        ])

        saved = (SP.read_text, SP.P.get_remote, SP.P.put_remote,
                 SP.E.build_embedder, SP.H.load, SP.H.DEFAULT_MODEL, SP.H.METRICS_PATH)
        saved_token = os.environ.get("GITHUB_TOKEN")
        SP.read_text = lambda *a: (samples, "sha1")
        SP.P.get_remote = lambda *a: (None, None)
        SP.P.put_remote = lambda path, doc, sha, repo, token, msg: written.update({path: doc})
        SP.E.build_embedder = lambda kind="st": E.HashEmbedder(dim=8)
        SP.H.load = lambda *a: FakeModel()
        SP.H.DEFAULT_MODEL = __file__          # 存在即可，避免真实模型依赖

        import tempfile
        metrics_file = os.path.join(tempfile.mkdtemp(), "m.json")
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump({"embedding_model": "fake", "model_version": "v1",
                       "trained_at": "2026-11-16T00:00:00Z", "n_samples": 100,
                       "label_definition": "voted_up", "cv": {"cv_f1_macro": 0.77}}, f)
        saved_metrics = SP.H.METRICS_PATH
        SP.H.METRICS_PATH = metrics_file
        os.environ["GITHUB_TOKEN"] = "dummy"
        os.environ["GITHUB_REPOSITORY"] = "test/repo"
        SP.__dict__["print"] = lambda *a, **k: None
        try:
            SP.main()
        finally:
            (SP.read_text, SP.P.get_remote, SP.P.put_remote, SP.E.build_embedder,
             SP.H.load, SP.H.DEFAULT_MODEL, SP.H.METRICS_PATH) = saved
            SP.H.METRICS_PATH = saved_metrics
            SP.__dict__.pop("print", None)
            if saved_token is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = saved_token

        doc = written[SP.OUT]
        self.assertEqual(doc["sample_size"], 5)
        self.assertIn("model", doc)
        self.assertEqual(doc["model"]["head_cv_f1_macro"], 0.77)
        self.assertEqual(doc["model"]["neutral_band"], [H.NEUTRAL_LOW, H.NEUTRAL_HIGH])
        self.assertEqual(len(doc["games"]), 1)
        self.assertIn("n", doc["games"][0]["totals"])


class WorkflowTest(unittest.TestCase):
    def test_workflow_uses_cpu_torch_and_existing_script(self):
        root = os.path.dirname(BACKEND)
        wf = os.path.join(root, ".github", "workflows", "analyze-sentiment.yml")
        self.assertTrue(os.path.exists(wf))
        with open(wf, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("download.pytorch.org/whl/cpu", text)   # 不能装成 CUDA 版
        self.assertIn("backend/collectors/sentiment_publish.py", text)
        self.assertIn("contents: write", text)
        self.assertIn("huggingface", text)                    # 有模型缓存，否则每次都重下 470MB

    def test_model_artifact_is_committed(self):
        """分类头必须进仓库：CI 与本地要用同一份权重。"""
        self.assertTrue(os.path.exists(H.DEFAULT_MODEL),
                        "缺少 %s —— 先运行 backend/ml/train_head.py" % H.DEFAULT_MODEL)
        self.assertTrue(os.path.exists(H.METRICS_PATH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
