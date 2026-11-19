"""
把 tests/fixtures/rag_corpus 里的真实公告批量导入知识库（Phase 6.5 前置）。

为什么不直接操作 Chroma：导入走 HTTP 接口，和线上路径完全一致。
另开进程直连 Chroma 会在 Flask 持有客户端时争用同一份 SQLite，
省下的 HTTP 开销不值得冒这个风险。

用法：
    python scripts/import_knowledge_corpus.py --clean-fixtures
    python scripts/import_knowledge_corpus.py --app-id 1203220
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(ROOT, "tests", "fixtures", "rag_corpus")
BASE = os.environ.get("KNOWLEDGE_API", "http://127.0.0.1:5000")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 早期手工建的两份测试文档：标题前缀固定，用来精确清理，不用模糊匹配
FIXTURE_TITLE_PREFIX = "永劫无间 1."
FIXTURE_TITLES = {"维护公告（临时）"}


def call(method, path, payload=None, timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def list_documents():
    status, body = call("GET", "/api/knowledge/documents?limit=500")
    if status != 200:
        raise SystemExit("列举文档失败：%s %s" % (status, body))
    return body["data"]["items"]


def clean_fixtures():
    """删除早期手工测试文档。只删标题精确匹配的，不做「像测试就删」。"""
    removed = []
    for item in list_documents():
        title = item.get("title") or ""
        if title.startswith(FIXTURE_TITLE_PREFIX) or title in FIXTURE_TITLES:
            status, body = call("DELETE", "/api/knowledge/documents/" + item["document_id"])
            if status == 200:
                removed.append((item["title"], body["data"].get("deleted_chunks")))
            else:
                print("  删除失败 %s：%s" % (item["document_id"], body))
    return removed


def load_corpora(app_id=None):
    """一次性读入并按 app_id 过滤，避免进度打印时反复读文件。"""
    corpora = []
    for name in sorted(f for f in os.listdir(CORPUS_DIR) if f.endswith(".json")):
        with open(os.path.join(CORPUS_DIR, name), encoding="utf-8") as f:
            corpus = json.load(f)
        if app_id is None or corpus["appid"] == app_id:
            corpora.append(corpus)
    return corpora


def import_corpus(app_id=None):
    corpora = load_corpora(app_id)
    expected = sum(len(c["items"]) for c in corpora)
    total_docs = created = deduped = failed = 0
    total_chunks = 0
    started = time.time()

    for corpus in corpora:
        for index, item in enumerate(corpus["items"], start=1):
            total_docs += 1
            payload = {
                "app_id": corpus["appid"],
                "title": item["title"],
                "content": item["contents"],
                "source_type": "announcement",
                "source_name": "Steam 官方公告",
                "source_url": item.get("url"),
                "publish_date": (item.get("date_utc") or "")[:10],
                "tags": "steam_announcement",
            }
            status, body = call("POST", "/api/knowledge/import", payload)
            if status not in (200, 201):
                failed += 1
                print("  [失败] app=%s %s -> %s %s"
                      % (corpus["appid"], item["title"][:30], status,
                         (body.get("error") or {}).get("code")))
                continue
            data = body["data"]
            if data.get("created"):
                created += 1
                total_chunks += data.get("chunk_count") or 0
            else:
                deduped += 1
            if total_docs % 10 == 0:
                print("  进度 %d/%d，已建 chunk %d，用时 %.0fs"
                      % (total_docs, expected, total_chunks, time.time() - started))

    return {"documents": total_docs, "created": created, "deduped": deduped,
            "failed": failed, "chunks": total_chunks,
            "elapsed_s": round(time.time() - started, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", type=int, default=None, help="只导入某款游戏")
    parser.add_argument("--clean-fixtures", action="store_true",
                        help="先删除早期手工测试文档")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    if args.dry_run:
        files = sorted(f for f in os.listdir(CORPUS_DIR) if f.endswith(".json"))
        total = sum(len(json.load(open(os.path.join(CORPUS_DIR, n), encoding="utf-8"))["items"])
                    for n in files)
        print("待导入 %d 条公告（%d 个语料文件）" % (total, len(files)))
        return 0

    if args.clean_fixtures:
        removed = clean_fixtures()
        print("清理测试文档 %d 份：%s" % (len(removed), removed))

    result = import_corpus(args.app_id)
    print("\n导入结果：%s" % json.dumps(result, ensure_ascii=False))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
