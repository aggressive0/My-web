"""LLM 预热脚本（Phase 6.2 Task 5）。

为什么预热要独立成脚本，而不是在 Flask 启动时同步做：
模型加载要 2–4 秒（首次部署、磁盘冷读时更久）。把它塞进 Flask 启动路径，
等于让「web 服务能不能起来」取决于「模型能不能加载」——模型服务挂了连
健康检查都打不开，排查入口先没了。

为什么脚本自己 load_dotenv：
预检发现 `load_dotenv()` 只写在 `backend/app.py` 里。独立脚本不加载 .env
就会拿到代码默认值（比如 timeout=120 而不是 .env 的 300），预热用的配置
和线上不一致，等于没预热。

用法：
    python scripts/warmup_llm.py             # 预热，模型未加载则加载
    python scripts/warmup_llm.py --status    # 只查状态，不发请求
    python scripts/warmup_llm.py --unload    # 主动卸载，用于验证冷启动
    python scripts/warmup_llm.py --json      # 输出机器可读结果

退出码：0 = 就绪 / 1 = 失败（便于 CI 或启动脚本判断）
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from dotenv import load_dotenv                      # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))

from ai.config import load_config                   # noqa: E402
from ai.llm.provider_factory import create_llm_provider  # noqa: E402

PROMPT = '只输出一个 JSON：{"ok": true}'
SYSTEM = "只输出 JSON，不要解释。"


def warm_up(provider, prompt=PROMPT, system_prompt=SYSTEM):
    """发一次最小请求把模型拉进显存。返回结构化结果，不打印。

    用最短的提示与最小输出：预热的目的是「让权重进显存」，
    不是「跑一次真实任务」。多花的每个 token 都是纯浪费。
    """
    started = time.time()
    text = provider.generate(prompt, system_prompt=system_prompt)
    wall_ms = int((time.time() - started) * 1000)
    metrics = dict(provider.last_metrics or {})
    return {
        "ok": bool((text or "").strip()),
        "wall_ms": wall_ms,
        "load_ms": metrics.get("load_ms"),
        "done_reason": metrics.get("done_reason"),
        "eval_count": metrics.get("eval_count"),
        "thinking_chars": metrics.get("thinking_chars"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="只查状态，不发请求")
    parser.add_argument("--unload", action="store_true", help="卸载模型后退出")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    config = load_config()
    problems = config.validate()
    provider = create_llm_provider(config)

    if args.status:
        health = provider.health()
        result = {"action": "status", "config_problems": problems,
                  "loaded": health.get("loaded"),
                  "available": health.get("available"),
                  "model": health.get("model"), "detail": health.get("detail")}
    elif args.unload:
        provider.unload()
        result = {"action": "unload", "ok": True, "loaded": provider._loaded()}
    else:
        warm = warm_up(provider)
        result = dict(warm, action="warmup", config_problems=problems,
                      loaded=provider._loaded(), keep_alive=config.keep_alive,
                      model=config.ollama_model)
        # 预热也记一条：否则「冷启动到底几秒」永远只有当下那一次的数字，没有历史
        try:
            from ai.call_log import get_repository
            get_repository().record(
                purpose="warmup", provider=config.provider, model=config.ollama_model,
                ok=result["ok"], metrics=provider.last_metrics or {},
                wall_ms=result["wall_ms"], thinking_enabled=config.enable_thinking)
        except Exception as exc:
            print("（写调用日志失败，已忽略：%s）" % exc, file=sys.stderr)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("模型：%s | keep_alive=%s" % (config.ollama_model, config.keep_alive))
        if problems:
            print("配置问题：" + "；".join(problems))
        if result["action"] == "warmup":
            print("预热：%s | 总耗时 %d ms | 加载 %s ms | 输出 %s token | 已驻留 %s"
                  % ("成功" if result["ok"] else "失败", result["wall_ms"],
                     result["load_ms"], result["eval_count"], result["loaded"]))
        elif result["action"] == "status":
            print("可用=%s | 已驻留=%s | %s"
                  % (result["available"], result["loaded"], result["detail"] or "无异常"))
        else:
            print("已卸载，当前驻留=%s" % result["loaded"])

    if result["action"] == "warmup":
        return 0 if result["ok"] else 1
    if result["action"] == "status":
        return 0 if result["available"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
