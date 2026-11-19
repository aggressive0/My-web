"""
问答输出结构（Phase 6.3 P1）。

两个刻意的设计：

1. **模型只能引用证据编号（E1/E2…），不能自己写 chunk_id。**
   让模型输出 chunk_id，它一定会编出看起来很像的串；编号由我们生成、
   由我们映射，模型伪造的编号会因为「不在本次候选里」被直接拒掉。

2. **`insufficient` 是模型可主动声明的字段。**
   门控通过只说明形态齐备，不代表内容真的回答了问题。模型如果发现
   「有日期但没有所问事件的日期」，应当自己说不充分，而不是硬编一个答案。
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GeneratedAnswer(StrictModel):
    # answer 允许为空串是刻意的：模型确实会返回 {"answer": "", "insufficient": false}。
    # 让它在这一层就炸成 Schema 错误，会把「模型没话说」和「格式不对」混为一谈；
    # 空答由调用方单独判类（empty_answer），错误码才对得上病因。
    answer: str = ""
    citations: List[str] = Field(default_factory=list)   # 证据编号，如 E1
    insufficient: bool = False

    @field_validator("citations")
    @classmethod
    def _labels_are_short(cls, value):
        if len(value) > 8:
            raise ValueError("引用编号最多 8 个")
        for label in value:
            if not (len(label) == 2 and label[0] in "Ee" and label[1].isdigit()):
                raise ValueError("引用编号必须形如 E1，实际 %r" % label)
        return value
