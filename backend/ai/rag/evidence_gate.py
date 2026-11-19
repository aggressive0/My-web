"""
证据门（Phase 6.3）：判断「现有候选能不能回答这个问题」。

为什么独立成服务，而不是 Prompt 里的一段指令：
Prompt 里的规则会被模型重新解释，而门的结果必须能被断言、被审计、被回归测试
钉死。换模型、改 Prompt 都不应该让「检索到 5 条就回答」这个行为回来。

当前实现**只有第一层（程序规则）**。第二层（LLM 语义复核）预留了接口但未接线，
理由是实测：四个失败问题的 20 条候选里 0 条含所需事实，第一层就能全部拦住。
先证明第一层有效再加第二层——反过来做，失败时分不清是哪一层的责任。

状态语义（与 API 契约一致）：
    sufficient            形态齐备（不等于内容为真，仍需第二层可选复核）
    insufficient_evidence 缺必要元素，明确不能作答
    undetermined          该形态没有可程序化的约束（mixed），必须交给第二层
"""

from dataclasses import asdict, dataclass, field

from .answer_shapes import detect_shape, evaluate_requirements, resolve_requirements

STATUS_SUFFICIENT = "sufficient"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_UNDETERMINED = "undetermined"

REASON_MISSING = "retrieved_documents_do_not_support_required_fact"
REASON_NO_CANDIDATES = "no_candidates_retrieved"
REASON_SPLIT = "required_elements_split_across_documents"
REASON_NO_RULE = "answer_shape_has_no_programmatic_rule"


@dataclass
class GateResult:
    status: str
    answer_shape: str
    required_elements: list = field(default_factory=list)
    missing_elements: list = field(default_factory=list)
    candidate_count: int = 0
    valid_count: int = 0
    valid_document_ids: list = field(default_factory=list)
    reason: str = ""
    layer: str = "program"

    def to_dict(self):
        return asdict(self)


def _content(hit):
    """同时兼容 SearchHit 与 to_dict() 后的字典。"""
    if isinstance(hit, dict):
        return hit.get("content") or ""
    return getattr(hit, "content", "") or ""


def _doc_id(hit):
    if isinstance(hit, dict):
        return hit.get("document_id")
    return getattr(hit, "document_id", None)


class EvidenceGate:
    def __init__(self, shape_detector=None):
        self.shape_detector = shape_detector or detect_shape

    def evaluate(self, question, hits, shape=None):
        hits = list(hits or [])
        shape = shape or self.shape_detector(question)
        required = [req.name for req in resolve_requirements(question, shape)]

        if not hits:
            return GateResult(
                status=STATUS_INSUFFICIENT, answer_shape=shape,
                required_elements=required, missing_elements=required,
                candidate_count=0, valid_count=0, reason=REASON_NO_CANDIDATES)

        if not required:
            # mixed：程序层没有可判定的必要条件。如实说「定不了」，而不是猜。
            return GateResult(
                status=STATUS_UNDETERMINED, answer_shape=shape,
                required_elements=[], missing_elements=[],
                candidate_count=len(hits), valid_count=len(hits),
                valid_document_ids=[d for d in (_doc_id(h) for h in hits) if d],
                reason=REASON_NO_RULE)

        texts = [_content(h) for h in hits]
        missing, _ = evaluate_requirements(question, shape, texts)
        # 单篇就完整 → 强证据；只有并集完整 → 元素分散在多篇，仍可用但要留痕
        complete_ids = []
        for hit in hits:
            own_missing, _ = evaluate_requirements(question, shape, [_content(hit)])
            if not own_missing:
                doc_id = _doc_id(hit)
                if doc_id:
                    complete_ids.append(doc_id)

        if missing:
            return GateResult(
                status=STATUS_INSUFFICIENT, answer_shape=shape,
                required_elements=required, missing_elements=missing,
                candidate_count=len(hits), valid_count=0,
                valid_document_ids=[], reason=REASON_MISSING)

        return GateResult(
            status=STATUS_SUFFICIENT, answer_shape=shape,
            required_elements=required, missing_elements=[],
            candidate_count=len(hits), valid_count=len(complete_ids),
            valid_document_ids=complete_ids,
            reason="" if complete_ids else REASON_SPLIT)

    # ------------------------------------------------------ 第二层（预留，未接线）
    @staticmethod
    def validate_semantic_judgement(judgement, allowed_chunk_ids):
        """校验第二层 LLM 判断的结构与引用范围。

        现在没接线，但校验先写：等接上时，任何「引用了不存在的 chunk」的输出
        都会被拒掉，而不是被当成有效证据（方案 §8.5 的硬要求）。
        """
        if not isinstance(judgement, dict):
            raise ValueError("语义复核结果必须是对象")
        verdict = judgement.get("verdict")
        if verdict not in ("sufficient", "insufficient", "conflicting"):
            raise ValueError("verdict 必须是 sufficient / insufficient / conflicting")
        allowed = set(allowed_chunk_ids or [])
        unknown = [c for c in (judgement.get("evidence_ids") or []) if c not in allowed]
        if unknown:
            raise ValueError("引用了不存在的 chunk：%s" % ", ".join(unknown[:5]))
        return verdict
