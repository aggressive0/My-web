"""
召回策略路由（Phase 6.5）。

三个接口（对齐 writing-block §11.6–11.8）：
    POST /api/recall/generate      生成并保存
    GET  /api/recall/history       历史列表
    GET  /api/recall/<strategy_id> 详情

失败不落库：只在响应里返回 reason/detail，历史列表里不会出现空策略。
"""

import sqlite3

from flask import Blueprint, current_app, jsonify, request

from ai.config import DEFAULT_KNOWLEDGE_DB, AIConfig
from ai.llm.provider_factory import create_llm_provider
from ai.rag.service import KnowledgeError, get_service as get_rag_service
from ai.recall.factory import build_generator
from ai.recall.store import RecallStrategyStore

bp = Blueprint("recall", __name__, url_prefix="/api/recall")

_STORE = None


def _db_path():
    import os
    return os.environ.get("KNOWLEDGE_DB") or os.environ.get("STEAM_DB") or DEFAULT_KNOWLEDGE_DB


def _store():
    global _STORE
    if _STORE is None:
        _STORE = RecallStrategyStore(_db_path())
        _STORE.init_schema()
    return _STORE


def _error(code, message, status=400):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _as_int(value, field, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是整数" % field)


def _game_name(app_id):
    try:
        conn = sqlite3.connect(_db_path(), timeout=10)
        row = conn.execute("SELECT name FROM games WHERE appid = ?", (app_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


@bp.route("/generate", methods=["POST"])
def generate():
    body = request.get_json(silent=True) or {}
    try:
        app_id = _as_int(body.get("app_id"), "app_id")
        if app_id is None or app_id <= 0:
            return _error("INVALID_APP_ID", "app_id 必须是正整数")
        top_k = min(_as_int(body.get("top_k"), "top_k", 5) or 5, 20)

        cfg = AIConfig()
        if not cfg.enabled:
            return _error("AI_DISABLED", "AI 未启用：在后端环境变量设置 AI_ENABLED=true", 409)
        provider = create_llm_provider(cfg)

        # 与 /api/knowledge/answer 共用同一个装配入口，避免两边参数分叉
        generator = build_generator(provider, get_rag_service(), cfg)
        result = generator.generate(app_id, game_name=_game_name(app_id),
                                    top_k=top_k, persist=True)
    except KnowledgeError as exc:
        return _error(exc.code, str(exc), 503)
    except ValueError as exc:
        return _error("INVALID_INPUT", str(exc))
    except Exception as exc:
        current_app.logger.exception("recall generate 异常：%r", exc)
        return _error("RECALL_ERROR", "策略生成失败，详见服务端日志", 500)

    status_code = 200 if result.get("status") == "generated" else 422
    return jsonify({
        "success": result.get("status") == "generated",
        "data": result,
        "meta": {
            "api_version": "v1",
            "phase": "6.5",
            "note": "data_metrics 来自本地 SQLite；strategy 为模型生成且经数值与引用双校验",
        },
    }), status_code


@bp.route("/history", methods=["GET"])
def history():
    try:
        app_id = _as_int(request.args.get("app_id"), "app_id")
        limit = min(_as_int(request.args.get("limit"), "limit", 20) or 20, 100)
        offset = max(_as_int(request.args.get("offset"), "offset", 0) or 0, 0)
        data = _store().list(app_id=app_id, limit=limit, offset=offset)
    except ValueError as exc:
        return _error("INVALID_INPUT", str(exc))
    except Exception as exc:
        current_app.logger.exception("recall history 异常：%r", exc)
        return _error("RECALL_ERROR", "读取历史失败，详见服务端日志", 500)

    return jsonify({"success": True, "data": data,
                    "meta": {"api_version": "v1", "phase": "6.5"}}), 200


@bp.route("/<strategy_id>", methods=["GET"])
def detail(strategy_id):
    try:
        record = _store().get(strategy_id)
    except Exception as exc:
        current_app.logger.exception("recall detail 异常：%r", exc)
        return _error("RECALL_ERROR", "读取策略失败，详见服务端日志", 500)
    if not record:
        return _error("STRATEGY_NOT_FOUND", "策略不存在：%s" % strategy_id, 404)
    return jsonify({"success": True, "data": record,
                    "meta": {"api_version": "v1", "phase": "6.5"}}), 200
