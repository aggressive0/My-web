"""
第二层证据复核（Phase 6.3）：让模型判断「这些候选到底有没有回答问题」。

为什么需要它：程序层只能判形态。实测有两道题形态齐备却答非所问——
「NARAKA 有多少注册玩家」的候选里有数字也有 players，但没有注册总量；
「客服响应时间」的候选里有日期，但日期说的是版本更新。
这类错误只有读内容才看得出来。

**它只能收紧，不能放宽。** 程序层判定缺必要元素时直接拒答，
不再问模型——否则第一层白做，系统退回「LLM 自由心证」（方案 §8.6）。

调用失败（模型挂、输出不合法、引用了不存在的证据）一律降级为
`undetermined` 并标 `available=False`，绝不因为「问不出来」就当成「充分」。
"""

import json
import logging

from .evidence_gate import EvidenceGate

logger = logging.getLogger(__name__)

VERDICT_SUFFICIENT = "sufficient"
VERDICT_INSUFFICIENT = "insufficient"
VERDICT_CONFLICTING = "conflicting"
VERDICT_UNKNOWN = "undetermined"
VALID_VERDICTS = (VERDICT_SUFFICIENT, VERDICT_INSUFFICIENT, VERDICT_CONFLICTING)

SYSTEM_PROMPT = (
    "你是证据充分性评估器，不负责生成回答。"
    "只能依据给定证据判断用户的问题能否被直接回答。"
    "不得使用自身知识补足证据里没有的事实。"
    "不得因为主题相关就认为可以回答：证据只是提到相关主题，"
    "却没有给出问题所问的具体事实（例如有数字但不是所问的指标、有日期但不是所问的事件），"
    "必须判 insufficient。"
    "判 sufficient 时，必须在 quote 里**逐字复制**证据中直接回答问题的原句"
    "（不能改写、不能拼接、不能概括）；找不到这样的原句就必须判 insufficient。"
    "证据互相矛盾且无法消解时判 conflicting。"
    "只输出 JSON，不要解释或代码块："
    "{\"verdict\": \"sufficient|insufficient|conflicting\", \"quote\": \"证据原句\", "
    "\"evidence_ids\": [\"E1\"], \"missing\": [\"缺少什么\"], \"reason\": \"一句话\"}"
)


def _normalize(text):
    """引用校验用：去空白、统一小写。只做这两件，不做同义改写——
    放宽比对就等于放弃「逐字」这个约束。"""
    return " ".join((text or "").split()).lower()


def verify_quote(quote, cited_hits):
    """模型引用的句子必须真的出现在它引用的证据里。

    这是把「模型说够了」变成「可核验的断言」的唯一办法。
    实测：不加这一步，同一批题在 3×600 与 5×300 两种输入下，
    模型会把 30 道题全部判成 sufficient（包括明明没有答案的 u01/u02）。
    """
    needle = _normalize(quote)
    if len(needle) < 6:
        return False
    return any(needle in _normalize(hit.get("content")) for hit in cited_hits)


def build_prompt(question, hits, snippet=300):
    lines = ["问题：%s" % question, "", "证据："]
    for index, hit in enumerate(hits, start=1):
        lines.append("[E%d] 标题：%s" % (index, (hit.get("title") or "")[:60]))
        lines.append((hit.get("content") or "")[:snippet])
        lines.append("")
    return "\n".join(lines)


class SemanticChecker:
    def __init__(self, provider, max_evidence=5, snippet=500):
        self.provider = provider
        # 看 5 条：只看 3 条会把「答案在第 4–5 位」误判成证据不足（实测 q23）。
        # 每条给 500 字——与生产 chunk 长度一致。截到 300 会把答案所在的后半段切掉，
        # 实测直接导致 k01「每阶段 4 个挑战任务」被判成没提到。
        self.max_evidence = max(1, int(max_evidence))
        self.snippet = max(80, int(snippet))

    def check(self, question, hits):
        """返回 {verdict, reason, missing, available}。任何异常都降级，不抛。"""
        used = list(hits or [])[:self.max_evidence]
        if not used:
            return {"verdict": VERDICT_INSUFFICIENT, "reason": "no_candidates",
                    "missing": [], "available": True}

        labels = {"E%d" % (i + 1): hit for i, hit in enumerate(used)}
        try:
            text = self.provider.generate(
                build_prompt(question, used, snippet=self.snippet),
                system_prompt=SYSTEM_PROMPT)
            document = json.loads((text or "").strip())
        except Exception as exc:
            logger.warning("语义复核调用失败：%r", exc)
            return {"verdict": VERDICT_UNKNOWN, "reason": "semantic_unavailable",
                    "missing": [], "available": False}

        verdict = document.get("verdict")
        cited = [str(x).upper() for x in (document.get("evidence_ids") or [])]
        # 引用了不存在的证据编号 = 模型在编，整条判断作废
        unknown = [label for label in cited if label not in labels]
        if unknown:
            logger.warning("语义复核引用了不存在的证据：%s | raw=%r",
                           unknown[:3], (text or "")[:200])
            return {"verdict": VERDICT_UNKNOWN, "reason": "semantic_untrusted",
                    "missing": [], "available": False}

        chunk_ids = [labels[label].get("chunk_id") for label in cited]
        allowed = [hit.get("chunk_id") for hit in used]
        try:
            EvidenceGate.validate_semantic_judgement(
                {"verdict": verdict, "evidence_ids": chunk_ids}, allowed)
        except Exception as exc:
            logger.warning("语义复核结果不可信：%s | raw=%r", exc, (text or "")[:200])
            return {"verdict": VERDICT_UNKNOWN, "reason": "semantic_untrusted",
                    "missing": [], "available": False}

        # 判「充分」必须给出可核验的原句。核验不过就当证据不足：
        # 宁可拒答，也不把一个无法追溯的「模型觉得够了」当成放行依据。
        if verdict == VERDICT_SUFFICIENT:
            quote = str(document.get("quote") or "")
            cited_hits = [labels[label] for label in cited]
            if not verify_quote(quote, cited_hits):
                logger.warning("语义复核的引用无法核验：quote=%r cited=%s",
                               quote[:80], cited[:3])
                return {"verdict": VERDICT_INSUFFICIENT,
                        "reason": "semantic_quote_unverified",
                        "missing": ["可逐字引用的直接证据"],
                        "evidence_labels": cited, "available": True}

        return {
            "verdict": verdict,
            "reason": str(document.get("reason") or "")[:200],
            "quote": str(document.get("quote") or "")[:200],
            "missing": [str(x)[:80] for x in (document.get("missing") or [])][:5],
            "evidence_labels": cited,
            "available": True,
        }
