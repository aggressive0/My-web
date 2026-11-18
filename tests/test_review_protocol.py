"""
评论采集协议回归测试（2026-11-18 修复的防线）。

背景：采集器曾经请求新接口却用旧接口的扁平 query，服务端静默忽略全部参数、
回落成「英语 + 每页 5 条」。这个 bug 在两个地方造成了错误结论：

  1. 代码注释里写下了「language 参数被忽略」——实际是字段名应为 languages；
  2. 我据此向用户报告「该通道不支持语言过滤」——实际只是格式错了。

这类「静默回落」不会被异常捕获，只能靠断言请求结构来防。所以这里不测网络，
只测**发出去的 URL 长什么样**。
"""

import json
import os
import sys
import unittest
import urllib.parse

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, os.path.join(BACKEND, "collectors"))

import steam_reviews_collector as R  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _CaptureOpener:
    """记录请求 URL，返回固定响应。"""

    def __init__(self, payload=None, fail=False):
        self.urls = []
        self.fail = fail
        self.payload = payload or {"response": {"reviews": [{"language": "schinese"}], "cursor": "*"}}

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.urls.append(url)
        if self.fail:
            raise OSError("endpoint down")
        return _Resp(self.payload)


class _Ctx(unittest.TestCase):
    def run_with(self, opener, **kwargs):
        orig_open, orig_sleep = R.OPENER, R.time.sleep
        R.OPENER, R.time.sleep = opener, lambda *_: None
        try:
            return R.fetch_page(730, retries=0, **kwargs)
        finally:
            R.OPENER, R.time.sleep = orig_open, orig_sleep

    @staticmethod
    def input_json_of(url):
        q = urllib.parse.urlparse(url).query
        raw = urllib.parse.parse_qs(q)["input_json"][0]
        return json.loads(raw)


class RequestShapeTest(_Ctx):
    def test_uses_input_json_not_flat_query(self):
        op = _CaptureOpener()
        self.run_with(op)
        url = op.urls[0]
        self.assertIn("input_json=", url)
        # 扁平参数一个都不该出现在 query 里
        for banned in ("&language=", "&num_per_page=", "&appid=", "&filter="):
            self.assertNotIn(banned, url)

    def test_languages_is_array_all_by_default(self):
        op = _CaptureOpener()
        self.run_with(op)
        payload = self.input_json_of(op.urls[0])
        self.assertEqual(payload["languages"], ["all"])
        self.assertIsInstance(payload["languages"], list)

    def test_specific_language_is_passed_as_single_item_array(self):
        op = _CaptureOpener()
        self.run_with(op, language="schinese")
        self.assertEqual(self.input_json_of(op.urls[0])["languages"], ["schinese"])

    def test_filter_maps_to_integer_enum(self):
        cases = {"recent": 1, "updated": 2, "all": 0}
        for name, expected in cases.items():
            op = _CaptureOpener()
            self.run_with(op, filter_=name)
            self.assertEqual(self.input_json_of(op.urls[0])["filter"], expected, name)

    def test_review_and_purchase_type_are_integers(self):
        op = _CaptureOpener()
        self.run_with(op)
        payload = self.input_json_of(op.urls[0])
        self.assertEqual(payload["review_type"], 0)
        self.assertEqual(payload["purchase_type"], 1)

    def test_cursor_is_forwarded(self):
        op = _CaptureOpener()
        self.run_with(op, cursor="AoIIP4AAQJq")
        self.assertEqual(self.input_json_of(op.urls[0])["cursor"], "AoIIP4AAQJq")


class ResponseMarkerTest(_Ctx):
    def test_new_endpoint_marks_source(self):
        resp = self.run_with(_CaptureOpener())
        self.assertEqual(resp.get("_endpoint"), "input_json")
        self.assertEqual(resp["reviews"][0]["language"], "schinese")

    def test_legacy_fallback_marks_source(self):
        """新接口挂掉时降级到旧端点，必须能识别出这是降级数据。"""

        class NewDownLegacyOk(_CaptureOpener):
            def open(self, req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else str(req)
                self.urls.append(url)
                if "IUserReviewsService" in url:
                    raise OSError("new endpoint down")
                return _Resp({"success": 1,
                              "reviews": [{"language": "english"}], "cursor": "*"})

        op = NewDownLegacyOk()
        resp = self.run_with(op)
        self.assertEqual(resp.get("_endpoint"), "legacy")


class LanguagePreservationTest(unittest.TestCase):
    def test_parse_review_keeps_language_verbatim(self):
        for lang in ("schinese", "russian", "english", "brazilian", None):
            row = R.parse_review({"recommendationid": "1", "language": lang})
            self.assertEqual(row["language"], lang)


if __name__ == "__main__":
    unittest.main(verbosity=2)
