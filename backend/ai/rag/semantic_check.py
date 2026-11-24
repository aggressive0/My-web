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


_PARTIAL_MIN = 24        # 逐字片段的最短长度


def _longest_common(a, b):
    """最长公共子串长度。用于判断「引用是不是从原文里截出来的」。"""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, start=1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def verify_quote(quote, cited_hits):
    """模型引用的句子必须能从它引用的证据里找到。

    这是把「模型说够了」变成「可核验的断言」的唯一办法。
    实测：不加这一步，同一批题在 3×600 与 5×300 两种输入下，
    模型会把 30 道题全部判成 sufficient（包括明明没有答案的 u01/u02）。

    两种通过方式：
    1. 整句逐字出现（去掉空白与大小写差异）；
    2. 存在 ≥24 字符的连续逐字片段——模型常截取半句或去掉标点，
       按整句比对会把这类**真引用**判成伪造（实测 q06/q23/k05 就这样被误杀）。
       24 字符足够具体，随机编造撞上的概率可忽略。
    """
    needle = _normalize(quote)
    # 6 个字符太松："the servers" 这种到处都有的短语也能通过，
    # 等于只要模型随便挑一句常见表达就算「引用了原文」。
    if len(needle) < 12:
        return False
    for hit in cited_hits:
        haystack = _normalize(hit.get("content"))
        if needle in haystack:
            return True
        if _longest_common(needle, haystack) >= _PARTIAL_MIN:
            return True
    return False


_NEGATION = (
    "没有提到", "未提及", "没有给出", "未包含", "不包含", "无法确认", "没有明确",
    "未明确", "无法确定", "并不包含", "缺少", "仅提到", "只提到", "仅包含", "未提供",
    "no mention", "not mentioned", "does not mention", "doesn't mention",
    "does not specify", "doesn't specify", "no evidence", "cannot be determined",
    "not explicitly", "no explicit", "without mentioning",
    # 实测 u01：reason 写「but the question asks for ... However, the evidence only
    # mentions banned players」却仍判 sufficient——它是在用转折句说「答非所问」。
    "only mentions", "only mentions", "only provides", "does not provide",
    "doesn't provide", "but the question asks", "does not answer", "doesn't answer",
)


def contradicts_verdict(verdict, document):
    """模型自相矛盾时把它拦下来。

    实测 q23：reason 里写着「证据没有明确提到电台名称」，verdict 却是 sufficient。
    引文校验过不了它——那句话确实存在，只是不回答问题。
    这里只认**明确的否定表述**，不做情感分析，避免把「没有变化」这类
    正常陈述误判成否定。
    """
    if verdict not in (VERDICT_SUFFICIENT, VERDICT_CONFLICTING):
        return None
    blob = " ".join([
        str(document.get("reason") or ""),
        " ".join(str(x) for x in (document.get("missing") or [])),
    ]).lower()
    if any(token in blob for token in _NEGATION):
        return "reason_or_missing_says_absent"
    # 「判充分」却说还缺东西，本身就是矛盾
    if document.get("missing"):
        return "missing_not_empty"
    return None


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
            # temperature=0：这是判定调用，不是创作。实测同一题在默认温度下
            # 会来回翻转（u01 有时 insufficient、有时 sufficient），
            # 而拒答结论必须可重复，否则「复现一次」就成了碰运气。
            text = self.provider.generate(
                build_prompt(question, used, snippet=self.snippet),
                system_prompt=SYSTEM_PROMPT,
                options={"temperature": 0})
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

        contradiction = contradicts_verdict(verdict, document)
        if contradiction:
            logger.warning("语义复核自相矛盾（%s）：verdict=%s reason=%r",
                           contradiction, verdict, str(document.get("reason") or "")[:120])
            return {"verdict": VERDICT_INSUFFICIENT,
                    "reason": "semantic_self_contradiction",
                    # 保留模型原话：判据是关键词匹配，必须能回看它到底说了什么，
                    # 否则「为什么这题被拦」只能靠猜。
                    "original_reason": str(document.get("reason") or "")[:200],
                    "missing": ["证据未直接回答问题"],
                    "evidence_labels": cited, "available": True}

        return {
            "verdict": verdict,
            "reason": str(document.get("reason") or "")[:200],
            "quote": str(document.get("quote") or "")[:200],
            "missing": [str(x)[:80] for x in (document.get("missing") or [])][:5],
            "evidence_labels": cited,
            "available": True,
        }
