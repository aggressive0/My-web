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

import json

from flask import Blueprint, jsonify, request

from ai.config import load_config
from ai.llm.base import LLMError
from ai.llm.provider_factory import create_llm_provider

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

PING_SYSTEM = "你是一名游戏运营分析师。只输出 JSON，不要输出任何解释文字。"
PING_USER = ('请只输出一个 JSON 对象：{"ok": true, "role": "analyst"}。不要输出别的。')


def _error(code, message, status=503):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _component(name, available, detail=None, **extra):
    data = {"name": name, "available": bool(available), "detail": detail}
    data.update(extra)
    return data


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
                             model=h.get("model"), installed=h.get("installed"))
        except Exception as e:                       # 工厂自身出错也如实报告
            llm = _component("llm", False, str(e)[:200], provider=cfg.provider)

    return jsonify({
        "success": True,
        "data": {
            "llm": llm,
            # Phase 6.1 只做模型接入，这两项还没实现——如实标 unavailable，
            # 不写「available: true」占位（AGENTS.md §4.3）
            "embedding": _component("embedding", False, "Phase 6.2 未实现"),
            "vector_db": _component("vector_db", False, "Phase 6.2 未实现"),
            "config_problems": problems,
            "public_config": cfg.public_view(),
        },
        "meta": {
            "api_version": "v1",
            "phase": "6.1",
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

    try:
        provider = create_llm_provider(cfg)
        text = provider.generate(prompt, system_prompt=system)
    except LLMError as e:
        # 503 服务不可用 / 504 超时 / 422 输出不合法
        status = {"LLM_TIMEOUT": 504, "MODEL_NOT_FOUND": 404,
                  "INVALID_OUTPUT": 422}.get(e.code, 503)
        return _error(e.code, e.message, status)
    except Exception as e:
        return _error("LLM_ERROR", "模型调用失败：%s" % str(e)[:120], 503)

    parsed, parse_error = None, None
    try:
        parsed = json.loads(text)
    except ValueError as e:
        parse_error = str(e)[:120]

    return jsonify({
        "success": True,
        "data": {
            "provider": provider.name,
            "model": cfg.public_view()["model"],
            "chars": len(text),
            "duration_ms": provider.last_duration_ms,
            "json_valid": parsed is not None,
            "json_parse_error": parse_error,
            # 只回预览，不把整段文本塞进响应
            "preview": text[:280],
        },
        "meta": {
            "api_version": "v1",
            "note": "本接口只验证模型链路；不做策略生成，不写入任何策略记录",
        },
    }), 200
