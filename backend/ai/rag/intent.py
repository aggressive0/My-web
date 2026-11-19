"""
问题路由（Phase 6.3 P1）。

为什么先做规则而不是让 LLM 分类：
1. 路由错一次，后面全错——它必须是可解释、可回归的东西，不能是「模型觉得」。
2. 五类意图的边界在中文里有很强的词面信号（「多少条评论」= SQL，「设计方案」= 策略）。
3. 规则跑一次是微秒级，LLM 分类要几百毫秒，而路由在检索之前。

命中规则的原文会一并返回（matched），出问题时能直接看出是哪条规则误伤，
而不是对着一个 intent 字符串猜。
"""

import re
from dataclasses import asdict, dataclass

KB_FACTUAL = "kb_factual_qa"
SQL_ANALYTICS = "sql_analytics"
STRATEGY = "strategy_generation"
OUT_OF_SCOPE = "out_of_scope"
AMBIGUOUS = "ambiguous"

VALID_INTENTS = (KB_FACTUAL, SQL_ANALYTICS, STRATEGY, OUT_OF_SCOPE, AMBIGUOUS)


@dataclass
class RouteResult:
    intent: str
    matched: str = ""       # 命中的规则名，空表示走了默认兜底
    reason: str = ""

    def to_dict(self):
        return asdict(self)


# 顺序即优先级。越界与策略必须排在 SQL 前面：
# 「如何降低负面评论占比」同时含「如何」和「占比」，先判 SQL 就会把它送错流程。
_RULES = [
    (OUT_OF_SCOPE, "weather", re.compile(r"天气|气温|下雨|股票|汇率|菜谱|电影票")),
    # 「写一段 Python 快速排序代码」中间会插语言名与算法名，不能要求紧邻
    (OUT_OF_SCOPE, "code", re.compile(
        r"(?:写|生成|实现)(?:一段|一个|个)?[^，。]{0,12}(?:代码|程序|脚本)|排序算法", re.I)),
    (OUT_OF_SCOPE, "translation", re.compile(r"翻译成|帮我翻译")),

    (STRATEGY, "design", re.compile(r"设计|方案|策划|制定|建议|策略")),
    (STRATEGY, "how_to_change", re.compile(r"如何(?:提升|降低|提高|减少|改善|挽回|应对)|怎么(?:提升|降低|减少)")),
    (STRATEGY, "campaign", re.compile(r"召回活动|回归活动|运营活动|拉新|促活")),

    (SQL_ANALYTICS, "metric", re.compile(
        r"同时在线|在线人数|在线峰值|CCU|评论数|好评率|差评率|负面评论占比|风险指数"
        r"|下载量|销量|活跃度|留存率|新增多少|多少条评论", re.I)),
    (SQL_ANALYTICS, "time_window", re.compile(
        r"(?:最近|近|过去)\s*\d+\s*(?:天|日|周|个月)|过去一周|本周|昨天|今天(?:的)?(?:数据|指标)")),
    (SQL_ANALYTICS, "superlative", re.compile(r"(?:最高|最低|最多|最少)的(?:是)?(?:哪|谁)")),

    (AMBIGUOUS, "vague_pronoun", re.compile(
        r"(?:这个|那个|这款|那款|它)(?:游戏)?(?:最近)?(?:怎么样|咋样|如何|好不好)")),
    (AMBIGUOUS, "vague_change", re.compile(r"有什么(?:变化|不同|区别|问题|情况)吗")),
]


def route(question):
    """返回 RouteResult。命中不了任何规则时落到知识库问答。"""
    text = (question or "").strip()
    if not text:
        return RouteResult(AMBIGUOUS, "empty", "问题为空")

    for intent, name, pattern in _RULES:
        match = pattern.search(text)
        if match:
            return RouteResult(intent, name, "命中规则 %s：%r" % (name, match.group()))

    # 兜底：能问出具体名词/数字的，按知识库问答处理；纯指代且极短的，判歧义
    if len(text) <= 8 and not re.search(r"[A-Za-z0-9]", text):
        return RouteResult(AMBIGUOUS, "too_short", "问题过短且无可识别对象")
    return RouteResult(KB_FACTUAL, "", "未命中特殊规则，按知识库问答处理")
