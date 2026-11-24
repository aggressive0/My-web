"""
从「当前信号」构造知识检索查询（Phase 6.5）。

之前 `KNOWLEDGE_QUERY` 是常量（"版本更新 维护 补偿 活动 玩家反馈 体验问题"），
于是 CCU 在跌、引的却可能是两个月前讲活动设计的公告——证据真实但与问题无关。

这里做两件事，都不调用模型：
1. 从最近负面评论里提取高频实词（"unstable"、"loading"、"buggy"…）；
2. 结合指标方向决定要查什么：在线人数下滑 → 查服务器/匹配/稳定性；
   差评占比高 → 查修复/补偿/说明。

为什么不用 LLM 生成查询词：检索发生在生成之前，用生成模型去决定「检索什么」，
一旦它跑偏，后面的证据与结论会一起跑偏，而且无法解释。规则的输出可以逐条核对。
"""

import re

# 评论语料是英文；这些词在任何一条差评里都出现，对检索没有区分度
_STOPWORDS = set("""
a about after again against all almost also although always am among an and another any
anyone anything are around as at back be because been before being below best better between
both but by can cannot could did do does doing done down during each either else enough even
ever every few first for from further get gets getting give given go goes going got had has
have having he her here hers herself him himself his how however i if in into is it its itself
just know last least less let like likely little long look lot made make makes making many may
maybe me might mine more most much must my myself near need never new next no nor not nothing
now of off often on once one only or other others otherwise our ours ourselves out over own same
say says see seems shall she should since so some something still such take than that the their
theirs them themselves then there these they thing things think this those though through thus
to too toward two under until up upon us use used using very want was way we well were what when
where whether which while who whom why will with within without would yes yet you your yours
yourself
game games gaming player players play playing played review reviews hour hours time times
good bad better worse really actually still again even much little bit lot
dont cant wont didnt doesnt isnt arent wasnt werent havent hasnt couldnt shouldnt wouldnt
im ive id youre theyre thats whats heres theres
""".split())

# 脏话与情绪词不是「话题」，进查询只会污染检索。
# 情绪本身已经由 negative_ratio 指标表达了，查询词要的是「说到了什么」。
_BLOCKED = set("""
fuck fucking fucked shit shitty bullshit damn damned crap crappy ass bitch bastard sucks suck
sucked trash garbage awful horrible terrible worst hate hated stupid dumb idiot moron
running gonna wanna gotta literally honestly basically actually pretty quite maybe probably
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{3,}")
_NON_ALPHA = re.compile(r"[^a-z]")

# 指标方向 → 需要检索的运营主题（中文查询词，BGE-M3 跨语言可匹配英文公告）
DIRECTION_HINTS = {
    "ccu_down": "服务器 稳定性 匹配 排队 掉线",
    "sentiment_down": "修复 补偿 说明 平衡性",
    "refund_up": "退款 补偿 政策",
}

BASE_QUERY = "版本更新 维护 修复 玩家反馈"


def extract_terms(texts, limit=8, min_count=2):
    """从文本里取高频实词。返回 [(word, count)]，按次数降序、同频按首次出现。"""
    counts = {}
    order = {}
    for index, text in enumerate(texts or []):
        for match in _WORD.finditer(text or ""):
            # 撇号必须去掉再比对停用词：实测 "it's" 混进了查询词，
            # 因为停用词表里是 "its"——同一类还有 don't / player's。
            word = _NON_ALPHA.sub("", match.group().lower())
            if len(word) < 4 or word in _STOPWORDS or word in _BLOCKED:
                continue
            counts[word] = counts.get(word, 0) + 1
            order.setdefault(word, index)
    # 同词根合并：实测同时出现 "feel" 与 "feels"，两者进查询只占位不增信息。
    # 只做「去掉尾部 s」这种级别的归一，不引第三方词干化——它对检索词的
    # 取舍影响很小，但会让「mods 变成 mod」这种反而更差的改写发生。
    best = {}
    for word, count in counts.items():
        if count < min_count:
            continue
        stem = word[:-1] if (len(word) > 4 and word.endswith("s")) else word
        current = best.get(stem)
        if current is None or count > current[1]:
            best[stem] = (word, count)

    ranked = sorted(best.values(), key=lambda item: (-item[1], order.get(item[0], 0)))
    return ranked[:limit]


def detect_directions(metrics):
    """从已算出的指标判断信号方向。只认有数据的指标，缺失不猜。"""
    values = {item["name"]: item.get("value") for item in metrics.get("evidence") or []}
    directions = []

    change = values.get("ccu_change_24h_pct")
    if change is not None and change <= -3.0:
        directions.append("ccu_down")

    negative = values.get("negative_ratio_window")
    if negative is not None and negative >= 0.3:
        directions.append("sentiment_down")

    refunded = values.get("refunded_ratio_window")
    if refunded is not None and refunded >= 0.1:
        directions.append("refund_up")

    return directions


def build_knowledge_query(metrics, texts=None, max_terms=6):
    """拼检索查询，并返回依据，便于事后核对「为什么查这些词」。"""
    directions = detect_directions(metrics)
    terms = extract_terms(texts or [], limit=max_terms)

    parts = [BASE_QUERY]
    for name in directions:
        parts.append(DIRECTION_HINTS[name])
    parts.extend(word for word, _ in terms)

    query = " ".join(" ".join(parts).split())
    basis = {
        "directions": directions,
        "terms": [word for word, _ in terms],
        "note": ("由指标方向与近期差评高频词生成，未使用模型"
                 if directions or terms else "无可用信号，回落到基础查询"),
    }
    return query, basis
