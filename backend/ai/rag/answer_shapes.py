"""
答案形态定义与程序层检测（Phase 6.3）。

核心命题：**答案形态不等于证据充分性，但形态是必要条件。**
没有数字就答不了「多少人」，没有地名就答不了「在哪里」。

两条设计原则，都是被实测逼出来的：

1. **要求按问题推导，不用固定清单。** 第一版把 count 写死成「数字 + 人员单位 +
   邻接关系」，结果「TennoGen 多少年」被判成证据不足——它问的是年数，
   而语料里写的是 "11 entire years"。§8.4-B 那句「不要对所有问题使用同一份
   固定字段清单」不是风格建议，是功能需求。

2. **规则只判必要条件，不判语义。** 「数字与人名相邻」不等于「这是团队人数」，
   所以第一层通过只代表形态齐备，语义是否对得上要第二层复核（见 evidence_gate）。

本模块不联网、不加载模型，可完全离线单测。
"""

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 计数的单位族
# 「多少」问的东西不同，需要的单位就不同：多少人要人员单位，多少年要时间单位。
_FAMILY_RULES = [
    # 编制与玩家人数必须分开：实测 "Six-player Squads" / "6-player game mode" 会被
    # 「人员单位」误判成团队人数。问编制的主体是员工/团队，问规模的主体才是玩家。
    ("staff", re.compile(r"员工|团队|成员|开发者|开发人员|编制|工作室人数"
                         r"|staff|employees?|headcount", re.I)),
    ("players", re.compile(r"玩家|用户|players?|users?", re.I)),
    ("time", re.compile(r"(?:多少|几)(?:年|个月|月|天|周|小时|分钟|秒)|多久", re.I)),
    ("times", re.compile(r"(?:多少|几)(?:次|回)", re.I)),
    ("amount", re.compile(r"(?:多少|几)(?:个|条|款|项|种)", re.I)),
]

_UNIT = {
    # 只认「组织编制」类单位。"player(s)" 是游戏模式的修饰语（Six-player Squads），
    # 不是员工人数——这条区分是 Q2 假阴性暴露出来的。
    "staff": re.compile(
        r"(?:名|位)?\s*(?:员工|团队成员|成员|开发人员|工作人员)"
        r"|(?:employees?|staff|headcount|team\s+members?)", re.I),
    "players": re.compile(r"(?:名|位)?\s*(?:玩家|用户)|(?:players?|users?)", re.I),
    "time": re.compile(
        r"(?:年|个月|月|天|周|小时|分钟|秒)"
        r"|(?:years?|months?|days?|weeks?|hours?|minutes?|seconds?)", re.I),
    "times": re.compile(r"(?:次|回)|(?:times?)", re.I),
    # 英文语料里「4 Challenge quests」「two difficulties」用的是复数名词，
    # 只认 items/entries 会把它们判成没有单位。允许任意英文复数名词。
    "amount": re.compile(r"(?:个|条|款|项)|(?:items?|entries?)|[A-Za-z]{3,}(?:s|es)\b", re.I),
    # 兜底：中文量词，或任意英文复数名词（quests / difficulties / stages …）。
    # 允许英文复数是为了不与语料的具体用词耦合，代价是偶尔会把 "224 points" 当成计数——
    # 但那种情况只会在「问数量」的题上出现，方向上是放宽而不是收紧，可接受。
    "generic": re.compile(r"(?:个|条|款|项|名|种|座|张)|[A-Za-z]{3,}(?:s|es)\b"),
}

_NUMBER = re.compile(
    r"\d[\d,\.]*"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen)\b", re.I)

_DATE = re.compile(
    r"20\d{2}[-/年]\d{1,2}"
    r"|\d{1,2}\s*(?:月|/\d{1,2})"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{1,2}"
    r"|\d{1,2}\s*(?:st|nd|rd|th)\b"
    r"|\b(?:UTC|PST|PDT|CET|CEST|ET)\b", re.I)
_EVENT_WORD = re.compile(
    r"(?:更新|维护|上线|开服|开始|结束|发布|开放|截止|报名)"
    r"|(?:update|launch|release|maintenance|start|end|go\s+live|available)", re.I)

_GEO = re.compile(
    r"(?:中国|美国|日本|韩国|新加坡|德国|法国|英国|加拿大|澳大利亚|印度|泰国|越南|巴西"
    r"|北京|上海|深圳|广州|杭州|成都|香港|台北|东京|首尔"
    r"|China|USA|United\s+States|Japan|Korea|Singapore|Germany|France|UK|Canada"
    r"|Australia|India|Thailand|Vietnam|Brazil|Europe|Eurasia|MENA|North\s+America"
    r"|South\s+America|Asia)", re.I)
_FACILITY = re.compile(
    r"(?:机房|数据中心|服务器室|服务器所在|节点位于|部署在|托管于|物理位置)"
    r"|(?:data\s*cent(?:er|re)|server\s+room|hosting|colocation)", re.I)

# 问「在哪里挑战/在哪里获得」指的是游戏内地点，不是机房。
# 这类只需要「介词 + 专名」的场所表达，用机房词去要求它必然误拦。
_PLACE_EXPR = re.compile(
    r"(?:\bin|\bat|\batop|\bfrom|\binside)\s+(?:the\s+)?[A-Z][A-Za-z'’\-]{2,}"
    r"|(?:位于|地处|在)\s*[\u4e00-\u9fa5A-Za-z]{2,}", re.I)

# 问题是否在问「设施位置」（机房/服务器）而不是游戏内地点
_FACILITY_QUESTION = re.compile(
    r"机房|数据中心|服务器(?:机房|所在|位置|部署)|物理位置|托管"
    r"|data\s*cent(?:er|re)|server\s+room|hosting", re.I)

_POLICY = re.compile(
    r"(?:退款|退还|不予退还|退货|补偿|政策|条款|规定|规则|禁止|不得|须符合)"
    r"|(?:refund|policy|terms|not\s+eligible|prohibited|must\s+comply)", re.I)
_APPLICABILITY = re.compile(
    r"(?:适用(?:于|范围)|仅限|凡|所有玩家|符合下列|在下列情况)"
    r"|(?:applies\s+to|eligible|under\s+the\s+following|for\s+all\s+players)", re.I)
_CONDITION = re.compile(
    r"(?:如果|若|当|条件|前提|需在|须在|方可)"
    r"|(?:if|when|provided\s+that|subject\s+to|condition)", re.I)

_STEP = re.compile(
    r"(?:步骤|第一步|首先|其次|然后|最后|流程|按照以下|完成(?:后|之后))"
    r"|(?:step\s*\d|first,|then,|finally|to\s+unlock|complete\s+the)", re.I)
_GOAL = re.compile(
    r"(?:解锁|获得|开启|达成|目标|即可)"
    r"|(?:unlock|obtain|receive|goal|to\s+get)", re.I)
_NAMED = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]{2,}|[\u4e00-\u9fa5]{2,}")


@dataclass(frozen=True)
class Requirement:
    name: str
    detect: object          # callable(text) -> bool
    description: str = ""


def _adjacent(number_re, unit_re, window=24):
    """数字与单位在窗口内共现。单纯「有数字」+「有单位」会把 '224 points ... 17 teams' 判成计数。"""
    def detect(text):
        nums = [m.start() for m in number_re.finditer(text)]
        units = [m.start() for m in unit_re.finditer(text)]
        return any(abs(n - u) <= window for n in nums for u in units)
    return detect


def detect_count_family(question):
    for name, pattern in _FAMILY_RULES:
        if pattern.search(question or ""):
            return name
    return "generic"


def resolve_requirements(question, shape):
    """把「问题 + 形态」解析成具体的必要条件列表。

    count 是按问题变化的：问人数要人员单位，问年数要时间单位。
    其余形态目前是固定的，但同样走这个入口，将来要分化不必改调用方。
    """
    if shape == "count":
        family = detect_count_family(question)
        unit = _UNIT[family]
        return [
            Requirement("numeric_value", lambda t: bool(_NUMBER.search(t)),
                        "数字或数词"),
            Requirement("count_unit", lambda t: bool(unit.search(t)),
                        "与问题同族的计数单位（%s）" % family),
            Requirement("subject_relation", _adjacent(_NUMBER, unit),
                        "数字与单位相邻，而不是各说各的"),
        ]
    if shape == "date":
        return [
            Requirement("date_expression", lambda t: bool(_DATE.search(t)), "日期表达"),
            Requirement("event_relation", lambda t: bool(_EVENT_WORD.search(t)), "与所问事件相关"),
        ]
    if shape == "location":
        # 问「机房在哪」和问「Icebind 在哪挑战」是两件事：
        # 前者要设施词（赛区/地区不是机房），后者只要「介词 + 专名」的场所表达。
        # 第一版对两者一视同仁，把游戏内地点题全部误拦了（实测 5 道）。
        if _FACILITY_QUESTION.search(question or ""):
            return [
                Requirement("facility_relation",
                            lambda t: bool(_FACILITY.search(t)), "与机房/服务器相关"),
                Requirement("place_expression",
                            lambda t: bool(_FACILITY.search(t) or _GEO.search(t)),
                            "具体地点"),
            ]
        return [
            Requirement("place_expression",
                        lambda t: bool(_PLACE_EXPR.search(t) or _GEO.search(t)),
                        "场所表达（介词+专名，或已知地名）"),
        ]
    if shape == "policy":
        return [
            Requirement("policy_statement", lambda t: bool(_POLICY.search(t)), "条款陈述"),
            Requirement("applicability", lambda t: bool(_APPLICABILITY.search(t)), "适用范围"),
            Requirement("conditions", lambda t: bool(_CONDITION.search(t)), "生效条件"),
        ]
    if shape == "procedure":
        return [
            Requirement("action_steps", lambda t: bool(_STEP.search(t)), "可执行步骤"),
            Requirement("goal", lambda t: bool(_GOAL.search(t)), "步骤目标"),
        ]
    if shape == "entity":
        return [Requirement("named_entity", lambda t: bool(_NAMED.search(t)), "专名")]
    return []          # mixed：程序层没有可判定的必要条件


# 向后兼容：只要静态名字的调用方（如旧测试）仍可拿到形态清单
ANSWER_SHAPES = {name: tuple(r.name for r in resolve_requirements("", name))
                 for name in ("count", "date", "location", "policy", "procedure",
                              "entity", "mixed")}


def detect_shape(question):
    """按关键词判定答案形态。顺序即优先级。"""
    text = question or ""
    for shape, pattern in [
        ("policy", re.compile(r"退款|退货|补偿|政策|规定|条款|规则|封禁标准", re.I)),
        ("count", re.compile(r"多少|几个|几名|几次|几条|几款|几种|数量|占比|比例", re.I)),
        ("date", re.compile(r"什么时候|何时|几点|哪天|日期|时间窗口|多久|预计", re.I)),
        ("location", re.compile(r"哪里|在哪|位于|位置|地点|哪个城市|机房", re.I)),
        # 「是怎么说的」「怎么规定」不是在问步骤，是问原文表述——
        # 不加这个否定，q21「直播安排是怎么说的」会被判成方法题然后误拦。
        ("procedure", re.compile(r"怎么(?!说|表述|规定|写)|如何(?!说)|步骤|流程|解锁|怎样", re.I)),
    ]:
        if pattern.search(text):
            return shape
    return "mixed"


def evaluate_requirements(question, shape, texts):
    """返回 (missing, required_names)。缺失 = 所有候选的并集里都没有该元素。"""
    requirements = resolve_requirements(question, shape)
    if not requirements:
        return [], []
    present = set()
    for text in texts:
        for req in requirements:
            if req.name not in present and req.detect(text or ""):
                present.add(req.name)
    missing = [req.name for req in requirements if req.name not in present]
    return missing, [req.name for req in requirements]
