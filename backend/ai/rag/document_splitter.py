"""
Chunk 切分（Phase 6.2）。

单位是**字符**而不是 token。方案 §5.4 要求口径明确，字符数可以离线复算，
不依赖某个 tokenizer 的版本；代价是中英混排时每块的语义密度不均。
这一点记在这里，是 6.4 检索评估要观察的变量，不是可以假装不存在的问题。

切分优先在语义边界断开，实在找不到才硬切：
段落 > 换行 > 中文句末 > 英文句末 > 逗号 > 硬切。

只在窗口**后 40%** 里找断点：太靠前的断点会把块切得过碎，
导致「一块里只有半句话」，检索命中后模型也读不出完整意思。
"""

# 顺序即优先级（rfind 取的是文本靠后的位置，不是列表靠前的位置）
BREAK_MARKS = (
    "\n\n", "\n", "。", "！", "？", "；", "…",
    ". ", "! ", "? ", "; ", "，", ", ", "、",
)

_MIN_FILL_RATIO = 0.6      # 断点至少要落在窗口 60% 之后


def split_text(text, size=500, overlap=80):
    """把 text 切成若干块。保证每块长度 ≤ size，且不产生空块。"""
    if size <= 0:
        raise ValueError("size 必须为正整数")
    if not (0 <= overlap < size):
        raise ValueError("必须满足 0 <= overlap < size")

    text = (text or "").strip()
    if not text:
        return []

    chunks = []
    start = 0
    total = len(text)
    floor = int(size * _MIN_FILL_RATIO)

    while start < total:
        end = min(start + size, total)
        if end < total:
            window = text[start:end]
            cut = -1
            for mark in BREAK_MARKS:
                idx = window.rfind(mark)
                if idx >= floor:
                    cut = max(cut, idx + len(mark))
            if cut > 0:
                end = start + cut

        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= total:
            break

        next_start = end - overlap
        if next_start <= start:        # overlap 可能吃满整个窗口，必须保证前进
            next_start = end
        start = next_start

    return chunks
