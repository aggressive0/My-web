"""
AI 路由（Phase 6.1）：健康检查 + 最小模型调用测试。

两个接口：
  GET  /api/ai/health     各组件状态（llm / embedding / vector_db），逐项如实报告
  POST /api/ai/llm/ping   发一次最小生成请求，验证「Flask → Provider → 模型」链路

为什么单独给一个 ping：健康检查只说「服务在线」，不代表能生成。
Phase 6.1 的验收是「能通过 Flask 调用本地 Qwen3」，必须有一步真的产生文本。

错误格式与方案 §11.7 一致：{"success": false, "error": {"code", "message"}}。
不返回堆栈、不返回内部地址。
"""

from flask import Blueprint, current_app, jsonify, request

from ai.call_log import get_repository as get_call_log
from ai.config import load_config
from ai.llm.base import LLMError, LLMTruncated
from ai.llm.provider_factory import create_llm_provider
from ai.recall.validation import parse_output

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

PING_SYSTEM = "你是一名游戏运营分析师。只输出 JSON，不要输出任何解释文字。"
PING_USER = ('请只输出一个 JSON 对象：{"ok": true, "role": "analyst"}。不要输出别的。')


def _error(code, message, status=503):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _component(name, available, detail=None, **extra):
    data = {"name": name, "available": bool(available), "detail": detail}
    data.update(extra)
    return data


def _record_call(cfg, ok, metrics=None, wall_ms=None, error_code=None,
                 truncated=False, purpose="ping"):
    """写一条调用日志。绝不因为日志失败而让请求失败——日志是辅助，不是主链路。"""
    try:
        get_call_log().record(
            purpose=purpose, provider=cfg.provider,
            model=cfg.public_view().get("model"), ok=ok, metrics=metrics or {},
            wall_ms=wall_ms, error_code=error_code, truncated=truncated,
            thinking_enabled=cfg.enable_thinking)
    except Exception as exc:                     # 表不存在、磁盘满、权限……
        current_app.logger.warning("写 llm_call_logs 失败：%r", exc)


@bp.route("/health", methods=["GET"])
def health():
    cfg = load_config()
    problems = cfg.validate()

    # LLM
    if not cfg.enabled:
        llm = _component("llm", False,
                         "AI_ENABLED=false（在后端 .env 中开启后才调用模型）",
                         provider=cfg.provider, model=cfg.public_view()["model"])
    else:
        try:
            provider = create_llm_provider(cfg)
            h = provider.health()
            llm = _component("llm", h.get("available", False),
                             h.get("detail"), provider=cfg.provider,
                             model=h.get("model"), installed=h.get("installed"),
                             # 已安装 ≠ 已驻留；预热是否生效全看这个字段
                             loaded=h.get("loaded"))
        except Exception as e:                       # 工厂自身出错也如实报告
            # 原始异常只进日志：它可能带内部地址或路径（AGENTS.md §8.1）
            current_app.logger.warning("ai health provider 初始化失败：%r", e)
            llm = _component("llm", False, "provider 初始化失败（%s）" % type(e).__name__,
                             provider=cfg.provider)

    # 检索组件（Phase 6.2）：只报告真实状态。embedding 的 available 语义是
    # 「现在就能用」——模型没缓存时为 false，避免首次调用静默下载 2.1GB。
    try:
        from ai.rag.service import get_service as get_rag_service
        rag = get_rag_service().health()
        emb = rag.get("embedding") or {}
        vdb = rag.get("vector_db") or {}
        embedding = _component("embedding", emb.get("available"),
                               emb.get("detail"), model=emb.get("model"),
                               device=emb.get("device"), loaded=emb.get("loaded"),
                               dim=emb.get("dim"), cached=emb.get("cached"))
        vector_db = _component("vector_db", vdb.get("available"), vdb.get("detail"),
                               store=vdb.get("store"), collection=vdb.get("collection"),
                               count=vdb.get("count"))
    except Exception as e:                       # 依赖缺失等，如实降级
        current_app.logger.warning("检索组件健康检查失败：%r", e)
        embedding = _component("embedding", False,
                               "组件初始化失败（%s）" % type(e).__name__)
        vector_db = _component("vector_db", False,
                               "组件初始化失败（%s）" % type(e).__name__)

    return jsonify({
        "success": True,
        "data": {
            "llm": llm,
            "embedding": embedding,
            "vector_db": vector_db,
            "config_problems": problems,
            # 用了旧变量名不算错误，但必须让运维看见，否则「配了没生效」会查很久
            "config_warnings": list(getattr(cfg, "deprecations", [])),
            "public_config": cfg.public_view(),
        },
        "meta": {
            "api_version": "v1",
            "phase": "6.2",
            "note": "各组件状态独立上报；不可用不会被伪装成成功",
        },
    }), 200


@bp.route("/llm/ping", methods=["POST"])
def llm_ping():
    cfg = load_config()
    if not cfg.enabled:
        return _error("AI_DISABLED",
                      "AI 未启用：在后端环境变量设置 AI_ENABLED=true", 409)

    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or PING_USER)[:2000]
    system = body.get("system_prompt") or PING_SYSTEM

    provider = None
    try:
        provider = create_llm_provider(cfg)
        text = provider.generate(prompt, system_prompt=system)
    except LLMError as e:
        # message 是可公开文案（不含地址/端口）；detail 只进服务端日志，
        # 否则「修掉泄露」就变成「丢掉诊断信息」。
        current_app.logger.warning("llm ping 失败 code=%s detail=%s", e.code, e.detail)
        _record_call(cfg, ok=False,
                     metrics=getattr(provider, "last_metrics", {}) or {},
                     wall_ms=getattr(provider, "last_duration_ms", None),
                     error_code=e.code,
                     # 截断可能由 Provider 直接抛（空内容），也可能由解析层抛，
                     # 两条路径都要打标，否则统计里的截断率永远偏低
                     truncated=(e.code == "LLM_TRUNCATED"))
        # 503 服务不可用 / 504 超时 / 404 模型缺失 / 422 输出侧问题
        # 输出侧（截断、非法 JSON、Schema 不符、app_id 不符、证据非法）统一 422：
        # 它们都是「模型答得不对」，不是「服务不可用」，混成 503 会误导排查方向。
        status = {"LLM_TIMEOUT": 504, "MODEL_NOT_FOUND": 404,
                  "INVALID_OUTPUT": 422, "LLM_TRUNCATED": 422,
                  "INVALID_JSON": 422, "SCHEMA_MISMATCH": 422,
                  "APP_ID_MISMATCH": 422, "EVIDENCE_INVALID": 422}.get(e.code, 503)
        return _error(e.code, e.message, status)
    except Exception as e:
        current_app.logger.exception("llm ping 异常：%r", e)
        _record_call(cfg, ok=False, error_code="LLM_ERROR")
        return _error("LLM_ERROR", "模型调用失败，详见服务端日志", 503)

    metrics = dict(provider.last_metrics or {})
    parsed, parse_error, error_code = None, None, None
    try:
        parsed = parse_output(text, done_reason=metrics.get("done_reason"))
    except LLMTruncated as e:
        # 截断是「本次调用失败」，不是「模型不能生成 JSON」，用 422 明确区分
        current_app.logger.warning("llm ping 截断：%s | %s", e.message, e.detail)
        _record_call(cfg, ok=False, metrics=metrics,
                     wall_ms=provider.last_duration_ms, error_code=e.code,
                     truncated=True)
        return _error(e.code, e.message, 422)
    except LLMError as e:
        parse_error = e.message
        error_code = e.code

    # ok 的定义是「调用成功且输出可用」：JSON 解析不出来就不算成功，
    # 否则统计里的成功率会把垃圾输出也算成健康。
    _record_call(cfg, ok=parsed is not None, metrics=metrics,
                 wall_ms=provider.last_duration_ms, error_code=error_code,
                 truncated=metrics.get("done_reason") == "length")

    return jsonify({
        "success": True,
        "data": {
            "provider": provider.name,
            "model": cfg.public_view()["model"],
            "chars": len(text),
            "duration_ms": provider.last_duration_ms,
            "json_valid": parsed is not None,
            "json_parse_error": parse_error,
            "json_error_code": error_code,
            "thinking_enabled": cfg.enable_thinking,
            # 截断但解析成功：结果可用，但不该与干净结果等价对待，必须让调用方看见
            "truncated_but_valid": metrics.get("done_reason") == "length",
            "done_reason": metrics.get("done_reason"),
            "metrics": metrics,
            # 只回预览，不把整段文本塞进响应
            "preview": text[:280],
        },
        "meta": {
            "api_version": "v1",
            "note": "本接口只验证模型链路；不做策略生成，不写入任何策略记录",
        },
    }), 200


@bp.route("/metrics", methods=["GET"])
def metrics():
    """LLM 调用统计（Phase 6.2 Task 7）。

    只返回调用元数据：耗时、token、done_reason、错误码。
    不含 prompt 正文（可能含业务数据）与任何密钥。
    """
    try:
        days = int(request.args.get("days", 30))
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        return _error("INVALID_PARAM", "days 与 limit 必须是整数", 400)
    if not (1 <= days <= 365):
        return _error("INVALID_PARAM", "days 必须在 1–365 之间", 400)
    limit = max(1, min(limit, 100))

    try:
        repository = get_call_log()
        data = {"stats": repository.stats(days=days),
                "recent": repository.recent(limit=limit)}
    except Exception as exc:
        current_app.logger.exception("读取 llm_call_logs 失败：%r", exc)
        return _error("LOG_UNAVAILABLE", "调用日志不可用，详见服务端日志", 503)

    return jsonify({
        "success": True,
        "data": data,
        "meta": {
            "api_version": "v1",
            "phase": "6.2",
            "note": "只含调用元数据，不含 prompt 正文与任何密钥",
        },
    }), 200
