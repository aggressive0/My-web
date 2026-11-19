"""
知识库路由（Phase 6.2）。

四个接口：
    POST   /api/knowledge/import              导入（multipart 文件或 JSON 正文）
    GET    /api/knowledge/documents           列表（按 app_id / 类型 / 状态分页）
    GET    /api/knowledge/documents/<id>      详情（含原文）
    DELETE /api/knowledge/documents/<id>      删除（元数据 + 向量）
    POST   /api/knowledge/search              检索测试

错误结构沿用 ai_routes：{"success": false, "error": {"code", "message"}}。
不返回堆栈、不返回内部路径；细节进 current_app.logger。
"""

from flask import Blueprint, current_app, jsonify, request

from ai.rag.answer_service import AnswerService
from ai.rag.document_loader import UnsupportedFormat
from ai.rag.service import KnowledgeError, get_service

bp = Blueprint("knowledge", __name__, url_prefix="/api/knowledge")

_UNAVAILABLE_CODES = {"EMBEDDING_UNAVAILABLE", "VECTOR_STORE_UNAVAILABLE",
                      "EMBEDDING_MISMATCH"}
_MAX_PAGE = 100


def _error(code, message, status=400):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _meta(app_id=None, note=None):
    meta = {
        "api_version": "v1",
        "phase": "6.2",
        "source": "local_knowledge_base",
        "data_type": "real",
        "timezone": "UTC",
    }
    if app_id is not None:
        meta["app_id"] = app_id
    if note:
        meta["note"] = note
    return meta


def _as_int(value, field, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise KnowledgeError("%s 必须是整数" % field)


def _parse_tags(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


@bp.route("/import", methods=["POST"])
def import_document():
    try:
        service = get_service()
        upload = request.files.get("file")
        if upload is not None:
            form = request.form
            app_id = _as_int(form.get("app_id"), "app_id")
            result = service.import_bytes(
                filename=upload.filename or "upload",
                data=upload.read(),
                app_id=app_id,
                title=form.get("title") or None,
                source_type=form.get("source_type") or "doc",
                scope=form.get("scope") or "game_specific",
                source_name=form.get("source_name"),
                source_url=form.get("source_url"),
                publish_date=form.get("publish_date"),
                tags=_parse_tags(form.get("tags")),
            )
        else:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return _error("INVALID_BODY", "需要 multipart 文件或 JSON 请求体")
            if not (body.get("content") or "").strip():
                return _error("INVALID_BODY", "content 不能为空")
            app_id = _as_int(body.get("app_id"), "app_id")
            result = service.import_document(
                app_id=app_id,
                title=body.get("title") or "",
                content=body.get("content") or "",
                source_type=body.get("source_type") or "doc",
                scope=body.get("scope") or "game_specific",
                source_name=body.get("source_name"),
                source_url=body.get("source_url"),
                publish_date=body.get("publish_date"),
                tags=_parse_tags(body.get("tags")),
            )
    except UnsupportedFormat as e:
        return _error("UNSUPPORTED_FORMAT", str(e))
    except KnowledgeError as e:
        current_app.logger.warning("knowledge import 失败 code=%s %s", e.code, e)
        status = 503 if e.code in _UNAVAILABLE_CODES else 400
        return _error(e.code, str(e), status)
    except ValueError as e:                      # 解码失败、空文件等
        return _error("INVALID_INPUT", str(e))
    except Exception as e:
        current_app.logger.exception("knowledge import 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "导入失败，详见服务端日志", 500)

    return jsonify({
        "success": True,
        "data": result,
        "meta": _meta(app_id=result.get("document_id") and None,
                      note="created=false 表示内容哈希未变，未重新生成向量"),
    }), (201 if result.get("created") else 200)


@bp.route("/documents", methods=["GET"])
def list_documents():
    try:
        app_id = _as_int(request.args.get("app_id"), "app_id")
        limit = min(_as_int(request.args.get("limit"), "limit", 20) or 20, _MAX_PAGE)
        offset = max(_as_int(request.args.get("offset"), "offset", 0) or 0, 0)
        data = get_service().list_documents(
            app_id=app_id,
            status=request.args.get("status"),
            source_type=request.args.get("source_type"),
            limit=limit, offset=offset)
    except KnowledgeError as e:
        return _error(e.code, str(e))
    except Exception as e:
        current_app.logger.exception("knowledge list 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "查询失败，详见服务端日志", 500)
    return jsonify({"success": True, "data": data, "meta": _meta(app_id=app_id)}), 200


@bp.route("/documents/<document_id>", methods=["GET"])
def get_document(document_id):
    try:
        data = get_service().get_document(document_id, with_content=True)
    except KnowledgeError as e:
        return _error(e.code, str(e), 404 if e.code == "DOCUMENT_NOT_FOUND" else 400)
    except Exception as e:
        current_app.logger.exception("knowledge detail 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "查询失败，详见服务端日志", 500)
    return jsonify({"success": True, "data": data,
                    "meta": _meta(app_id=data.get("app_id"))}), 200


@bp.route("/documents/<document_id>", methods=["DELETE"])
def delete_document(document_id):
    try:
        data = get_service().delete_document(document_id)
    except KnowledgeError as e:
        status = 503 if e.code in _UNAVAILABLE_CODES else (
            404 if e.code == "DOCUMENT_NOT_FOUND" else 400)
        return _error(e.code, str(e), status)
    except Exception as e:
        current_app.logger.exception("knowledge delete 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "删除失败，详见服务端日志", 500)
    return jsonify({"success": True, "data": data, "meta": _meta()}), 200


@bp.route("/search", methods=["POST"])
def search():
    body = request.get_json(silent=True) or {}
    try:
        app_id = _as_int(body.get("app_id"), "app_id")
        if app_id is None or app_id <= 0:
            return _error("INVALID_APP_ID", "app_id 必须是正整数（跨游戏隔离的前提）")
        query = (body.get("query") or "").strip()
        if not query:
            return _error("INVALID_QUERY", "query 不能为空")
        top_k = min(_as_int(body.get("top_k"), "top_k", 5) or 5, 50)
        result = get_service().search(app_id, query, top_k=top_k)
    except KnowledgeError as e:
        status = 503 if e.code in _UNAVAILABLE_CODES else 400
        return _error(e.code, str(e), status)
    except ValueError as e:
        return _error("INVALID_INPUT", str(e))
    except Exception as e:
        current_app.logger.exception("knowledge search 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "检索失败，详见服务端日志", 500)

    return jsonify({
        "success": True,
        "data": result,
        "meta": _meta(app_id=app_id,
                      note="score = 1 - cosine_distance（derived）；chunk 原文来自本地知识库（real）"),
    }), 200


@bp.route("/answer", methods=["POST"])
def answer():
    """问答（Phase 6.3 P1）：路由 → 检索 → 证据门 → 生成 → 引用校验。

    与 /search 的分工：/search 只回答「检索到了什么」，本接口回答「能不能答、答什么」。
    状态见 ai/rag/answer_service.py 的模块注释。
    """
    body = request.get_json(silent=True) or {}
    try:
        app_id = _as_int(body.get("app_id"), "app_id")
        if app_id is None or app_id <= 0:
            return _error("INVALID_APP_ID", "app_id 必须是正整数（跨游戏隔离的前提）")
        question = (body.get("query") or body.get("question") or "").strip()
        if not question:
            return _error("INVALID_QUERY", "query 不能为空")
        provider = None
        checker = None
        cfg = None            # 必须在 try 之外初始化：初始化失败时下面还要判断它
        try:
            from ai.config import AIConfig
            from ai.llm.provider_factory import create_llm_provider
            cfg = AIConfig()
            if cfg.enabled:
                provider = create_llm_provider(cfg)
                # 第二层复核与生成共用一个 provider；不可用时 checker 为 None，
                # evidence.layer 会如实标成 program，而不是假装复核过
                from ai.rag.semantic_check import SemanticChecker
                checker = SemanticChecker(provider)
        except Exception as exc:                 # 生成不可用不该拖垮路由与拒答
            current_app.logger.warning("answer 的 LLM 初始化失败：%r", exc)

        # 召回深度默认跟随配置（RAG_TOP_K），**不在路由里写死 10**：
        # 写死过一次，结果是「配置改成 50，接口仍然只给 10 条」，配置形同虚设。
        default_top_k = cfg.rag.top_k if cfg is not None else 10
        top_k = min(_as_int(body.get("top_k"), "top_k", default_top_k) or default_top_k, 100)

        # 策略分支与 /api/recall/generate 共用同一个装配入口（参数不会分叉）
        strategy = None
        if cfg is not None:
            from ai.recall.factory import build_generator
            strategy = build_generator(provider, get_service(), cfg)

        result = AnswerService(
            get_service(), provider=provider, checker=checker, strategy=strategy,
            max_evidence=cfg.max_evidence if cfg is not None else 5).answer(
            app_id, question, top_k=top_k)
    except KnowledgeError as e:
        status = 503 if e.code in _UNAVAILABLE_CODES else 400
        return _error(e.code, str(e), status)
    except ValueError as e:
        return _error("INVALID_INPUT", str(e))
    except Exception as e:
        current_app.logger.exception("knowledge answer 异常：%r", e)
        return _error("KNOWLEDGE_ERROR", "问答失败，详见服务端日志", 500)

    return jsonify({
        "success": True,
        "data": result,
        "meta": _meta(app_id=app_id,
                      note="status 表示证据与路由此刻的真实结论；answer 为 null 时不要补写"),
    }), 200
