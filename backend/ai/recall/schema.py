"""
召回策略结构（writing-block.md §9.3 的落地版本）。

相对方案的四处收紧，每处都有理由：

1. `execution_status` 只允许 `"suggested"`。本系统没有触达通道，永远不会真的
   执行召回；允许别的取值等于给「我们已执行」留后门（AGENTS.md §4.1）。
2. `knowledge_evidence` 必须带 `chunk_id`。没有 chunk_id 的引用无法回溯到原文，
   证据链就断了——这比「字段少一个」严重得多。
3. `extra="forbid"`。模型自己加字段说明它没按契约输出，应当失败而不是静默接受。
4. `recall_actions` 数量限制在 1–5 条。开放上限只会让模型把同一件事拆成十条。

字段名与方案保持一致，不做「更优雅」的重命名：下游（前端、报告）已经按这些
名字写了文档，为了好看改名会让文档和代码对不上。
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ObservedSignal(StrictModel):
    metric: str = Field(min_length=1)
    value: Optional[float] = None
    source: str = "sqlite"


class DataEvidence(StrictModel):
    name: str = Field(min_length=1)
    value: Optional[float] = None
    description: str = ""
    source: str = "sqlite"


class KnowledgeEvidence(StrictModel):
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)      # 没有它就无法回溯原文
    title: str = ""
    source_name: str = ""
    source_url: Optional[str] = None
    claim: str = ""


class AiInference(StrictModel):
    reasoning: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"] = "medium"
    limitations: str = ""


class RecallAction(StrictModel):
    name: str = Field(min_length=1)
    target_group: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    description: str = Field(min_length=1)
    expected_effect: str = ""
    measurement: List[str] = Field(default_factory=list)


class RiskAnalysis(StrictModel):
    risk_level: Literal["high", "medium", "low", "unknown"] = "unknown"
    observed_signals: List[ObservedSignal] = Field(default_factory=list)
    possible_causes: List[str] = Field(default_factory=list)


class RecallStrategy(StrictModel):
    app_id: int
    game_name: Optional[str] = None
    generated_at: Optional[str] = None
    execution_status: Literal["suggested"] = "suggested"
    summary: str = Field(min_length=1)
    risk_analysis: RiskAnalysis
    data_evidence: List[DataEvidence] = Field(default_factory=list)
    knowledge_evidence: List[KnowledgeEvidence] = Field(default_factory=list)
    ai_inference: List[AiInference] = Field(default_factory=list)
    recall_actions: List[RecallAction]
    limitations: List[str] = Field(default_factory=list)

    @field_validator("app_id")
    @classmethod
    def _app_id_must_be_positive(cls, value):
        if value <= 0:
            raise ValueError("app_id 必须为正整数（-1 只用于 global 知识，不是策略）")
        return value

    @field_validator("recall_actions")
    @classmethod
    def _actions_within_bounds(cls, value):
        if not value:
            raise ValueError("recall_actions 不能为空")
        if len(value) > 5:
            raise ValueError("recall_actions 最多 5 条，实际 %d 条" % len(value))
        return value


SCHEMA_VERSION = "recall_strategy/v1"
